# -*- coding: utf-8 -*-
"""
binance_backtest15.py
---------------------
[2026-11-26 신설, 사용자 요청] bt14 S0(V3 숏 기준, 327건 +$1,063)에 '정리(청산) 비용'을 추가.
기존 bt3~14는 진입 비용(가격 0.7% = 3배 증거금 대비 -2.1%)만 한 번 빼고, 48h 정리할 때 수수료·슬리피지는 안 뺐음.
여기서는 진입 0.7%는 그대로 두고, 정리 때 가격 기준 EXIT% 를 추가로 뺀다(증거금 대비 = EXIT% × 3배).
  E0  정리 비용 0%     (기존 bt14 S0와 같아야 함 → 검산용)
  E1  정리 0.05%       (바이낸스 선물 시장가 수수료만)
  E2  정리 0.35%       (수수료 + 슬리피지 절반)
  E3  정리 0.7%        (진입과 같은 수준 — 보수적)
  --exit 로 원하는 값 하나를 더 넣을 수 있음(가격 %, 예: --exit 0.5)
강제청산은 기존처럼 증거금 -90%(정리 비용 추가 없음 — 청산되면 거래소가 정리하므로).
나머지(대상·모델·가드·$30/강한숏 $60·3배·48h·31.3% 청산·7일 차단)는 bt14와 같음. 예측은 bt14 캐시(binance_v3_preds_1-300_v2.pkl)를 그대로 씀.
※ 11/26부터 bt3~14 기본 비용(b3.COST)은 진입+정리 4.2% = 여기 E3과 같음.
결과: reports/binance_backtest15_YYYYMMDD.md, reports/binance_backtest15_trades.csv
"""
import os
import sys
import json
import time
import argparse
from datetime import datetime

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import binance_backtest2 as b2  # noqa: E402
import binance_backtest3 as b3  # noqa: E402
import binance_backtest4 as b4  # noqa: E402
import binance_backtest8 as b8  # noqa: E402
import binance_backtest9 as b9  # noqa: E402
import binance_backtest12 as b12  # noqa: E402
import binance_backtest14 as b14  # noqa: E402

log = b2.log
LEV = 3
ENTRY_COST = 0.021  # 진입 비용만(가격 0.7% × 3배) 고정 — 11/26부터 b3.COST는 진입+정리 0.042라 여기선 따로 둠
EXITS = [(0.0, 'E0 정리 비용 0% (기존 = 검산)'),
         (0.05, 'E1 정리 0.05% (수수료만)'),
         (0.35, 'E2 정리 0.35% (수수료+슬리피지 절반)'),
         (0.7, 'E3 정리 0.7% (진입과 같게)')]


