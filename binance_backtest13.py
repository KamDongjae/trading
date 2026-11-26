# -*- coding: utf-8 -*-
"""
binance_backtest13.py
---------------------
[2026-11-25 신설, 사용자 요청] V3 모델(코인+BTC 특성, REF 기준, 바이낸스 학습)로 거래대금 순위 구간 다시 고르기(공정 비교).
기존 81-200위는 V0(원화 모델) 결과로 고른 구간이라, V3 + 상장 2년↑ 기준으로 1-300위 전체를 다시 비교.
예측: 대상 = 필터 후 순위 1-300위 전부(REF = 필터 후 상위 200, 학습은 REF 행만 → 81-200위 예측값은 bt11·12와 같음)
      예측 저장은 저장소 밖: ..\\trading_cache\\binance_v3_preds_1-300.pkl (GitHub 용량 문제 방지)
구간: 1-40 / 41-80 / 81-120 / 121-160 / 161-200 / 201-300 / 81-200 / 41-200 / 121-300 / 1-300
설정: 추천(exp4<-0.5 · p_up<0.45 · ATR<2.5 · 3배 · 상장 730일↑) / 기준(ATR<3 · 상장 180일↑, bt11 V3와 같음)
공통: 판정 v2 숏(시장 4h≥+1% 대기 · 숏 손절 차단) · $30(강한숏 $60) · 48h · 비용 4.2% · 역행 31.3% 강제청산(7일 차단) · 7일<50 재확인
통과: 앞뒤 6개월 + · 청산 ≤6%
결과: reports/binance_backtest13_YYYYMMDD.md, reports/binance_backtest13_trades.csv
"""
import os
import sys
import json
import time
import argparse
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
import binance_backtest12 as b12  # noqa: E402

log = b2.log
CACHE_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), 'trading_cache')
PRED_PKL = os.path.join(CACHE_DIR, 'binance_v3_preds_1-300.pkl')
FULL = (1, 300)
BANDS = [(1, 40), (41, 80), (81, 120), (121, 160), (161, 200), (201, 300), (81, 200), (41, 200), (121, 300), (1, 300)]
SETTINGS = {'추천(ATR<2.5·상장730일↑)': (2.5, 730), '기준(ATR<3·상장180일↑)': (3.0, 180)}


