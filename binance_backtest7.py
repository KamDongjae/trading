# -*- coding: utf-8 -*-
"""
binance_backtest7.py
--------------------
[2026-11-25 신설, 사용자 요청] D 코인(원화 거래소 ∩ 바이낸스 선물)의 공통 특성을 숫자 기준으로 만들어 바이낸스 전체에 적용.

1) 특성 프로파일: 시험 시작 전 1년(사전 기간)만 사용 → 미래 정보 없음
   상장일수 / 30일 거래대금 중앙값(그날 후보 중 백분위) / 1h ATR(24h 평균) / 30일 일변동성 / 1년 고점 대비 낙폭 / 7일 상승률
   D 코인(BTC 제외) vs 나머지 바이낸스 코인 분포(10·25·50·75·90%) 비교
2) 규칙 G: 매일 각 특성이 D 분포의 구간 안에 있는 코인 (밈·스테이블·배수 제외)
   G 핵심(10~90%): 상장일수·거래대금 백분위·ATR
   G 전체(10~90%): + 30일 변동성·1년 고점 대비 낙폭
   G 전체(25~75%): 같은 특성, 좁은 구간
3) 시험: D 단독 / G / G − D(처음 보는 코인만 = 진짜 검증) × 모델 M_krw
공통: 자동진입 상장 180일↑, 판정 v2 숏, $30·3배·48h, 강한숏 $60, 비용 -2.1%, 역행 31.3% 청산, 청산 코인 7일 차단
통과: 앞뒤 6개월 + · 청산 ≤6%
결과: reports/binance_backtest7_YYYYMMDD.md, reports/binance_backtest7_trades.csv
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
PROFILE_DAYS = 365
FEATS = ['age', 'vpct', 'atr', 'vol30', 'dd365', 'r7']
FEAT_KO = {'age': '상장일수(일)', 'vpct': '거래대금 백분위(100=최대)', 'atr': '1h ATR %',
           'vol30': '30일 일변동성 %', 'dd365': '1년 고점 대비 %', 'r7': '7일 상승률 %'}
RULES = {
    'G핵심10-90': (['age', 'vpct', 'atr'], 0.10, 0.90),
    'G전체10-90': (['age', 'vpct', 'atr', 'vol30', 'dd365'], 0.10, 0.90),
    'G전체25-75': (['age', 'vpct', 'atr', 'vol30', 'dd365'], 0.25, 0.75),
}


def feature_panels(M, onboard, meme_set):
    """일별 × 코인 특성 패널. 모두 그날 09시 이전 정보만 사용."""
    V24, A, R7, DV, DC = M
    days = V24.index
    cols = V24.columns
    base = pd.DataFrame(True, index=days, columns=cols)
    for tk in cols:
        if tk in b3.STABLES or b3.is_multiplier(tk) or tk in meme_set:
            base[tk] = False
    med30 = DV.rolling(30, min_periods=20).median().shift(1).reindex(index=days, columns=cols)
    vpct = med30.where(base).rank(axis=1, pct=True) * 100
    ret = DC.pct_change() * 100
    vol30 = ret.rolling(30, min_periods=20).std().shift(1).reindex(index=days, columns=cols)
    dd365 = ((DC / DC.rolling(365, min_periods=60).max() - 1) * 100).shift(1).reindex(index=days, columns=cols)
    age = pd.DataFrame({tk: (days - onboard.get(tk, days[0])).days for tk in cols}, index=days).astype(float)
    P = {'age': age, 'vpct': vpct, 'atr': A.reindex(index=days, columns=cols),
         'vol30': vol30, 'dd365': dd365, 'r7': R7.reindex(index=days, columns=cols)}
    return P, base


def profile(P, base, D, pre_days):
    """사전 기간 D 코인 / 나머지 분포."""
    qs = [0.10, 0.25, 0.50, 0.75, 0.90]
    out = {}
    for f in FEATS:
        X = P[f].loc[pre_days].where(base.loc[pre_days])
        dcols = [c for c in X.columns if c in D]
        ocols = [c for c in X.columns if c not in D and c != 'BTC']
        dv = X[dcols].stack().dropna()
        ov = X[ocols].stack().dropna()
        out[f] = {'D': dv.quantile(qs).values if len(dv) else np.full(5, np.nan),
                  'O': ov.quantile(qs).values if len(ov) else np.full(5, np.nan),
                  'nD': len(dv), 'nO': len(ov)}
    return out


def rule_universe(P, base, prof_d, feats, lo_q, hi_q):
    """D 분포 lo_q~hi_q 구간 안에 모든 특성이 드는 코인. 상장일수는 하한만."""
    ok = base.copy()
    for f in feats:
        X = P[f]
        lo, hi = prof_d[f]['lo'][lo_q], prof_d[f]['hi'][hi_q]
        cond = X >= lo
        if f != 'age':
            cond &= X <= hi
        ok &= cond.fillna(False)
    if 'BTC' in ok.columns:
        ok['BTC'] = False
    return {day: frozenset(ok.columns[ok.loc[day].values]) for day in ok.index}


def d_bounds(P, base, D, pre_days):
    """각 특성의 D 분위수(사전 기간)."""
    qs = [0.10, 0.25, 0.75, 0.90]
    b = {}
    for f in FEATS:
        X = P[f].loc[pre_days].where(base.loc[pre_days])
        dv = X[[c for c in X.columns if c in D]].stack().dropna()
        q = dv.quantile(qs)
        b[f] = {'lo': {0.10: q.loc[0.10], 0.25: q.loc[0.25]}, 'hi': {0.75: q.loc[0.75], 0.90: q.loc[0.90]}}
    return b


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
    Dp = D - {'BTC'}
    end = max(d.index[-1] for d in bf.values())
    test_start = (end.normalize() - pd.DateOffset(months=a.months)).replace(day=1)
    split = test_start + pd.DateOffset(months=a.months // 2)
    log(f"바이낸스 {len(bf)}종목 · D {len(D)}개 · 시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d}")

    M = b4.daily_metrics(bf)
    P, base = feature_panels(M, onboard, meme_set)
    days = P['age'].index
    pre_days = days[(days < test_start) & (days >= test_start - pd.Timedelta(days=PROFILE_DAYS))]
    log(f"사전 기간 {pre_days[0]:%Y-%m-%d}~{pre_days[-1]:%Y-%m-%d} ({len(pre_days)}일)")
    prof = profile(P, base, Dp, pre_days)
    bnd = d_bounds(P, base, Dp, pre_days)
    for f in FEATS:
        log(f"  {f}: D {np.round(prof[f]['D'], 1)} / 나머지 {np.round(prof[f]['O'], 1)}")

    Dday = {day: frozenset(t for t in D if (day - onboard.get(t, bf[t].index[0])).days >= 7) for day in days}
    unis = {'D 단독': Dday}
    for rn, (feats, lq, hq) in RULES.items():
        g = rule_universe(P, base, bnd, feats, lq, hq)
        unis[f'{rn} (D 포함)'] = g
        unis[f'{rn} 처음 보는 코인만(G−D)'] = {d: s - D for d, s in g.items()}
    stat_lines = []
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
    for t in ('BTC',):
        if t in bf:
            pool.setdefault(t, set()).update(days)

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
        Tu = T[mask]
        Pq = Tu.join(pr, how='inner')
        if Pq.empty:
            rows.append((un, pd.DataFrame()))
            continue
        Pq = Pq.assign(mkt_r4=Pq.groupby('ts')['r4'].transform('mean'), u_r24=Pq.groupby('ts')['r24'].transform('mean'))
        sev = [e for e in b3.short_events(Pq) if e[7] >= MIN_AGE_AUTO_D]
        tr = b3.run_entries(sev, bf)
        if not tr.empty:
            tr = tr.assign(in_D=tr['ticker'].isin(D))
            allt.append(tr.assign(universe=un))
        rows.append((un, tr))
        log(f"{un} 끝: {len(tr)}건")

    os.makedirs(b2.REPORT_DIR, exist_ok=True)
    if allt:
        pd.concat(allt).to_csv(os.path.join(b2.REPORT_DIR, 'binance_backtest7_trades.csv'), index=False, encoding='utf-8-sig')

    L = [f"# D 코인 특성 → 바이낸스 전체 적용 검증 ({datetime.now():%Y-%m-%d %H:%M})", "",
         f"시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d} (앞/뒤 나눔 {split:%Y-%m-%d}) · 특성 기준은 사전 기간 {pre_days[0]:%Y-%m-%d}~{pre_days[-1]:%Y-%m-%d} D {len(Dp)}개(BTC 제외)로만 계산 · 모델 M_krw",
         "숏 판정 v2 · 자동진입 상장 180일↑ · $30·3배·48h · 강한숏 $60 · 비용 -2.1% · 역행 31.3% 청산 · 청산 코인 7일 차단 · 통과 = 앞뒤 6개월 + · 청산 ≤6%", "",
         "## 1. D 코인 vs 나머지 바이낸스 코인 (사전 기간, 코인·일 단위 분포)", "",
         "| 특성 | D 10% | D 25% | D 중앙 | D 75% | D 90% | 나머지 10% | 나머지 25% | 나머지 중앙 | 나머지 75% | 나머지 90% |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for f in FEATS:
        d, o = prof[f]['D'], prof[f]['O']
        L.append(f"| {FEAT_KO[f]} | " + " | ".join(f"{x:,.1f}" for x in d) + " | " + " | ".join(f"{x:,.1f}" for x in o) + " |")
    L += ["", "## 2. 규칙 (D 분포 구간, 상장일수는 하한만)", ""]
    for rn, (feats, lq, hq) in RULES.items():
        parts = []
        for f in feats:
            lo, hi = bnd[f]['lo'][lq], bnd[f]['hi'][hq]
            parts.append(f"{FEAT_KO[f]} ≥ {lo:,.1f}" if f == 'age' else f"{FEAT_KO[f]} {lo:,.1f}~{hi:,.1f}")
        L.append(f"- **{rn}**: " + " · ".join(parts))
    L += ["", "| 대상 | 하루 평균 코인 | 1년간 거쳐간 코인 | D 비중 |", "|---|---|---|---|"] + stat_lines
    L += ["", "## 3. 성적", "",
          "| 대상 | 진입 | 승률 | 건당 ROI | 청산 | $손익(1년) | 앞/뒤 6개월 | 최악월 | 마이너스 달 | 통과 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    res = [(un, b2.stats(t, split), t) for un, t in rows]
    for un, s, t in res:
        L.append(b2.row(un, s))
    L += ["", "## 4. G(D 포함)에서 D 코인 / 새 코인 나눠 보기", "",
          "| 대상 | D 코인 진입·청산·$ | 새 코인 진입·청산·$ |", "|---|---|---|"]
    for un, s, t in res:
        if '(D 포함)' not in un or t.empty:
            continue
        dd, ff = t[t['in_D']], t[~t['in_D']]
        L.append(f"| {un} | {len(dd)}건 · {int(dd['liq'].sum())} · ${dd['pnl'].sum():+,.0f} | {len(ff)}건 · {int(ff['liq'].sum())} · ${ff['pnl'].sum():+,.0f} |")
    newt = [t for un, s, t in res if 'G−D' in un and not t.empty]
    if newt:
        best_un, best_s, best_t = max([x for x in res if 'G−D' in x[0] and not x[2].empty], key=lambda x: x[1]['pnl'])
        g = best_t.groupby('ticker').agg(n=('pnl', 'size'), liq=('liq', 'sum'), pnl=('pnl', 'sum')).sort_values('pnl')
        L += ["", f"## 5. 새 코인 코인별 손익 ({best_un})", "", "| 코인 | 진입 | 청산 | $손익 |", "|---|---|---|---|"]
        for tk, r in g.iterrows():
            L.append(f"| {tk} | {int(r.n)} | {int(r.liq)} | ${r.pnl:+,.1f} |")
        L += ["", f"### 월별 ({best_un})", "", "| 월 | 진입 | 청산 | $손익 |", "|---|---|---|---|"]
        for m, gg in best_t.groupby(best_t['ts'].dt.to_period('M')):
            L.append(f"| {m} | {len(gg)} | {int(gg['liq'].sum())} | ${gg['pnl'].sum():+,.1f} |")
    L += ["", f"소요 {time.time() - t0:.0f}초"]
    out = os.path.join(b2.REPORT_DIR, f"binance_backtest7_{datetime.now():%Y%m%d}.md")
    with open(out, 'w', encoding='utf-8') as f:
        f.write("\n".join(L) + "\n")
    log(f"결과 저장: {out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
