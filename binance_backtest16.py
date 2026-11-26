# -*- coding: utf-8 -*-
"""
binance_backtest16.py
---------------------
[2026-11-26 신설, 사용자 요청] 바이낸스 V3 숏 판정 기준(4h 예상수익 exp4 상한)을 -0.5 → -0.4 / -0.3 / -0.2 로 풀었을 때 비교.
bt15 E3(진입 0.7% + 정리 0.7% = 증거금 -4.2%, 327건 +$837)이 기준. 나머지(대상 1-300위·가드 상장 730일↑·ATR<2.5·7일<+50%·
시장 반등 대기·숏 손절 차단·$30(강한숏 exp4≤-1.0 $60)·3배·48h·청산 31.3%(7일 차단))는 bt14/15와 같음. RSI 규칙 없음(V3 탭은 원래 안 씀).
--cuts 로 다른 값 지정 가능(예: --cuts -0.5 -0.35 -0.25).
결과: reports/binance_backtest16_YYYYMMDD.md, reports/binance_backtest16_trades.csv
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
import binance_backtest15 as b15  # noqa: E402

log = b2.log
EXIT_PCT = 0.7
CUTS = [-0.5, -0.4, -0.3, -0.2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--months', type=int, default=12)
    ap.add_argument('--cuts', type=float, nargs='+', default=CUTS)
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
    orig = b3.EXP4_MAX
    runs = []
    for c in a.cuts:
        b3.EXP4_MAX = c
        ev = b14.events(P, 'short')
        tr = b15.run_side_exit(ev, bf, 'short', EXIT_PCT)
        s = b2.stats(tr, split)
        hs = b12.half_stats(tr, split)
        dd = b9.max_dd(tr)
        runs.append((c, len(ev), s, hs, dd, tr))
        log(f"cut {c:+.2f}: 판정 {len(ev)} · {s['n']}건 ${s['pnl']:+,.0f} (앞 {s['h1']:+,.0f} / 뒤 {s['h2']:+,.0f}) 건당 {s['roi']:+.1f}% 청산 {s['liq']} 낙폭 ${dd:+,.0f}")
    b3.EXP4_MAX = orig
    os.makedirs(b2.REPORT_DIR, exist_ok=True)
    pd.concat([r[5].assign(cut=r[0]) for r in runs if not r[5].empty]).to_csv(
        os.path.join(b2.REPORT_DIR, 'binance_backtest16_trades.csv'), index=False, encoding='utf-8-sig')
    L = [f"# 바이낸스 V3 숏 — 판정 기준(exp4 상한) 완화 비교 ({datetime.now():%Y-%m-%d %H:%M})", "",
         f"시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d} (앞/뒤 나눔 {split:%Y-%m-%d}) · bt14/15와 같은 대상·가드 · $30(강한숏 $60)·3배·48h·청산 31.3%(7일 차단) · 비용 -4.2%(진입+정리 0.7%씩)", "",
         "| exp4 상한 | 진입 | 월평균 | 승률 | 건당 ROI | 청산 | $손익(1년) | 앞/뒤 6개월 | 최악월 | 마이너스 달 | 최대 낙폭 | 통과 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c, nev, s, hs, dd, tr in runs:
        if not s['n']:
            continue
        ok = s['h1'] > 0 and s['h2'] > 0 and s['liqp'] <= 6
        L.append(f"| {c:+.2f}{' (현재)' if abs(c - orig) < 1e-9 else ''} | {s['n']} | {s['n'] / a.months:.0f} | {s['win']:.0f}% | {s['roi']:+.1f}% | {s['liq']} ({s['liqp']:.1f}%) | ${s['pnl']:+,.0f} | "
                 f"${s['h1']:+,.0f} / ${s['h2']:+,.0f} | ${s['worst']:+,.0f} | {s['mneg']}/{s['mon']} | ${dd:+,.0f} | {'✅' if ok else '❌'} |")
    L += ["", "통과 = 앞·뒤 6개월 모두 + 이고 청산 6% 이하. 현재(-0.5)가 327건 +$837(bt15 E3)이면 검산 통과.", "", f"소요 {time.time() - t0:.0f}초"]
    out = os.path.join(b2.REPORT_DIR, f"binance_backtest16_{datetime.now():%Y%m%d}.md")
    with open(out, 'w', encoding='utf-8') as f:
        f.write("\n".join(L) + "\n")
    log(f"결과 저장: {out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
