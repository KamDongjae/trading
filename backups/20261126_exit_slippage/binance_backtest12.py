# -*- coding: utf-8 -*-
"""
binance_backtest12.py
---------------------
[2026-11-25 신설, 사용자 요청] 개편 2단계 후속: V3 모델(코인+BTC 특성, REF 기준, 바이낸스 학습)의 강제청산 줄이기 + 판정 기준 재조정.
V3 예측은 binance_backtest11 과 같은 방식으로 다시 만들고 reports/binance_v3_preds.pkl 로 저장(다음 시험 재사용).
조합(전부 곱):
  판정 기준 (exp4 <, p_up <): (-0.5,0.45) (-0.7,0.45) (-1.0,0.45) (-0.5,0.40) (-0.7,0.40)
  진입 ATR 상한: 3.0 / 2.5 / 2.0   (7일<50 재확인은 그대로)
  레버리지: 3배 / 2배 / ATR≥2 이면 2배(아니면 3배)   — 강제청산 역행 = 1/배수 − 2% (3배 31.3%, 2배 48%), 비용 = 2.1% × 배수/3
  상장 기간: 180일↑ / 730일↑
과최적화 확인: 앞 6개월 성적(청산 ≤6% · 손익>0 중 손익/낙폭 최고)만으로 고른 조합의 뒤 6개월 성적을 따로 표시.
공통: 대상 = 필터 후 24h 거래대금 81-200위 · 판정 v2 숏(시장 4h≥+1% 대기 · 숏 손절 차단) · $30 · 48h · 강한숏(exp4≤-1) $60 · 청산 코인 7일 차단
통과: 앞뒤 6개월 + · 청산 ≤6%
결과: reports/binance_backtest12_YYYYMMDD.md, reports/binance_backtest12_grid.csv
"""
import os
import sys
import json
import time
import argparse
import itertools
from datetime import datetime

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import binance_backtest2 as b2  # noqa: E402
import binance_backtest3 as b3  # noqa: E402
import binance_backtest4 as b4  # noqa: E402
import binance_backtest8 as b8  # noqa: E402
import binance_backtest9 as b9  # noqa: E402
import binance_backtest11 as b11  # noqa: E402

log = b2.log
PRED_PKL = os.path.join(b2.REPORT_DIR, 'binance_v3_preds.pkl')
THRESH = [(-0.5, 0.45), (-0.7, 0.45), (-1.0, 0.45), (-0.5, 0.40), (-0.7, 0.40)]
ATR_CAPS = [3.0, 2.5, 2.0]
LEVS = ['3배', '2배', 'ATR≥2만 2배']
AGES = [180, 730]


def lev_of(mode, atr):
    if mode == '3배':
        return 3
    if mode == '2배':
        return 2
    return 2 if atr >= 2.0 else 3


def run_lev(events, frames, atr_max, lev_mode, r168_max=50.0):
    """b9.run_limited(제한 없음)과 같은 규칙 + 거래별 레버리지."""
    trades = []
    hold_until, liq_until = {}, {}
    never = pd.Timestamp('1900-01-01')
    for ts, tk, side, exp4, pup, atr, r168, age in sorted(events, key=lambda e: (e[0], e[3], e[1])):
        if not (atr < atr_max) or not (r168 < r168_max):
            continue
        if ts < hold_until.get(tk, never) or ts < liq_until.get(tk, never):
            continue
        d = frames[tk]
        i = d.index.get_loc(ts)
        px = float(d['close'].iloc[i])
        fut = d.iloc[i + 1:i + 1 + b3.HOLD_H]
        if len(fut) < b3.HOLD_H:
            continue
        lev = lev_of(lev_mode, atr)
        liq_move = 1.0 / lev - 0.02
        cost = b3.COST * lev / 3.0
        amt = b3.USD * (b3.STRONG_MULT if exp4 <= b3.STRONG_EXP4 else 1.0)
        up = fut['high'] / px - 1
        adv = float(up.max())
        liq = adv >= liq_move
        if liq:
            roi = -0.9
            ex = fut.index[(up >= liq_move).values][0]
            liq_until[tk] = ex + pd.Timedelta(hours=b3.LIQ_BLOCK_H)
        else:
            roi = lev * -(fut['close'].iloc[-1] / px - 1) - cost
            ex = fut.index[-1]
        hold_until[tk] = ex
        trades.append({'ts': ts, 'exit_ts': ex, 'ticker': tk, 'amount': amt, 'lev': lev, 'exp4': round(exp4, 2),
                       'p_up': round(pup, 3), 'atr_pct': round(atr, 2), 'r168': round(r168, 1), 'age_d': int(age),
                       'max_adverse_pct': round(adv * 100, 1), 'liq': liq, 'roi': round(roi, 4), 'pnl': round(roi * amt, 2)})
    return pd.DataFrame(trades)