def run_side_exit(events_, frames, side, exit_pct):
    """bt14.run_side 와 같고, 강제청산이 아닌 정리에서 exit_pct(가격 %) × 레버리지만큼 더 뺌."""
    trades = []
    hold_until, liq_until = {}, {}
    never = pd.Timestamp('1900-01-01')
    lev, liq_move = LEV, 1.0 / 3 - 0.02
    cost = ENTRY_COST + LEV * exit_pct / 100.0
    for ts, tk, sd, exp4, pup, atr, r168, age in sorted(events_, key=lambda e: (e[0], e[3] if side == 'short' else -e[3], e[1])):
        if ts < hold_until.get(tk, never) or ts < liq_until.get(tk, never):
            continue
        d = frames[tk]
        i = d.index.get_loc(ts)
        px = float(d['close'].iloc[i])
        fut = d.iloc[i + 1:i + 1 + b3.HOLD_H]
        if len(fut) < b3.HOLD_H:
            continue
        amt = b3.USD * (b3.STRONG_MULT if (side == 'short' and exp4 <= b3.STRONG_EXP4) else 1.0)
        mv = (fut['high'] / px - 1) if side == 'short' else (1 - fut['low'] / px)
        adv = float(mv.max())
        liq = adv >= liq_move
        if liq:
            roi = -0.9
            ex = fut.index[(mv >= liq_move).values][0]
            liq_until[tk] = ex + pd.Timedelta(hours=b3.LIQ_BLOCK_H)
        else:
            ret = fut['close'].iloc[-1] / px - 1
            roi = lev * (-ret if side == 'short' else ret) - cost
            ex = fut.index[-1]
        hold_until[tk] = ex
        trades.append({'ts': ts, 'exit_ts': ex, 'ticker': tk, 'side': side, 'amount': amt, 'exp4': round(exp4, 2),
                       'p_up': round(pup, 3), 'atr_pct': round(atr, 2), 'r168': round(r168, 1), 'age_d': int(age),
                       'max_adverse_pct': round(adv * 100, 1), 'liq': liq, 'roi': round(roi, 4), 'pnl': round(roi * amt, 2)})
    return pd.DataFrame(trades)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--months', type=int, default=12)
    ap.add_argument('--exit', type=float, default=None, help='추가로 볼 정리 비용(가격 %%)')
    a = ap.parse_args()
    t0 = time.time()
    with open(os.path.join(b2.BIN_DIR, '_symbols.json'), encoding='utf-8') as f:
        sym = json.load(f)
    onboard = {m['coin']: pd.Timestamp(int(m['onboard']), unit='ms') + pd.Timedelta(hours=9)
               for m in sym.values() if m.get('onboard')}
    sub = b3.fetch_subtypes()
    meme_set = {c for c, v in sub.items() if any('meme' in str(x).lower() for x in v)}
    bf = b2.load_dir(b2.BIN_DIR, 'binance_*_1h.csv')
    end = max(d.index[-1] for d in bf.values())
    test_start = (end.normalize() - pd.DateOffset(months=a.months)).replace(day=1)
    split = test_start + pd.DateOffset(months=a.months // 2)
    log(f"바이낸스 {len(bf)}종목 · 시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d}")
    M = b4.daily_metrics(bf)
    P = b14.get_preds(bf, onboard, meme_set, M, test_start, end)
    uni = b8.band_universe(M, onboard, meme_set, *b14.FULL)
    P = P[b3.membership_mask(P['ts'], P['ticker'].values, uni)]
    ev = b14.events(P, 'short')
    log(f"숏 판정 {len(ev)}개 · 진입 비용 {ENTRY_COST * 100:.1f}%(증거금)")

    exits = list(EXITS)
    if a.exit is not None and all(abs(a.exit - e) > 1e-9 for e, _ in exits):
        exits.append((a.exit, f"EX 정리 {a.exit:g}% (--exit)"))
    runs = []
    for ep, name in exits:
        tr = run_side_exit(ev, bf, 'short', ep)
        s = b2.stats(tr, split)
        hs = b12.half_stats(tr, split)
        dd = b9.max_dd(tr)
        runs.append((name, ep, s, hs, dd, tr))
        log(f"{name}: {s['n']}건 ${s['pnl']:+,.0f} (앞 {s['h1']:+,.0f} / 뒤 {s['h2']:+,.0f}) 건당 {s['roi']:+.1f}% 낙폭 ${dd:+,.0f}")

    os.makedirs(b2.REPORT_DIR, exist_ok=True)
    pd.concat([r[5].assign(run=r[0]) for r in runs if not r[5].empty]).to_csv(
        os.path.join(b2.REPORT_DIR, 'binance_backtest15_trades.csv'), index=False, encoding='utf-8-sig')
    L = [f"# 바이낸스 V3 숏 — 정리 수수료·슬리피지 추가 ({datetime.now():%Y-%m-%d %H:%M})", "",
         f"시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d} (앞/뒤 나눔 {split:%Y-%m-%d}) · bt14 S0와 같은 판정·가드 · $30(강한숏 $60)·3배·48h·청산 31.3%(7일 차단)",
         f"진입 비용 가격 0.7%(증거금 -{ENTRY_COST * 100:.1f}%) 고정 + 정리 비용(가격 %) × 3배 추가. 강제청산은 -90% 그대로", "",
         "| 시험 | 정리 비용(가격) | 총비용(증거금) | 진입 | 승률 | 건당 ROI | 청산 | $손익(1년) | 앞/뒤 6개월 | 최악월 | 마이너스 달 | 최대 낙폭 | 통과 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, ep, s, hs, dd, tr in runs:
        if not s['n']:
            continue
        ok = s['h1'] > 0 and s['h2'] > 0 and s['liqp'] <= 6
        tot = ENTRY_COST * 100 + LEV * ep
        L.append(f"| {name} | {ep:g}% | -{tot:.2f}% | {s['n']} | {s['win']:.0f}% | {s['roi']:+.1f}% | {s['liq']} ({s['liqp']:.1f}%) | ${s['pnl']:+,.0f} | "
                 f"${s['h1']:+,.0f} / ${s['h2']:+,.0f} | ${s['worst']:+,.0f} | {s['mneg']}/{s['mon']} | ${dd:+,.0f} | {'✅' if ok else '❌'} |")
    L += ["", "E0이 327건 +$1,063(bt14 S0)과 같으면 검산 통과.", "", f"소요 {time.time() - t0:.0f}초"]
    out = os.path.join(b2.REPORT_DIR, f"binance_backtest15_{datetime.now():%Y%m%d}.md")
    with open(out, 'w', encoding='utf-8') as f:
        f.write("\n".join(L) + "\n")
    log(f"결과 저장: {out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
