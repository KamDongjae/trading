# -*- coding: utf-8 -*-
"""
binance_backtest11.py
---------------------
[2026-11-25 신설, 사용자 요청] 개편 2단계: 대상 범위와 무관한 특성 + 바이낸스 넓은 데이터로 모델 재학습 검증.

문제: 기존 시장 특성(mkt_r24·breadth·reg_c·rk_* 등)과 exp4 목표(x4)·mkt_r4 가 '그 시험에 들어간 코인 묶음' 기준이라
      대상 범위를 바꾸면 같은 코인의 예측값이 달라짐(bt9 vs bt10 같은 조합 결과 차이).
해결: 고정 기준 묶음 REF = 매일 09시 필터(밈·스테이블·배수 제외, 상장 1년↑, ±30% 이력 제외, ATR<3, 7일<50) 후 24h 거래대금 상위 200
      모든 시장 특성·순위 특성·x4·mkt_r4 를 REF 기준으로 계산 → 어떤 대상을 고르든 같은 코인·같은 시각이면 같은 값
모델(3개월마다 재학습, 학습은 시험 월 시작 3일 전까지 데이터만):
  V0 기존: M_krw(원화 history 학습) + 대상 묶음 기준 특성(bt9와 같은 방식, 매월 재학습)
  V1 M_ref: 바이낸스 REF 코인 학습 + REF 기준 특성
  V2 M_ref 처음 보는 코인: 코인을 이름 해시로 A/B 반 나눔 → A로 학습한 모델로 B 시험, B로 학습한 모델로 A 시험
  V3 M_ref 시장 특성 뺌: 코인 자체 특성 + BTC 특성만
대상: 필터 후 거래대금 81-200위 (bt9·bt10 최선) · 한도 제한 없음 / 동시 20개
공통: 판정 v2 숏(exp4<-0.5 · p_up<0.45 · 시장 4h≥+1% 대기 · 숏 손절 차단) · 자동진입 상장 180일↑ · $30·3배·48h · 강한숏 $60
      · 비용 -2.1% · 역행 31.3% 강제청산(7일 차단) · 진입 때 ATR·7일 재확인
통과: 앞뒤 6개월 + · 청산 ≤6%
결과: reports/binance_backtest11_YYYYMMDD.md, reports/binance_backtest11_trades.csv
"""
import os
import sys
import json
import time
import hashlib
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
import prob_model as pm  # noqa: E402

log = b2.log
MIN_AGE_AUTO_D = 180
REF_N = 200
UNI_BAND = (81, 200)
TRAIN_STEP_H = 4            # 학습 행: 4시간마다
LIMITS = {'제한 없음': dict(max_n=None), '동시 20개': dict(max_n=20)}
RANK_COLS = ['r24', 'r72', 'r168', 'rsi', 'atr_pct', 'vol_z']
FEATS_NOXS = pm.TICKER_FEATS + ['btc_r24', 'btc_r72', 'btc_r168']


def coin_half(tk):
    return int(hashlib.md5(tk.encode('utf-8')).hexdigest(), 16) % 2


def day_mask(tk, idx, days_by_tk):
    ds = days_by_tk.get(tk)
    if not ds:
        return np.zeros(len(idx), dtype=bool)
    dd = (idx - pd.Timedelta(hours=9)).normalize()
    return np.isin(dd.values, np.array(sorted(ds), dtype='datetime64[ns]'))


def build_ref_table(bf, onboard, ref, uni, test_start):
    """REF ∪ 대상 코인 행. 시험 전은 4시간마다, 시험 기간은 매시. is_ref·REF 기준 특성 포함."""
    need, refd = {}, {}
    for u, dst in ((ref, need), (uni, need)):
        for day, s in u.items():
            for t in s:
                dst.setdefault(t, set()).add(day)
    for day, s in ref.items():
        for t in s:
            refd.setdefault(t, set()).add(day)
    if 'BTC' in bf:
        need['BTC'] = set(ref.keys())
    out = []
    for tk, d in bf.items():
        mk = day_mask(tk, d.index, need)
        mk &= (d.index.hour % TRAIN_STEP_H == 0) | (d.index >= test_start)
        if not mk.any():
            continue
        f = pm.build_features(d)
        f['ticker'] = tk
        f['age_d'] = (d.index - onboard.get(tk, d.index[0])).total_seconds() / 86400
        f['f24'] = (d['close'].shift(-24) / d['close'] - 1) * 100
        f['f4'] = (d['close'].shift(-4) / d['close'] - 1) * 100
        f['is_ref'] = day_mask(tk, d.index, refd)
        out.append(f[mk])
    X = pd.concat(out).reset_index()
    X = X.rename(columns={X.columns[0]: 'ts'})
    X = X[~X['_stale']].sort_values(['ts', 'ticker']).reset_index(drop=True)
    for c in pm.TICKER_FEATS + ['p', 'ema60', 'f24', 'f4', 'age_d']:
        X[c] = X[c].astype('float32')
    return add_ref_cross_section(X)