def half_stats(t, split):
    out = {}
    for nm, g in (('h1', t[t['ts'] < split] if not t.empty else t), ('h2', t[t['ts'] >= split] if not t.empty else t)):
        if g.empty:
            out[nm] = dict(n=0, pnl=0.0, liqp=np.nan, dd=0.0)
        else:
            out[nm] = dict(n=len(g), pnl=float(g['pnl'].sum()), liqp=float(g['liq'].mean() * 100), dd=b9.max_dd(g))
    return out


def get_preds(bf, onboard, meme_set, M, test_start, end):
    if os.path.exists(PRED_PKL):
        P = pd.read_pickle(PRED_PKL)
        if P['ts'].min() >= test_start - pd.Timedelta(days=1) and P['ts'].max() >= end - pd.Timedelta(days=2):
            log(f"V3 예측 불러옴: {PRED_PKL} ({len(P):,}행)")
            return P
    ref = b8.band_universe(M, onboard, meme_set, 1, b11.REF_N)
    uni = b8.band_universe(M, onboard, meme_set, *b11.UNI_BAND)
    X = b11.build_ref_table(bf, onboard, ref, uni, test_start)
    log(f"REF 테이블 {len(X):,}행 · {X['ticker'].nunique()}종목")
    test_mask = (X['ts'] >= test_start).values & b3.membership_mask(X['ts'], X['ticker'].values, uni)
    edges3 = pd.date_range(test_start, end + pd.DateOffset(months=3), freq='3MS')
    pr3 = b11.walk_forward_ref(X, test_mask, edges3, b11.FEATS_NOXS, 'V3 시장특성 뺌')
    cols = ['ts', 'ticker', 'p', 'atr_pct', 'r168', 'age_d', 'mkt_r4']
    P = X.loc[pr3.index, cols].join(pr3)
    P.to_pickle(PRED_PKL)
    log(f"V3 예측 저장: {PRED_PKL} ({len(P):,}행)")
    return P


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--months', type=int, default=12)
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
    P = get_preds(bf, onboard, meme_set, M, test_start, end)

    orig = (b3.EXP4_MAX, b3.PUP_MAX)
    ev_cache = {}
    for th in THRESH:
        b3.EXP4_MAX, b3.PUP_MAX = th
        ev_cache[th] = b3.short_events(P)
        log(f"판정 기준 exp4<{th[0]} · p_up<{th[1]}: 판정 {len(ev_cache[th])}번")
    b3.EXP4_MAX, b3.PUP_MAX = orig

    rows, trades_by = [], {}
    for th, cap, lv, ag in itertools.product(THRESH, ATR_CAPS, LEVS, AGES):
        sev = [e for e in ev_cache[th] if e[7] >= ag]
        tr = run_lev(sev, bf, cap, lv)
        s = b2.stats(tr, split)
        hs = half_stats(tr, split)
        dd = b9.max_dd(tr)
        key = f"exp4<{th[0]} · p_up<{th[1]} · ATR<{cap} · {lv} · 상장{ag}일↑"
        trades_by[key] = tr
        rows.append(dict(조합=key, exp4=th[0], p_up=th[1], atr=cap, lev=lv, age=ag, n=s['n'], win=s['win'], roi=s['roi'],
                         liq=s['liq'], liqp=s['liqp'], pnl=s['pnl'], h1=s['h1'], h2=s['h2'], worst=s['worst'], mneg=s['mneg'],
                         mon=s['mon'], dd=dd, ratio=s['pnl'] / max(1.0, -dd),
                         h1_liqp=hs['h1']['liqp'], h2_liqp=hs['h2']['liqp'], h1_dd=hs['h1']['dd'], h2_dd=hs['h2']['dd'],
                         ok=bool(s['n'] and s['h1'] > 0 and s['h2'] > 0 and s['liqp'] <= 6)))
    G = pd.DataFrame(rows)
    os.makedirs(b2.REPORT_DIR, exist_ok=True)
    G.to_csv(os.path.join(b2.REPORT_DIR, 'binance_backtest12_grid.csv'), index=False, encoding='utf-8-sig')

    def fmt(r):
        return (f"| {r['조합']} | {r['n']} | {r['win']:.0f}% | {r['roi']:+.1f}% | {r['liq']} ({r['liqp']:.1f}%) | ${r['pnl']:+,.0f} | "
                f"${r['h1']:+,.0f} / ${r['h2']:+,.0f} | {r['h1_liqp']:.1f}% / {r['h2_liqp']:.1f}% | ${r['worst']:+,.0f} | {r['mneg']}/{r['mon']} | "
                f"${r['dd']:+,.0f} | {r['ratio']:.1f} | {'✅' if r['ok'] else '❌'} |")
    hdr = ["| 조합 | 진입 | 승률 | 건당 ROI | 청산 | $손익(1년) | 앞/뒤 6개월 | 청산율 앞/뒤 | 최악월 | 마이너스 달 | 최대 낙폭 | 손익/낙폭 | 통과 |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    L = [f"# V3 모델 강제청산 줄이기 + 판정 기준 재조정 ({datetime.now():%Y-%m-%d %H:%M})", "",
         f"시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d} (앞/뒤 나눔 {split:%Y-%m-%d}) · 대상 필터 후 거래대금 81-200위 · V3 = 코인+BTC 특성·바이낸스 REF 학습·3개월마다 재학습",
         "$30(강한숏 $60) · 48h · 강제청산 역행 = 1/배수−2% (3배 31.3% / 2배 48%) · 비용 2.1%×배수/3 · 청산 코인 7일 차단 · 통과 = 앞뒤 6개월 + · 청산 ≤6%", "",
         "## 판정 기준별 판정 횟수", "", "| 기준 | 판정 횟수 |", "|---|---|"]
    for th in THRESH:
        L.append(f"| exp4<{th[0]} · p_up<{th[1]} | {len(ev_cache[th])} |")
    okg = G[G['ok']].sort_values('ratio', ascending=False)
    L += ["", f"## 통과 조합 {len(okg)}개 / 전체 {len(G)}개 (손익/낙폭 순, 상위 25)", ""] + hdr
    L += [fmt(r) for _, r in okg.head(25).iterrows()]
    base = G[(G['exp4'] == -0.5) & (G['p_up'] == 0.45) & (G['atr'] == 3.0) & (G['lev'] == '3배') & (G['age'] == 180)]
    L += ["", "## 기준(bt11 V3와 같은 설정)", ""] + hdr + [fmt(r) for _, r in base.iterrows()]
    # 과최적화 확인: 앞 6개월만으로 선택
    c1 = G[(G['h1'] > 0) & (G['h1_liqp'] <= 6)].copy()
    c1['r1'] = c1['h1'] / c1['h1_dd'].abs().clip(lower=1.0)
    L += ["", "## 과최적화 확인: 앞 6개월 성적만으로 고른 상위 5개 → 뒤 6개월 성적", "",
          "| 조합 | 앞 6개월 손익 | 앞 청산율 | 앞 손익/낙폭 | 뒤 6개월 손익 | 뒤 청산율 | 뒤 최대 낙폭 |", "|---|---|---|---|---|---|---|"]
    for _, r in c1.sort_values('r1', ascending=False).head(5).iterrows():
        L.append(f"| {r['조합']} | ${r['h1']:+,.0f} | {r['h1_liqp']:.1f}% | {r['r1']:.1f} | ${r['h2']:+,.0f} | {r['h2_liqp']:.1f}% | ${r['h2_dd']:+,.0f} |")
    # 요소별 평균 효과
    L += ["", "## 요소별 평균(전체 조합 평균)", ""]
    for col, nm in (('atr', 'ATR 상한'), ('lev', '레버리지'), ('age', '상장 기간'), ('exp4', 'exp4 기준'), ('p_up', 'p_up 기준')):
        q = G.groupby(col).agg(진입=('n', 'mean'), 청산율=('liqp', 'mean'), 손익=('pnl', 'mean'), 손익낙폭=('ratio', 'mean'), 통과=('ok', 'sum'))
        L += [f"**{nm}**", "", "| 값 | 평균 진입 | 평균 청산율 | 평균 손익 | 평균 손익/낙폭 | 통과 수 |", "|---|---|---|---|---|---|"]
        for k, r in q.iterrows():
            L.append(f"| {k} | {r['진입']:.0f} | {r['청산율']:.1f}% | ${r['손익']:+,.0f} | {r['손익낙폭']:.1f} | {int(r['통과'])} |")
        L.append("")
    if len(okg):
        k = okg.iloc[0]['조합']
        tb = trades_by[k]
        L += [f"## 통과 1위 월별: {k}", "", "| 월 | 진입 | 청산 | $손익 |", "|---|---|---|---|"]
        for m, g in tb.groupby(tb['ts'].dt.to_period('M')):
            L.append(f"| {m} | {len(g)} | {int(g['liq'].sum())} | ${g['pnl'].sum():+,.1f} |")
        liqs = tb[tb['liq']]
        if len(liqs):
            L += ["", "청산: " + ", ".join(f"{r.ticker} {r.ts:%m/%d}" for r in liqs.itertuples())]
    L += ["", f"소요 {time.time() - t0:.0f}초"]
    out = os.path.join(b2.REPORT_DIR, f"binance_backtest12_{datetime.now():%Y%m%d}.md")
    with open(out, 'w', encoding='utf-8') as f:
        f.write("\n".join(L) + "\n")
    log(f"결과 저장: {out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
