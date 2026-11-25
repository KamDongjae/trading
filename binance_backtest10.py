# -*- coding: utf-8 -*-
"""
binance_backtest10.py
---------------------
[2026-11-25 신설, 사용자 요청] '순위 먼저 → 구간 안에서 필터' 방식 검증.
전체 순위: 매일 09시 바이낸스 선물 전체(스테이블만 제외)를 24h 거래대금으로 줄 세움
구간 41-80 / 81-120 / 121-160 / 161-200 각각에서 필터(밈·배수 제외, 상장 1년↑, 30일 하루 ±30% 이력 제외, ATR<3, 7일<50) 통과한 코인만
  + 네 구간 생존자 합친 그룹(전체 41-200위 생존자) + 전체 81-200위 생존자
비교 기준(같은 실행): bt9 최선 = 필터 먼저 → 필터 후 순위 81-200위
각 대상 × 한도(제한 없음 / 동시 10개 / 동시 20개)
공통: 판정 v2 숏 · 모델 M_krw · 자동진입 상장 180일↑ · $30·3배·48h · 강한숏 $60 · 비용 -2.1% · 역행 31.3% 강제청산(7일 차단) · 진입 때 ATR·7일 재확인
통과: 앞뒤 6개월 + · 청산 ≤6%
결과: reports/binance_backtest10_YYYYMMDD.md, reports/binance_backtest10_trades.csv
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

log = b2.log
MIN_AGE_AUTO_D = 180
RAW_BANDS = [(41, 80), (81, 120), (121, 160), (161, 200)]
LIMITS = {'제한 없음': dict(max_n=None), '동시 10개': dict(max_n=10), '동시 20개': dict(max_n=20)}


def raw_band_survivors(M, onboard, meme_set, lo, hi, atr_max=3.0, r7_max=50.0):
    """전체(스테이블만 제외) 24h 거래대금 순위 lo~hi 위 → 그 안에서 필터 통과한 코인."""
    V24, A, R7, DV, DC = M
    days = V24.index
    cols = V24.columns
    nonstable = pd.Series([tk not in b3.STABLES for tk in cols], index=cols)
    raw_rank = V24.loc[:, nonstable.values].rank(axis=1, ascending=False, method='first').reindex(columns=cols)
    inband = (raw_rank >= lo) & (raw_rank <= hi)
    shock = (DC.pct_change().abs().mul(100).rolling(30, min_periods=1).max().shift(1) >= b3.SHOCK_PCT).reindex(days).fillna(False)
    age = pd.DataFrame({tk: (days - onboard.get(tk, days[0])).days for tk in cols}, index=days)
    ok = inband & (age >= b4.AGE_D) & ~shock.reindex(columns=cols).fillna(False).astype(bool) & (A < atr_max) & (R7 < r7_max)
    for tk in cols:
        if tk in b3.STABLES or b3.is_multiplier(tk) or tk in meme_set:
            ok[tk] = False
    ok = ok.fillna(False).astype(bool)
    return {day: frozenset(cols[ok.loc[day].values]) for day in days}, inband


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
    end = max(d.index[-1] for d in bf.values())
    test_start = (end.normalize() - pd.DateOffset(months=a.months)).replace(day=1)
    split = test_start + pd.DateOffset(months=a.months // 2)
    log(f"바이낸스 {len(bf)}종목 · 시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d}")
    M = b4.daily_metrics(bf)
    days = M[0].index
    tdays = days[days >= test_start]

    unis, survive = {}, {}
    for lo, hi in RAW_BANDS:
        u, inband = raw_band_survivors(M, onboard, meme_set, lo, hi)
        unis[f'전체 {lo}-{hi}위 생존'] = u
        n_in = inband.loc[tdays].sum(axis=1).mean()
        n_ok = np.mean([len(u[d]) for d in tdays])
        survive[f'전체 {lo}-{hi}위'] = (n_in, n_ok)
    unis['전체 41-200위 생존(네 구간 합침)'] = {d: frozenset().union(*[unis[f'전체 {lo}-{hi}위 생존'][d] for lo, hi in RAW_BANDS]) for d in days}
    unis['전체 81-200위 생존'] = {d: frozenset().union(*[unis[f'전체 {lo}-{hi}위 생존'][d] for lo, hi in RAW_BANDS[1:]]) for d in days}
    unis['[기준] 필터 후 81-200위'] = b8.band_universe(M, onboard, meme_set, 81, 200)

    # 필터 후 순위로 환산하면 어디쯤인지
    V24, A, R7, DV, DC = M
    ref_all = b8.band_universe(M, onboard, meme_set, 1, 10000)
    stat_lines = []
    for k, u in unis.items():
        sz = [len(u[d]) for d in tdays]
        nm = set().union(*[u[d] for d in tdays])
        frk = []
        for d in tdays[::7]:
            s = sorted(ref_all[d], key=lambda t: -V24.at[d, t] if pd.notna(V24.at[d, t]) else 0)
            pos = {t: i + 1 for i, t in enumerate(s)}
            frk += [pos[t] for t in u[d] if t in pos]
        rng = f"{np.percentile(frk, 10):.0f}~{np.percentile(frk, 90):.0f}" if frk else '–'
        stat_lines.append(f"| {k} | {np.mean(sz):.1f} | {len(nm)} | {rng} |")
        log(f"[{k}] 하루 평균 {np.mean(sz):.1f}개 · 거쳐간 {len(nm)}개 · 필터 후 순위 10~90% {rng}")

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

    res, allt = [], []
    for un, uni in unis.items():
        mask = b3.membership_mask(T['ts'], T['ticker'].values, uni)
        Pq = T[mask].join(pr, how='inner')
        if Pq.empty:
            continue
        Pq = Pq.assign(mkt_r4=Pq.groupby('ts')['r4'].transform('mean'), u_r24=Pq.groupby('ts')['r24'].transform('mean'))
        sev = [e for e in b3.short_events(Pq) if e[7] >= MIN_AGE_AUTO_D]
        lims = LIMITS if ('합침' in un or '81-200' in un) else {'제한 없음': LIMITS['제한 없음']}
        for ln, lim in lims.items():
            tr, mo, mm = b9.run_limited(sev, bf, **lim)
            s = b2.stats(tr, split)
            dd = b9.max_dd(tr)
            res.append((f"{un} · {ln}", s, tr, mo, mm, dd))
            if not tr.empty:
                allt.append(tr.assign(universe=un, limit=ln))
            log(f"{un} · {ln}: {len(tr)}건 ${s['pnl']:+,.0f} · 낙폭 ${dd:+,.0f} · 최대동시 {mo}")

    os.makedirs(b2.REPORT_DIR, exist_ok=True)
    if allt:
        pd.concat(allt).to_csv(os.path.join(b2.REPORT_DIR, 'binance_backtest10_trades.csv'), index=False, encoding='utf-8-sig')
    L = [f"# 순위 먼저 → 구간 안 필터 방식 검증 ({datetime.now():%Y-%m-%d %H:%M})", "",
         f"시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d} (앞/뒤 나눔 {split:%Y-%m-%d}) · 전체 순위 = 매일 09시 바이낸스 선물 전체(스테이블만 제외) 24h 거래대금 순위 → 구간 안에서 필터(밈·배수 제외, 상장 1년↑, ±30% 이력 제외, ATR<3, 7일<50) · 모델 M_krw · 진입 때 ATR·7일 재확인",
         "숏 판정 v2 · 자동진입 상장 180일↑ · $30·3배·48h · 강한숏 $60 · 비용 -2.1% · 역행 31.3% 강제청산(7일 차단) · 통과 = 앞뒤 6개월 + · 청산 ≤6%", "",
         "## 구간별 생존율", "", "| 구간 | 구간 안 코인(하루 평균) | 필터 생존(하루 평균) | 생존율 |", "|---|---|---|---|"]
    for k, (ni, no) in survive.items():
        L.append(f"| {k} | {ni:.1f} | {no:.1f} | {no / ni * 100:.0f}% |")
    L += ["", "| 대상 | 하루 평균 코인 | 1년간 거쳐간 코인 | 필터 후 순위로 환산(10~90%) |", "|---|---|---|---|"] + stat_lines
    L += ["", "## 성적", "", "| 대상 · 한도 | 진입 | 승률 | 건당 ROI | 청산 | $손익(1년) | 앞/뒤 6개월 | 최악월 | 마이너스 달 | 통과 | 최대 동시 | 최대 증거금 | 최대 낙폭 | 손익/낙폭 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for nm, s, tr, mo, mm, dd in res:
        ratio = s['pnl'] / max(1.0, -dd)
        L.append(b2.row(nm, s) + f" {mo} | ${mm:,.0f} | ${dd:+,.0f} | {ratio:.1f} |")
    okr = [x for x in res if x[1]['n'] and x[1]['h1'] > 0 and x[1]['h2'] > 0 and x[1]['liqp'] <= 6]
    if okr:
        nm, s, tb, mo, mm, dd = max(okr, key=lambda x: x[1]['pnl'] / max(1.0, -x[5]))
        L += ["", f"## 통과 중 손익/최대낙폭 최고: {nm}", "", "| 월 | 진입 | 청산 | $손익 |", "|---|---|---|---|"]
        for m, g in tb.groupby(tb['ts'].dt.to_period('M')):
            L.append(f"| {m} | {len(g)} | {int(g['liq'].sum())} | ${g['pnl'].sum():+,.1f} |")
    L += ["", f"소요 {time.time() - t0:.0f}초"]
    out = os.path.join(b2.REPORT_DIR, f"binance_backtest10_{datetime.now():%Y%m%d}.md")
    with open(out, 'w', encoding='utf-8') as f:
        f.write("\n".join(L) + "\n")
    log(f"결과 저장: {out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