def add_ref_cross_section(X):
    R = X[X['is_ref']]
    g = R.groupby('ts')
    agg = pd.DataFrame({
        'mkt_r24': g['r24'].mean(), 'mkt_r72': g['r72'].mean(), 'mkt_r4': g['r4'].mean(), 'mkt_f4': g['f4'].mean(),
        'breadth': (R['p'] > R['ema60']).astype(float).groupby(R['ts']).mean(),
        '_nb': R['_bull'].astype(float).groupby(R['ts']).sum(), '_ns': R['_bear'].astype(float).groupby(R['ts']).sum(),
        '_aatr': g['atr_pct'].mean()})
    tb = (agg['_nb'] - agg['_ns']) / (agg['_nb'] + agg['_ns']).replace(0, np.nan)
    agg['reg_c'] = np.select([agg['_aatr'] >= 2, tb >= 0.15, tb <= -0.15], [2, 1, -1], 0)
    for c in ['mkt_r24', 'mkt_r72', 'mkt_r4', 'mkt_f4', 'breadth', 'reg_c']:
        X[c] = X['ts'].map(agg[c]).astype('float32')
    btc = X[X['ticker'] == 'BTC'].set_index('ts')[['r24', 'r72', 'r168']]
    btc = btc[~btc.index.duplicated()]
    for c in ['r24', 'r72', 'r168']:
        X['btc_' + c] = X['ts'].map(btc[c]).astype('float32') if len(btc) else np.nan
    # 순위 특성: 같은 시각 REF 분포 안에서의 백분위
    ts = X['ts'].values
    bounds = np.flatnonzero(np.r_[True, ts[1:] != ts[:-1], True])
    isref = X['is_ref'].values
    for c in RANK_COLS:
        v = X[c].values.astype('float64')
        rk = np.full(len(v), np.nan)
        for a, b in zip(bounds[:-1], bounds[1:]):
            rv = v[a:b][isref[a:b]]
            rv = np.sort(rv[~np.isnan(rv)])
            if len(rv) < 20:
                continue
            seg = v[a:b]
            r = (np.searchsorted(rv, seg, 'left') + np.searchsorted(rv, seg, 'right')) / 2 / len(rv)
            r[np.isnan(seg)] = np.nan
            rk[a:b] = r
        X['rk_' + c] = rk.astype('float32')
    X['x4'] = (X['f4'] - X['mkt_f4']).astype('float32')
    return X


def walk_forward_ref(X, test_mask, edges, feats, name, train_coin_filter=None, test_coin_filter=None):
    L = X[X['is_ref'] & X['f24'].notna() & (X['ts'].dt.hour % TRAIN_STEP_H == 0)]
    if train_coin_filter is not None:
        L = L[L['ticker'].map(train_coin_filter)]
    T = X[test_mask]
    if test_coin_filter is not None:
        T = T[T['ticker'].map(test_coin_filter)]
    parts = []
    for a, b in zip(edges[:-1], edges[1:]):
        te = T[(T['ts'] >= a) & (T['ts'] < b)]
        if te.empty:
            continue
        tr = L[L['ts'] < a - pd.Timedelta(days=3)]
        if len(tr) < b2.MIN_TRAIN_ROWS:
            log(f"[{name}] {a:%Y-%m} 학습 데이터 부족({len(tr)}) — 건너뜀")
            continue
        t0 = time.time()
        m = pm._new_model().fit(tr[feats], (tr['f24'] > 0).astype(int))
        tr4 = tr[tr['x4'].notna()]
        m4 = pm._new_reg().fit(tr4[feats], tr4['x4'].clip(-15, 15))
        parts.append(pd.DataFrame({'p_up': m.predict_proba(te[feats])[:, 1], 'exp4': m4.predict(te[feats])}, index=te.index))
        log(f"[{name}] {a:%Y-%m}~ 학습 {len(tr):,}행·{tr['ticker'].nunique()}종목(첫 {tr['ts'].min():%Y-%m-%d}) → 예측 {len(te):,}행 ({time.time() - t0:.0f}s)")
    return pd.concat(parts) if parts else pd.DataFrame(columns=['p_up', 'exp4'])