def get_preds(bf, onboard, meme_set, M, test_start, end):
    if os.path.exists(PRED_PKL):
        P = pd.read_pickle(PRED_PKL)
        if P['ts'].min() >= test_start - pd.Timedelta(days=1) and P['ts'].max() >= end - pd.Timedelta(days=2):
            log(f"V3 예측 불러옴: {PRED_PKL} ({len(P):,}행)")
            return P
    ref = b8.band_universe(M, onboard, meme_set, 1, b11.REF_N)
    uni = b8.band_universe(M, onboard, meme_set, *FULL)
    X = b11.build_ref_table(bf, onboard, ref, uni, test_start)
    log(f"REF 테이블 {len(X):,}행 · {X['ticker'].nunique()}종목")
    test_mask = (X['ts'] >= test_start).values & b3.membership_mask(X['ts'], X['ticker'].values, uni)
    edges3 = pd.date_range(test_start, end + pd.DateOffset(months=3), freq='3MS')
    pr3 = b11.walk_forward_ref(X, test_mask, edges3, b11.FEATS_NOXS, 'V3 시장특성 뺌')
    cols = ['ts', 'ticker', 'p', 'atr_pct', 'r168', 'age_d', 'mkt_r4']
    P = X.loc[pr3.index, cols].join(pr3)
    os.makedirs(CACHE_DIR, exist_ok=True)
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
    tdays = [d for d in M[0].index if d >= test_start]

    res, allt, stat_lines = [], [], []
    for lo, hi in BANDS:
        uni = b8.band_universe(M, onboard, meme_set, lo, hi)
        bn = f'{lo}-{hi}위'
        stat_lines.append(f"| {bn} | {np.mean([len(uni[d]) for d in tdays]):.1f} | {len(set().union(*[uni[d] for d in tdays]))} |")
        mk = b3.membership_mask(P['ts'], P['ticker'].values, uni)
        ev = b3.short_events(P[mk])
        for sn, (cap, ag) in SETTINGS.items():
            sev = [e for e in ev if e[7] >= ag]
            tr = b12.run_lev(sev, bf, cap, '3배')
            s = b2.stats(tr, split)
            hs = b12.half_stats(tr, split)
            dd = b9.max_dd(tr)
            res.append((bn, sn, s, hs, dd, tr))
            if not tr.empty:
                allt.append(tr.assign(band=bn, setting=sn))
            log(f"{bn} · {sn}: {s['n']}건 ${s['pnl']:+,.0f} (앞 {s['h1']:+,.0f} / 뒤 {s['h2']:+,.0f}) · 청산 {s['liqp'] if s['n'] else 0:.1f}% · 낙폭 ${dd:+,.0f}")

    os.makedirs(b2.REPORT_DIR, exist_ok=True)
    if allt:
        pd.concat(allt).to_csv(os.path.join(b2.REPORT_DIR, 'binance_backtest13_trades.csv'), index=False, encoding='utf-8-sig')
    hdr = ["| 구간 | 진입 | 승률 | 건당 ROI | 청산 | $손익(1년) | 앞/뒤 6개월 | 청산율 앞/뒤 | 최악월 | 마이너스 달 | 최대 낙폭 | 손익/낙폭 | 통과 |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    L = [f"# V3 모델로 거래대금 순위 구간 다시 고르기 ({datetime.now():%Y-%m-%d %H:%M})", "",
         f"시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d} (앞/뒤 나눔 {split:%Y-%m-%d}) · 필터(밈·스테이블·배수 제외, 상장 1년↑, ±30% 이력 제외, ATR<3, 7일<50) 후 24h 거래대금 순위 구간 · V3 = 코인+BTC 특성·바이낸스 REF(상위 200) 학습·3개월마다 재학습",
         "판정 exp4<-0.5 · p_up<0.45 · 3배 · $30(강한숏 $60) · 48h · 비용 4.2% · 역행 31.3% 강제청산(7일 차단) · 통과 = 앞뒤 6개월 + · 청산 ≤6%", "",
         "| 구간 | 하루 평균 코인 | 1년간 거쳐간 코인 |", "|---|---|---|"] + stat_lines
    for sn in SETTINGS:
        L += ["", f"## {sn}", ""] + hdr
        for bn, s2, s, hs, dd, tr in res:
            if s2 != sn:
                continue
            if s['n'] == 0:
                L.append(f"| {bn} | 0 | – | – | – | – | – | – | – | – | – | – | – |")
                continue
            ok = s['h1'] > 0 and s['h2'] > 0 and s['liqp'] <= 6
            L.append(f"| {bn} | {s['n']} | {s['win']:.0f}% | {s['roi']:+.1f}% | {s['liq']} ({s['liqp']:.1f}%) | ${s['pnl']:+,.0f} | "
                     f"${s['h1']:+,.0f} / ${s['h2']:+,.0f} | {hs['h1']['liqp']:.1f}% / {hs['h2']['liqp']:.1f}% | ${s['worst']:+,.0f} | "
                     f"{s['mneg']}/{s['mon']} | ${dd:+,.0f} | {s['pnl'] / max(1.0, -dd):.1f} | {'✅' if ok else '❌'} |")
    rec = [r for r in res if r[1].startswith('추천') and r[2]['n']]
    okr = [r for r in rec if r[2]['h1'] > 0 and r[2]['h2'] > 0 and r[2]['liqp'] <= 6]
    if okr:
        bn, sn, s, hs, dd, tb = max(okr, key=lambda r: r[2]['pnl'] / max(1.0, -r[4]))
        g = tb.groupby('ticker').agg(n=('pnl', 'size'), liq=('liq', 'sum'), pnl=('pnl', 'sum')).sort_values('pnl')
        L += ["", f"## 추천 설정 통과 중 손익/낙폭 최고: {bn}", "", "| 월 | 진입 | 청산 | $손익 |", "|---|---|---|---|"]
        for m, gg in tb.groupby(tb['ts'].dt.to_period('M')):
            L.append(f"| {m} | {len(gg)} | {int(gg['liq'].sum())} | ${gg['pnl'].sum():+,.1f} |")
        L += ["", "손실 상위 코인: " + ", ".join(f"{t} {int(r.n)}건 ${r.pnl:+.0f}" for t, r in g.head(8).iterrows()),
              "", "청산: " + (", ".join(f"{r.ticker} {r.ts:%m/%d}" for r in tb[tb['liq']].itertuples()) or "없음")]
    L += ["", f"소요 {time.time() - t0:.0f}초"]
    out = os.path.join(b2.REPORT_DIR, f"binance_backtest13_{datetime.now():%Y%m%d}.md")
    with open(out, 'w', encoding='utf-8') as f:
        f.write("\n".join(L) + "\n")
    log(f"결과 저장: {out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
