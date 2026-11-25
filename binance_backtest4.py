# -*- coding: utf-8 -*-
"""
binance_backtest4.py
--------------------
[2026-11-25 신설, 사용자 요청] 바이낸스 전체 → '필터 먼저' 적용 → 거래대금 상위 N개 추출 순서로 검증.
(binance_backtest3.py 는 ATR·7일 상승 필터를 상위 40 뽑은 뒤 진입 때만 걸었음 — 순서가 반대)

매일 09시 KST 대상 코인(그날 08시까지 데이터로 계산):
  1) 필터: 스테이블·배수코인·밈(바이낸스 분류) 제외 · 상장 1년↑ · 최근 30일 하루 종가 ±30% 이상 변동 이력 없음
           · 1h ATR(직전 24h 평균) < ATR_MAX · 7일 상승률 < R7_MAX
  2) 남은 코인 중 거래대금 상위 N (24h 거래대금 / 30일 일거래대금 중앙값 비교), 이미 들어간 코인은 1.5N위까지 유지
비교: N 100/60/40 × 순위 기준 24h/30일 × ATR<5/3% × 7일<30/50% × 진입 때 같은 필터 재확인 있음/없음 × 모델 M_krw/M_bin
규칙·통과 기준은 binance_backtest3.py 와 같음(숏 판정 v2, $30·3배·48h, 강한숏 $60, 청산 코인 7일 차단, 앞뒤 6개월 + · 청산 ≤6%)
결과: reports/binance_backtest4_YYYYMMDD.md, reports/binance_backtest4_trades.csv
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
import prob_model as pm  # noqa: E402
import binance_backtest2 as b2  # noqa: E402
import binance_backtest3 as b3  # noqa: E402

log = b2.log
AGE_D = 365
NS = (100, 60, 40)
RANKS = ('24h', '30d')
ATRS = (5.0, 3.0)
R7S = (30.0, 50.0)


def daily_metrics(frames):
    """코인별 매일 08시 시점 값: 24h 거래대금, 1h ATR 24h 평균, 7일 상승률 / 일별 거래대금·종가."""
    m24, atr, r7, dv, dc = {}, {}, {}, {}, {}
    for tk, d in frames.items():
        c = d['close']
        tr = pd.concat([d['high'] - d['low'], (d['high'] - c.shift()).abs(), (d['low'] - c.shift()).abs()], axis=1).max(axis=1)
        a = (tr.rolling(14).mean() / c * 100).rolling(24, min_periods=12).mean()
        v24 = d['value'].rolling(24, min_periods=12).sum()
        rr = (c / c.shift(168) - 1) * 100
        at8 = d.index.hour == 8
        idx = d.index[at8].normalize()
        m24[tk] = pd.Series(v24[at8].values, index=idx)
        atr[tk] = pd.Series(a[at8].values, index=idx)
        r7[tk] = pd.Series(rr[at8].values, index=idx)
        g = d.resample('1D')
        dv[tk] = g['value'].sum()
        dc[tk] = g['close'].last()
    return (pd.DataFrame(m24).sort_index(), pd.DataFrame(atr).sort_index(), pd.DataFrame(r7).sort_index(),
            pd.DataFrame(dv).sort_index(), pd.DataFrame(dc).sort_index())


def universe(M, onboard, meme_set, rank_by, n, atr_max, r7_max):
    V24, A, R7, DV, DC = M
    days = V24.index
    med30 = DV.rolling(30, min_periods=20).median().shift(1).reindex(days)
    shock = (DC.pct_change().abs().mul(100).rolling(30, min_periods=1).max().shift(1) >= b3.SHOCK_PCT).reindex(days).fillna(False)
    age = pd.DataFrame({tk: (days - onboard.get(tk, days[0])).days for tk in V24.columns}, index=days)
    metric = V24 if rank_by == '24h' else med30.reindex(columns=V24.columns)
    elig = (age >= AGE_D) & ~shock.reindex(columns=V24.columns).fillna(False).astype(bool) & metric.notna() \
        & (A < atr_max) & (R7 < r7_max)
    for tk in V24.columns:
        if tk in b3.STABLES or b3.is_multiplier(tk) or tk in meme_set:
            elig[tk] = False
    rank = metric.where(elig).rank(axis=1, ascending=False, method='first')
    keep_n = int(n * 1.5)
    members, out = set(), {}
    for day in days:
        r = rank.loc[day]
        keep = {t for t in members if r.get(t, np.nan) <= keep_n}
        for t in r[r <= n].sort_values().index:
            if len(keep) >= n:
                break
            keep.add(t)
        members = keep
        out[day] = frozenset(members)
    return out


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
    log(f"밈 분류 {len(meme_set)}개")
    kf = b2.load_dir(b2.KRW_DIR, '*_1h.csv')
    K = b2.krw_table(kf)
    bf = b2.load_dir(b2.BIN_DIR, 'binance_*_1h.csv')
    end = max(d.index[-1] for d in bf.values())
    test_start = (end.normalize() - pd.DateOffset(months=a.months)).replace(day=1)
    split = test_start + pd.DateOffset(months=a.months // 2)
    log(f"바이낸스 {len(bf)}종목 · 시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d}")
    M = daily_metrics(bf)
    unis = {}
    for rk in RANKS:
        for n in NS:
            for am in ATRS:
                for r7 in R7S:
                    key = (rk, n, am, r7)
                    unis[key] = universe(M, onboard, meme_set, rk, n, am, r7)
                    sz = [len(v) for k, v in unis[key].items() if k >= test_start]
                    nm = set().union(*[v for k, v in unis[key].items() if k >= test_start])
                    log(f"[{rk} 상위{n} ATR<{am:g} 7일<{r7:g}] 하루 평균 {np.mean(sz):.1f}개 · 거쳐간 {len(nm)}개")
    pool = {}
    for u in unis.values():
        for day, s in u.items():
            for t in s:
                pool.setdefault(t, set()).add(day)

    def pool_mask(tk, idx):
        days = pool.get(tk)
        if not days:
            return np.zeros(len(idx), dtype=bool)
        dd = (idx - pd.Timedelta(hours=9)).normalize()
        return np.isin(dd.values, np.array(sorted(days), dtype='datetime64[ns]'))

    B = b3.build_table(bf, onboard, pool_mask, test_start)
    log(f"풀 테이블 {len(B):,}행 · {B['ticker'].nunique()}종목")
    T = B[B['ts'] >= test_start].copy()
    edges = pd.date_range(test_start, end + pd.DateOffset(months=1), freq='MS')
    preds = {'M_krw': b2.walk_forward(T, K, edges, 'M_krw'), 'M_bin': b2.walk_forward(T, B, edges, 'M_bin')}

    rows, allt = [], []
    for key, uni in unis.items():
        rk, n, am, r7 = key
        mask = b3.membership_mask(T['ts'], T['ticker'].values, uni)
        Tu = T[mask]
        for mn, pr in preds.items():
            P = Tu.join(pr, how='inner')
            if P.empty:
                continue
            P = P.assign(mkt_r4=P.groupby('ts')['r4'].transform('mean'), u_r24=P.groupby('ts')['r24'].transform('mean'))
            sev = b3.short_events(P)
            lev_ = b3.long_events(P)
            for chk in (False, True):
                tr = b3.run_entries(sev, bf, am if chk else None, r7 if chk else None)
                rows.append(((rk, n, am, r7, mn, 'short', chk), tr))
                if not tr.empty:
                    allt.append(tr.assign(rank_by=rk, top_n=n, atr_max=am, r7_max=r7, model=mn, entry_check=chk))
            tr = b3.run_entries(lev_, bf, am, None)
            rows.append(((rk, n, am, r7, mn, 'long', True), tr))
            if not tr.empty:
                allt.append(tr.assign(rank_by=rk, top_n=n, atr_max=am, r7_max=r7, model=mn, entry_check=True))
        log(f"{key} 끝")

    os.makedirs(b2.REPORT_DIR, exist_ok=True)
    if allt:
        pd.concat(allt).to_csv(os.path.join(b2.REPORT_DIR, 'binance_backtest4_trades.csv'), index=False, encoding='utf-8-sig')

    def label(k):
        rk, n, am, r7, mn, side, chk = k
        return f"{'24h' if rk == '24h' else '30일중앙값'} 상위{n} · ATR<{am:g} · 7일<{r7:g} · {mn}{' · 진입때 재확인' if (chk and side == 'short') else ''}"

    def table(side):
        res = [(label(k), b2.stats(t, split)) for k, t in rows if k[5] == side]
        res.sort(key=lambda x: (0 if (x[1]['n'] > 0 and x[1]['h1'] > 0 and x[1]['h2'] > 0 and x[1]['liqp'] <= 6) else 1, -float(x[1]['pnl'])))
        return [b2.row(nm, s) for nm, s in res]

    head = "| 대상(필터 후 추출) · 모델 | 진입 | 승률 | 건당 ROI | 청산 | $손익(1년) | 앞/뒤 6개월 | 최악월 | 마이너스 달 | 통과 |\n|---|---|---|---|---|---|---|---|---|---|"
    lines = [f"# 바이낸스 '필터 먼저 → 상위 N' 검증 ({datetime.now():%Y-%m-%d %H:%M})", "",
             f"시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d} (앞/뒤 나눔 {split:%Y-%m-%d}) · 매일 09시: 스테이블·배수·밈({len(meme_set)}개) 제외, 상장 1년↑, 30일 하루 ±30% 이력 제외, 1h ATR(24h 평균)·7일 상승률 필터 → 거래대금 상위 N(1.5N위까지 유지)",
             "숏 판정 v2 · $30·3배·48h · 강한숏 $60 · 비용 -2.1% · 역행 31.3% 청산 · 청산 코인 7일 차단 · 통과 = 앞뒤 6개월 + · 청산 ≤6% · 지금 상장된 종목만(상폐 코인 빠짐)", "",
             "## 숏", "", head] + table('short') + ["", "## 롱 (근사 — 모델만 p_up≥0.55·exp4>0.2, 대상 평균 24h<0이면 대기, 진입 때 ATR 재확인)", "", head] + table('long')
    best = [(k, t, b2.stats(t, split)) for k, t in rows if k[5] == 'short' and not t.empty]
    best = [x for x in best if x[2]['h1'] > 0 and x[2]['h2'] > 0 and x[2]['liqp'] <= 6]
    if best:
        k, tb, s = max(best, key=lambda x: x[2]['pnl'])
        lines += ["", f"## 통과 중 숏 1년 손익 최고: {label(k)}", "", "| 월 | 진입 | 청산 | $손익 |", "|---|---|---|---|"]
        for m, g in tb.groupby(tb['ts'].dt.to_period('M')):
            lines.append(f"| {m} | {len(g)} | {int(g['liq'].sum())} | ${g['pnl'].sum():+,.1f} |")
        top = tb.groupby('ticker')['pnl'].agg(['count', 'sum']).sort_values('sum')
        lines += ["", f"거쳐간 코인 {tb['ticker'].nunique()}개",
                  "손실 큰 코인 5: " + ", ".join(f"{i} {r['count']:.0f}건 ${r['sum']:+.0f}" for i, r in top.head(5).iterrows()),
                  "수익 큰 코인 5: " + ", ".join(f"{i} {r['count']:.0f}건 ${r['sum']:+.0f}" for i, r in top.tail(5)[::-1].iterrows())]
        liqs = tb[tb['liq']]
        if len(liqs):
            lines.append("청산: " + ", ".join(f"{r.ticker} {r.ts:%m/%d}" for r in liqs.itertuples()))
    else:
        lines += ["", "## 숏 통과 조합 없음"]
    lines += ["", "참고: 원화 거래소 코인 28개·원화 모델 $+564·청산 5.9%(7일 차단 근사 $+613·4.7%) / binance_backtest3(30일중앙값 상위40 뒤 필터) 원화 모델 전부 마이너스",
              "", f"소요 {time.time() - t0:.0f}초"]
    out = os.path.join(b2.REPORT_DIR, f"binance_backtest4_{datetime.now():%Y%m%d}.md")
    with open(out, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines) + "\n")
    log(f"결과 저장: {out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