def events_from(X, pred):
    P = X.loc[pred.index].join(pred)
    return [e for e in b3.short_events(P) if e[7] >= MIN_AGE_AUTO_D], P


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
    ref = b8.band_universe(M, onboard, meme_set, 1, REF_N)
    uni = b8.band_universe(M, onboard, meme_set, *UNI_BAND)
    tdays = [d for d in uni if d >= test_start]
    log(f"REF 하루 평균 {np.mean([len(ref[d]) for d in tdays]):.1f}개 · 대상 하루 평균 {np.mean([len(uni[d]) for d in tdays]):.1f}개")

    results = []  # (모델명, 한도, trades, mo, mm, dd, n_events)

    def run_all(name, sev):
        for ln, lim in LIMITS.items():
            tr, mo, mm = b9.run_limited(sev, bf, **lim)
            dd = b9.max_dd(tr)
            results.append((name, ln, tr, mo, mm, dd, len(sev)))
            s = b2.stats(tr, split)
            log(f"{name} · {ln}: {len(tr)}건 ${s['pnl']:+,.0f} (앞 {s['h1']:+,.0f} / 뒤 {s['h2']:+,.0f}) · 낙폭 ${dd:+,.0f}")

    # ── V0 기존 방식 ──
    kf = b2.load_dir(b2.KRW_DIR, '*_1h.csv')
    K = b2.krw_table(kf)
    pool = {}
    for day, s in uni.items():
        if day >= test_start - pd.Timedelta(days=3):
            for t in s:
                pool.setdefault(t, set()).add(day)
    if 'BTC' in bf:
        pool.setdefault('BTC', set()).update(uni.keys())
    B = b3.build_table(bf, onboard, lambda tk, idx: day_mask(tk, idx, pool), test_start)
    T0 = B[B['ts'] >= test_start].copy()
    edges1 = pd.date_range(test_start, end + pd.DateOffset(months=1), freq='MS')
    pr0 = b2.walk_forward(T0, K, edges1, 'V0 M_krw')
    m0 = b3.membership_mask(T0['ts'], T0['ticker'].values, uni)
    P0 = T0[m0].join(pr0, how='inner')
    P0 = P0.assign(mkt_r4=P0.groupby('ts')['r4'].transform('mean'))
    sev0 = [e for e in b3.short_events(P0) if e[7] >= MIN_AGE_AUTO_D]
    run_all('V0 기존(M_krw·대상 기준 특성)', sev0)
    del K, B, T0, P0, kf

    # ── REF 기준 테이블 ──
    X = build_ref_table(bf, onboard, ref, uni, test_start)
    log(f"REF 테이블 {len(X):,}행 · {X['ticker'].nunique()}종목 · REF 행 {int(X['is_ref'].sum()):,}")
    test_mask = (X['ts'] >= test_start).values & b3.membership_mask(X['ts'], X['ticker'].values, uni)
    edges3 = pd.date_range(test_start, end + pd.DateOffset(months=3), freq='3MS')
    sig_lines = []

    def sig_stat(name, P):
        r = (P['exp4'] < b3.EXP4_MAX) & (P['p_up'] < b3.PUP_MAX)
        sig_lines.append(f"| {name} | {P['p_up'].mean():.3f} | {P['exp4'].mean():+.2f} | {r.mean() * 100:.2f}% |")

    pr1 = walk_forward_ref(X, test_mask, edges3, pm.FEATURES, 'V1 M_ref')
    sev1, P1 = events_from(X, pr1)
    sig_stat('V1 M_ref', P1)
    run_all('V1 M_ref(바이낸스 학습·REF 기준 특성)', sev1)

    prA = walk_forward_ref(X, test_mask, edges3, pm.FEATURES, 'V2 A학습→B시험',
                           train_coin_filter=lambda t: coin_half(t) == 0, test_coin_filter=lambda t: coin_half(t) == 1)
    prB = walk_forward_ref(X, test_mask, edges3, pm.FEATURES, 'V2 B학습→A시험',
                           train_coin_filter=lambda t: coin_half(t) == 1, test_coin_filter=lambda t: coin_half(t) == 0)
    pr2 = pd.concat([prA, prB])
    sev2, P2 = events_from(X, pr2)
    sig_stat('V2 처음 보는 코인', P2)
    run_all('V2 M_ref 처음 보는 코인(반 나눠 교차)', sev2)

    pr3 = walk_forward_ref(X, test_mask, edges3, FEATS_NOXS, 'V3 시장특성 뺌')
    sev3, P3 = events_from(X, pr3)
    sig_stat('V3 시장 특성 뺌', P3)
    run_all('V3 M_ref 시장 특성 뺌(코인+BTC만)', sev3)

    os.makedirs(b2.REPORT_DIR, exist_ok=True)
    allt = [r[2].assign(model=r[0], limit=r[1]) for r in results if not r[2].empty]
    if allt:
        pd.concat(allt).to_csv(os.path.join(b2.REPORT_DIR, 'binance_backtest11_trades.csv'), index=False, encoding='utf-8-sig')
    L = [f"# 개편 2단계: 대상 범위와 무관한 특성 + 바이낸스 학습 모델 ({datetime.now():%Y-%m-%d %H:%M})", "",
         f"시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d} (앞/뒤 나눔 {split:%Y-%m-%d}) · 대상 = 필터 후 24h 거래대금 {UNI_BAND[0]}-{UNI_BAND[1]}위 · "
         f"REF(시장 특성 기준 묶음) = 필터 후 상위 {REF_N} · 학습 4시간마다 행 · V0 매월 / V1~V3 3개월마다 재학습(시험 월 3일 전까지 데이터만)",
         "숏 판정 v2 · 자동진입 상장 180일↑ · $30·3배·48h · 강한숏 $60 · 비용 -2.1% · 역행 31.3% 강제청산(7일 차단) · 진입 때 ATR·7일 재확인 · 통과 = 앞뒤 6개월 + · 청산 ≤6%", "",
         "| 모델 · 한도 | 진입 | 승률 | 건당 ROI | 청산 | $손익(1년) | 앞/뒤 6개월 | 최악월 | 마이너스 달 | 통과 | 판정 횟수 | 최대 동시 | 최대 낙폭 | 손익/낙폭 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, ln, tr, mo, mm, dd, ne in results:
        s = b2.stats(tr, split)
        L.append(b2.row(f"{name} · {ln}", s) + f" {ne} | {mo} | ${dd:+,.0f} | {s['pnl'] / max(1.0, -dd):.1f} |")
    L += ["", "## 예측값 분포(시험 대상 행)", "", "| 모델 | p_up 평균 | exp4 평균 | 숏 조건(exp4<-0.5 · p_up<0.45) 비율 |", "|---|---|---|---|"] + sig_lines
    L += ["", "참고: V1~V3 의 시장·순위 특성과 x4·mkt_r4 는 고정 REF 기준이라, 대상 범위를 바꿔도 같은 코인·같은 시각의 예측값은 같음."]
    best = [r for r in results if not r[2].empty]
    if best:
        name, ln, tb, mo, mm, dd, ne = max(best, key=lambda r: b2.stats(r[2], split)['pnl'] / max(1.0, -r[5]))
        L += ["", f"## 손익/낙폭 최고: {name} · {ln}", "", "| 월 | 진입 | 청산 | $손익 |", "|---|---|---|---|"]
        for m, g in tb.groupby(tb['ts'].dt.to_period('M')):
            L.append(f"| {m} | {len(g)} | {int(g['liq'].sum())} | ${g['pnl'].sum():+,.1f} |")
    L += ["", f"소요 {time.time() - t0:.0f}초"]
    out = os.path.join(b2.REPORT_DIR, f"binance_backtest11_{datetime.now():%Y%m%d}.md")
    with open(out, 'w', encoding='utf-8') as f:
        f.write("\n".join(L) + "\n")
    log(f"결과 저장: {out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
