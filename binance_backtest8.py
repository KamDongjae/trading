# -*- coding: utf-8 -*-
"""
binance_backtest8.py
--------------------
[2026-11-25 신설, 사용자 요청] 거래대금 순위 구간별 성적: '최상위보다 조금 아래'가 나은지 데이터 확인.
매일 09시: 밈·스테이블·배수 제외, 상장 1년↑, 30일 하루 ±30% 이력 제외, ATR<3, 7일<50 (F40과 같은 필터)
→ 24h 거래대금 순위 구간 1-40 / 41-80 / 81-120 / 121-160 / 161-200 / 201-300 각각 대상
각 구간: 전체 / 처음 보는 코인만(D 제외) · 모델 M_krw
공통: 자동진입 상장 180일↑, 판정 v2 숏, $30·3배·48h, 강한숏 $60, 비용 -4.2%, 역행 31.3% 청산, 청산 코인 7일 차단
통과: 앞뒤 6개월 + · 청산 ≤6%
결과: reports/binance_backtest8_YYYYMMDD.md, reports/binance_backtest8_trades.csv
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

log = b2.log
MIN_AGE_AUTO_D = 180
BANDS = [(1, 40), (41, 80), (81, 120), (121, 160), (161, 200), (201, 300)]


def band_universe(M, onboard, meme_set, lo, hi, atr_max=3.0, r7_max=50.0):
    V24, A, R7, DV, DC = M
    days = V24.index
    shock = (DC.pct_change().abs().mul(100).rolling(30, min_periods=1).max().shift(1) >= b3.SHOCK_PCT).reindex(days).fillna(False)
    age = pd.DataFrame({tk: (days - onboard.get(tk, days[0])).days for tk in V24.columns}, index=days)
    elig = (age >= b4.AGE_D) & ~shock.reindex(columns=V24.columns).fillna(False).astype(bool) & V24.notna() \
        & (A < atr_max) & (R7 < r7_max)
    for tk in V24.columns:
        if tk in b3.STABLES or b3.is_multiplier(tk) or tk in meme_set:
            elig[tk] = False
    rank = V24.where(elig).rank(axis=1, ascending=False, method='first')
    inb = (rank >= lo) & (rank <= hi)
    return {day: frozenset(inb.columns[inb.loc[day].values]) for day in days}


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
    kf = b2.load_dir(b2.KRW_DIR, '*_1h.csv')
    K = b2.krw_table(kf)
    bf = b2.load_dir(b2.BIN_DIR, 'binance_*_1h.csv')
    D = set(kf) & set(bf)
    end = max(d.index[-1] for d in bf.values())
    test_start = (end.normalize() - pd.DateOffset(months=a.months)).replace(day=1)
    split = test_start + pd.DateOffset(months=a.months // 2)
    log(f"바이낸스 {len(bf)}종목 · D {len(D)}개 · 시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d}")
    M = b4.daily_metrics(bf)
    days = M[0].index
    unis, stat_lines = {}, []
    for lo, hi in BANDS:
        u = band_universe(M, onboard, meme_set, lo, hi)
        unis[f'{lo}-{hi}위'] = u
        unis[f'{lo}-{hi}위 · D 제외'] = {d: s - D for d, s in u.items()}
    for k, u in unis.items():
        sz = [len(v) for d, v in u.items() if d >= test_start]
        nm = set().union(*[v for d, v in u.items() if d >= test_start])
        dshare = np.mean([len(v & D) / len(v) if v else 0 for d, v in u.items() if d >= test_start])
        stat_lines.append(f"| {k} | {np.mean(sz):.1f} | {len(nm)} | {dshare * 100:.0f}% |")
        log(f"[{k}] 하루 평균 {np.mean(sz):.1f}개 · 거쳐간 {len(nm)}개 · D 비중 {dshare * 100:.0f}%")

    pool = {}
    for u in unis.values():
        for day, s in u.items():
            if day >= test_start - pd.Timedelta(days=3):
                for t in s:
                    pool.setdefault(t, set()).add(day)
    if 'BTC' in bf:
        pool.setdefault('BTC', set()).update(days)

    def pool_mask(tk, idx):
        ds = pool.get(tk)
        if not ds:
            return np.zeros(len(idx), dtype=bool)
        dd = (idx - pd.Timedelta(hours=9)).normalize()
        return np.isin(dd.values, np.array(sorted(ds), dtype='datetime64[ns]'))

    B = b3.build_table(bf, onboard, pool_mask, test_start)
    log(f"풀 테이블 {len(B):,}행 · {B['ticker'].nunique()}종목")
    T = B[B['ts'] >= test_start].copy()
    edges = pd.date_range(test_start, end + pd.DateOffset(months=1), freq='MS')
    pr = b2.walk_forward(T, K, edges, 'M_krw')

    rows, allt = [], []
    for un, uni in unis.items():
        mask = b3.membership_mask(T['ts'], T['ticker'].values, uni)
        Pq = T[mask].join(pr, how='inner')
        if Pq.empty:
            rows.append((un, pd.DataFrame()))
            continue
        Pq = Pq.assign(mkt_r4=Pq.groupby('ts')['r4'].transform('mean'), u_r24=Pq.groupby('ts')['r24'].transform('mean'))
        sev = [e for e in b3.short_events(Pq) if e[7] >= MIN_AGE_AUTO_D]
        tr = b3.run_entries(sev, bf, 3.0, 50.0)
        if not tr.empty:
            tr = tr.assign(in_D=tr['ticker'].isin(D))
            allt.append(tr.assign(universe=un))
        rows.append((un, tr))
        log(f"{un} 끝: {len(tr)}건")

    os.makedirs(b2.REPORT_DIR, exist_ok=True)
    if allt:
        pd.concat(allt).to_csv(os.path.join(b2.REPORT_DIR, 'binance_backtest8_trades.csv'), index=False, encoding='utf-8-sig')
    L = [f"# 거래대금 순위 구간별 성적 ({datetime.now():%Y-%m-%d %H:%M})", "",
         f"시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d} (앞/뒤 나눔 {split:%Y-%m-%d}) · 매일 09시 밈·스테이블·배수 제외, 상장 1년↑, 30일 하루 ±30% 이력 제외, ATR<3, 7일<50 → 24h 거래대금 순위 구간 · 모델 M_krw · 진입 때 ATR·7일 재확인",
         "숏 판정 v2 · 자동진입 상장 180일↑ · $30·3배·48h · 강한숏 $60 · 비용 -4.2% · 역행 31.3% 청산 · 청산 코인 7일 차단 · 통과 = 앞뒤 6개월 + · 청산 ≤6%", "",
         "| 대상 | 하루 평균 코인 | 1년간 거쳐간 코인 | D 비중 |", "|---|---|---|---|"] + stat_lines
    L += ["", "| 대상 | 진입 | 승률 | 건당 ROI | 청산 | $손익(1년) | 앞/뒤 6개월 | 최악월 | 마이너스 달 | 통과 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    res = [(un, b2.stats(t, split), t) for un, t in rows]
    for un, s, t in res:
        L.append(b2.row(un, s))
    ok = [x for x in res if 'D 제외' in x[0] and not x[2].empty]
    if ok:
        bu, bs, bt = max(ok, key=lambda x: x[1]['pnl'])
        g = bt.groupby('ticker').agg(n=('pnl', 'size'), liq=('liq', 'sum'), pnl=('pnl', 'sum')).sort_values('pnl')
        L += ["", f"## 코인별 손익 ({bu})", "", "| 코인 | 진입 | 청산 | $손익 |", "|---|---|---|---|"]
        for tk, r in g.iterrows():
            L.append(f"| {tk} | {int(r.n)} | {int(r.liq)} | ${r.pnl:+,.1f} |")
        L += ["", f"### 월별 ({bu})", "", "| 월 | 진입 | 청산 | $손익 |", "|---|---|---|---|"]
        for m, gg in bt.groupby(bt['ts'].dt.to_period('M')):
            L.append(f"| {m} | {len(gg)} | {int(gg['liq'].sum())} | ${gg['pnl'].sum():+,.1f} |")
    L += ["", f"소요 {time.time() - t0:.0f}초"]
    out = os.path.join(b2.REPORT_DIR, f"binance_backtest8_{datetime.now():%Y%m%d}.md")
    with open(out, 'w', encoding='utf-8') as f:
        f.write("\n".join(L) + "\n")
    log(f"결과 저장: {out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
