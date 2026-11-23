# -*- coding: utf-8 -*-
"""
binance_backtest2.py
--------------------
[2026-11-23 신설, 사용자 요청] 바이낸스 탭 붙이기 전 2차 검증.
1차(binance_backtest.py)는 원화로 학습한 모델을 바이낸스 거래대금 상위 40에 적용 → 밈코인이 계속 들어와 청산 21.8%.
여기서는 두 가지를 바꿔 본다.
  1) 모델: 원화로 학습(M_krw) vs 바이낸스로 학습(M_bin) — 둘 다 월별 워크포워드(그 달 3일 전까지 데이터로 학습)
  2) 대상 코인(유니버스):
     A 24h 거래대금 상위 40 (1차와 같음)
     B 상장 1년↑ 중 30일 거래대금 상위 40
     C 상장 1년↑ 중 30일 거래대금 상위 20 (대형 코인)
     D 원화 거래소(history/)에 있는 코인 중 바이낸스 선물 있는 것
판정·자동진입 규칙은 1차와 같음(판정 v2 숏, $30·3배·48h, 강한 숏 $60, 비용 -2.1%, 역행 31.3% 청산, 같은 코인 건너뜀,
상장 180일 미만 자동진입 안 함, 청산 코인 48h 차단). 롱은 1차처럼 제외.
통과 기준: 앞 6개월·뒤 6개월 둘 다 플러스, 청산 비율 6% 이하.

데이터: history_binance/binance_*_1h.csv (binance_history_backfill.py 로 최대 1100일), history/*_1h.csv(원화)
결과: reports/binance_backtest2_YYYYMMDD.md, reports/binance_backtest2_trades.csv
"""
import os
import sys
import glob
import json
import time
import argparse
from datetime import datetime

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import prob_model as pm  # noqa: E402

BIN_DIR = os.path.join(SCRIPT_DIR, "history_binance")
KRW_DIR = os.path.join(SCRIPT_DIR, "history")
REPORT_DIR = os.path.join(SCRIPT_DIR, "reports")

TABLE_TOP = 100                 # 피처 테이블: 매시 24h 거래대금 상위 100 + 원화 코인 + BTC
MIN_AGE_VERDICT_D = 7
MIN_AGE_AUTO_D = 180
EXP4_MAX, PUP_MAX, MKT_R4_BLOCK = -0.5, 0.45, 1.0
STOP_PCT, STOP_WATCH_H, STOP_BLOCK_H = 3.0, 24, 24
USD, LEV, HOLD_H = 30.0, 3, 48
STRONG_EXP4, STRONG_MULT = -1.0, 2.0
COST = 0.021
LIQ_MOVE = 0.313
LIQ_BLOCK_H = 48
MIN_TRAIN_ROWS = 20000


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_dir(d, pattern):
    frames = {}
    for f in sorted(glob.glob(os.path.join(d, pattern))):
        base = os.path.basename(f)
        parts = base.split('_')
        if len(parts) < 3:
            continue
        src, tk = parts[0], '_'.join(parts[1:-1])
        if tk in frames and src != 'upbit':
            continue
        try:
            x = pd.read_csv(f)
            x['ts'] = pd.to_datetime(x['ts_kst'])
            x = x.set_index('ts').sort_index()
            x = x[~x.index.duplicated()]
            cols = ['open', 'high', 'low', 'close', 'volume'] + (['value'] if 'value' in x.columns else [])
            x = x[cols].astype(float).asfreq('1h')
            x['close'] = x['close'].ffill()
            for c in ['open', 'high', 'low']:
                x[c] = x[c].fillna(x['close'])
            x['volume'] = x['volume'].fillna(0)
            if 'value' in x.columns:
                x['value'] = x['value'].fillna(0)
            else:
                x['value'] = x['volume'] * x['close']
            if len(x) >= 300:
                frames[tk] = x
        except Exception as e:
            log(f"읽기 실패 {base}: {e}")
    return frames


def _finish(X):
    X = pm.add_cross_section(X)
    X['x4'] = X['f4'] - X.groupby('ts')['f4'].transform('mean')
    return X


def krw_table(frames):
    out = []
    for tk, d in frames.items():
        f = pm.build_features(d)
        f['ticker'] = tk
        f['f24'] = (d['close'].shift(-24) / d['close'] - 1) * 100
        f['f4'] = (d['close'].shift(-4) / d['close'] - 1) * 100
        f = f.iloc[200:]
        f = f[f.index.hour % 2 == 0]
        out.append(f)
    X = pd.concat(out).reset_index()
    X = X.rename(columns={X.columns[0]: 'ts'})
    X = X[~X['_stale']]
    return _finish(X)


def binance_table(frames, onboard, krw_coins, test_start):
    V24 = pd.DataFrame({tk: d['value'].rolling(24, min_periods=1).sum() for tk, d in frames.items()})
    V30 = pd.DataFrame({tk: d['value'].rolling(720, min_periods=24).sum() for tk, d in frames.items()})
    idx = V24.index
    age_d = pd.DataFrame({tk: (idx - onboard.get(tk, frames[tk].index[0])).total_seconds() / 86400
                          for tk in V24.columns}, index=idx)
    V24ok = V24.where((age_d >= MIN_AGE_VERDICT_D) & V24.notna())
    keep = V24ok.rank(axis=1, ascending=False, method='first') <= TABLE_TOP
    for tk in keep.columns:
        if tk in krw_coins or tk == 'BTC':
            keep[tk] = V24ok[tk].notna()
    out = []
    for tk, d in frames.items():
        mk = keep[tk].reindex(d.index).fillna(False).astype(bool)
        mk &= (d.index.hour % 2 == 0) | (d.index >= test_start)
        if not mk.any():
            continue
        f = pm.build_features(d)
        f['ticker'] = tk
        f['age_d'] = age_d[tk].reindex(d.index).values
        f['val24'] = V24[tk].reindex(d.index).values
        f['val720'] = V30[tk].reindex(d.index).values
        f['f24'] = (d['close'].shift(-24) / d['close'] - 1) * 100
        f['f4'] = (d['close'].shift(-4) / d['close'] - 1) * 100
        out.append(f[mk.values])
    X = pd.concat(out).reset_index()
    X = X.rename(columns={X.columns[0]: 'ts'})
    X = X[~X['_stale']]
    for c in pm.FEATURES:
        if c in X.columns:
            X[c] = X[c].astype('float32')
    return _finish(X)


def walk_forward(T_test, L_train, months_edges, name):
    parts = []
    L = L_train[L_train['f24'].notna()]
    L = L[L['ts'].dt.hour % 2 == 0]
    for a, b in zip(months_edges[:-1], months_edges[1:]):
        te = T_test[(T_test['ts'] >= a) & (T_test['ts'] < b)]
        if te.empty:
            continue
        tr = L[L['ts'] < a - pd.Timedelta(days=3)]
        if len(tr) < MIN_TRAIN_ROWS:
            log(f"[{name}] {a:%Y-%m} 학습 데이터 부족({len(tr)}) — 건너뜀")
            continue
        t0 = time.time()
        m = pm._new_model().fit(tr[pm.FEATURES], (tr['f24'] > 0).astype(int))
        tr4 = tr[tr['x4'].notna()]
        m4 = pm._new_reg().fit(tr4[pm.FEATURES], tr4['x4'].clip(-15, 15))
        parts.append(pd.DataFrame({'p_up': m.predict_proba(te[pm.FEATURES])[:, 1],
                                   'exp4': m4.predict(te[pm.FEATURES])}, index=te.index))
        log(f"[{name}] {a:%Y-%m} 학습 {len(tr):,}행(첫 {tr['ts'].min():%Y-%m-%d}) → 예측 {len(te):,}행 ({time.time() - t0:.0f}s)")
    return pd.concat(parts) if parts else pd.DataFrame(columns=['p_up', 'exp4'])


def simulate(P, frames, liq_block=True):
    P = P.sort_values(['ts', 'ticker'])
    raw = (P['exp4'] < EXP4_MAX) & (P['p_up'] < PUP_MAX)
    P = P.assign(raw=raw, blocked_mkt=raw & (P['mkt_r4'] >= MKT_R4_BLOCK))
    trades = []
    prev_short = {}
    track, stop_until, hold_until, liq_until = {}, {}, {}, {}
    never = pd.Timestamp('1900-01-01')
    for ts, g in P.groupby('ts', sort=True):
        for r in g.itertuples(index=False):
            tk = r.ticker
            px = r.p
            tr = track.get(tk)
            if tr:
                if (ts - tr[1]).total_seconds() > STOP_WATCH_H * 3600:
                    track.pop(tk, None)
                elif px >= tr[0] * (1 + STOP_PCT / 100):
                    stop_until[tk] = ts + pd.Timedelta(hours=STOP_BLOCK_H)
                    track.pop(tk, None)
            v_short = bool(r.raw) and not bool(r.blocked_mkt)
            raw_short = v_short
            if v_short and ts < stop_until.get(tk, never):
                v_short = False
            elif raw_short and not prev_short.get(('raw', tk)):
                track[tk] = (px, ts)
            prev_short[('raw', tk)] = raw_short
            was = prev_short.get(tk, False)
            prev_short[tk] = v_short
            if not v_short or was:
                continue
            if r.age_d < MIN_AGE_AUTO_D:
                continue
            if ts < hold_until.get(tk, never):
                continue
            if liq_block and ts < liq_until.get(tk, never):
                continue
            d = frames[tk]
            i = d.index.get_loc(ts)
            fut = d.iloc[i + 1:i + 1 + HOLD_H]
            if len(fut) < HOLD_H:
                continue
            amt = USD * (STRONG_MULT if r.exp4 <= STRONG_EXP4 else 1.0)
            adv = fut['high'].max() / px - 1
            liq = adv >= LIQ_MOVE
            if liq:
                roi = -0.9
                hit = fut.index[(fut['high'] / px - 1) >= LIQ_MOVE][0]
                hold_until[tk] = hit
                liq_until[tk] = hit + pd.Timedelta(hours=LIQ_BLOCK_H)
            else:
                roi = LEV * -(fut['close'].iloc[-1] / px - 1) - COST
                hold_until[tk] = fut.index[-1]
            trades.append({'ts': ts, 'ticker': tk, 'amount': amt, 'p_up': round(float(r.p_up), 3),
                           'exp4': round(float(r.exp4), 2), 'atr_pct': round(float(r.atr_pct), 2),
                           'r168': round(float(r.r168), 1), 'age_d': int(r.age_d),
                           'max_adverse_pct': round(adv * 100, 1), 'liq': liq, 'roi': round(roi, 4),
                           'pnl': round(roi * amt, 2)})
    return pd.DataFrame(trades)


def stats(t, split):
    if t.empty:
        return dict(n=0, win=np.nan, roi=np.nan, liq=0, liqp=np.nan, pnl=0.0, h1=0.0, h2=0.0, worst=0.0, mneg=0, mon=0)
    mon = t.groupby(t['ts'].dt.to_period('M'))['pnl'].sum()
    return dict(n=len(t), win=(t['roi'] > 0).mean() * 100, roi=t['roi'].mean() * 100, liq=int(t['liq'].sum()),
                liqp=t['liq'].mean() * 100, pnl=t['pnl'].sum(), h1=t[t['ts'] < split]['pnl'].sum(),
                h2=t[t['ts'] >= split]['pnl'].sum(), worst=mon.min(), mneg=int((mon < 0).sum()), mon=len(mon))


def row(name, s):
    if s['n'] == 0:
        return f"| {name} | 0 | – | – | – | – | – | – | – | – |"
    ok = "✅" if (s['h1'] > 0 and s['h2'] > 0 and s['liqp'] <= 6) else "❌"
    return (f"| {name} | {s['n']} | {s['win']:.0f}% | {s['roi']:+.1f}% | {s['liq']} ({s['liqp']:.1f}%) | "
            f"${s['pnl']:+,.0f} | ${s['h1']:+,.0f} / ${s['h2']:+,.0f} | ${s['worst']:+,.0f} | {s['mneg']}/{s['mon']} | {ok} |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--months', type=int, default=12)
    a = ap.parse_args()
    t0 = time.time()
    with open(os.path.join(BIN_DIR, '_symbols.json'), encoding='utf-8') as f:
        sym = json.load(f)
    onboard = {}
    for s, m in sym.items():
        if m.get('onboard'):
            onboard[m['coin']] = pd.Timestamp(int(m['onboard']), unit='ms') + pd.Timedelta(hours=9)

    log("원화 history 읽는 중")
    kf = load_dir(KRW_DIR, '*_1h.csv')
    K = krw_table(kf)
    log(f"원화 {len(kf)}종목 {len(K):,}행 (학습용, 2시간 간격)")
    log("바이낸스 history 읽는 중")
    bf = load_dir(BIN_DIR, 'binance_*_1h.csv')
    first = min(d.index[0] for d in bf.values())
    end = max(d.index[-1] for d in bf.values())
    test_start = (end.normalize() - pd.DateOffset(months=a.months)).replace(day=1)
    split = test_start + pd.DateOffset(months=a.months // 2)
    krw_coins = set(kf) & set(bf)
    log(f"바이낸스 {len(bf)}종목, 데이터 {first:%Y-%m-%d} ~ {end:%Y-%m-%d}, 시험 {test_start:%Y-%m-%d}~ (반 나눔 {split:%Y-%m-%d}), 원화 겹치는 코인 {len(krw_coins)}")
    B = binance_table(bf, onboard, krw_coins, test_start)
    log(f"바이낸스 테이블 {len(B):,}행")
    T = B[B['ts'] >= test_start]
    edges = pd.date_range(test_start, end + pd.DateOffset(months=1), freq='MS')

    preds = {'M_krw': walk_forward(T, K, edges, 'M_krw'), 'M_bin': walk_forward(T, B, edges, 'M_bin')}

    # 유니버스
    T = T.copy()
    elig = T['age_d'] >= MIN_AGE_VERDICT_D
    T['rk24'] = T['val24'].where(elig).groupby(T['ts']).rank(ascending=False, method='first')
    old = elig & (T['age_d'] >= 365)
    T['rk720o'] = T['val720'].where(old).groupby(T['ts']).rank(ascending=False, method='first')
    unis = {
        'A 24h거래대금 상위40': T['rk24'] <= 40,
        'B 상장1년↑ 30일거래대금 상위40': T['rk720o'] <= 40,
        'C 상장1년↑ 30일거래대금 상위20': T['rk720o'] <= 20,
        'D 원화 거래소 코인': elig & T['ticker'].isin(krw_coins),
    }
    res, allt = {}, []
    for un, mask in unis.items():
        for mn, pr in preds.items():
            P = T[mask].join(pr, how='inner')
            if P.empty:
                continue
            P = P.assign(mkt_r4=P.groupby('ts')['r4'].transform('mean'))
            for fn, fm in [('', None), (' · ATR<3%', 'atr')]:
                Q = P if fm is None else P[P['atr_pct'] < 3]
                tr = simulate(Q, bf)
                key = f"{un} · {mn}{fn}"
                res[key] = tr
                if not tr.empty:
                    allt.append(tr.assign(variant=key))
                s = stats(tr, split)
                log(f"{key}: {s['n']}건 청산 {s['liqp'] if s['n'] else 0:.1f}% ${s['pnl']:+,.0f}")

    os.makedirs(REPORT_DIR, exist_ok=True)
    if allt:
        pd.concat(allt).to_csv(os.path.join(REPORT_DIR, 'binance_backtest2_trades.csv'), index=False, encoding='utf-8-sig')
    lines = [f"# 바이낸스 2차 백테스트 — 바이낸스 학습 모델 · 유니버스 비교 ({datetime.now():%Y-%m-%d %H:%M})", "",
             f"데이터 {first:%Y-%m-%d} ~ {end:%Y-%m-%d} · 시험 {test_start:%Y-%m-%d} ~ (앞/뒤 나눔 {split:%Y-%m-%d}) · "
             f"판정 v2 숏 · $30·3배·48h · 강한숏 $60 · 비용 -2.1% · 역행 31.3% 청산 · 청산 코인 48h 차단 · 자동진입 상장 180일↑ · 롱 제외", "",
             "M_krw = 원화(history/)로 학습, M_bin = 바이낸스(거래대금 상위 100 + 원화 코인)로 학습 — 둘 다 월별 워크포워드", "",
             "통과(✅) = 앞 6개월·뒤 6개월 둘 다 플러스 + 청산 6% 이하", "",
             "| 유니버스 · 모델 | 진입 | 승률 | 건당 ROI | 청산 | $손익(1년) | 앞/뒤 6개월 | 최악월 | 마이너스 달 | 통과 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for k, t in res.items():
        lines.append(row(k, stats(t, split)))
    lines += ["", "참고: 1차(원화 학습·A 유니버스) $+419·청산 21.8%, 원화 history 같은 규칙 $+2,173·청산 3.9%(그중 바이낸스 선물 없는 9코인 $+1,161)", ""]
    good = [(k, stats(t, split)) for k, t in res.items()]
    good = [(k, s) for k, s in good if s['n'] and s['h1'] > 0 and s['h2'] > 0 and s['liqp'] <= 6]
    if good:
        best_k = max(good, key=lambda x: x[1]['pnl'])[0]
        tb = res[best_k]
        lines += [f"## 통과 중 1년 손익 최고: {best_k}", "", "| 월 | 진입 | 청산 | $손익 |", "|---|---|---|---|"]
        for m, g in tb.groupby(tb['ts'].dt.to_period('M')):
            lines.append(f"| {m} | {len(g)} | {int(g['liq'].sum())} | ${g['pnl'].sum():+,.1f} |")
        top = tb.groupby('ticker')['pnl'].agg(['count', 'sum']).sort_values('sum')
        lines += ["", "손실 큰 코인 5: " + ", ".join(f"{i} {r['count']:.0f}건 ${r['sum']:+.0f}" for i, r in top.head(5).iterrows()),
                  "수익 큰 코인 5: " + ", ".join(f"{i} {r['count']:.0f}건 ${r['sum']:+.0f}" for i, r in top.tail(5)[::-1].iterrows())]
    else:
        lines += ["## 통과한 조합 없음"]
    lines += ["", f"소요 {time.time() - t0:.0f}초"]
    out = os.path.join(REPORT_DIR, f"binance_backtest2_{datetime.now():%Y%m%d}.md")
    with open(out, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines) + "\n")
    log(f"결과 저장: {out}")
    print("\n".join(lines))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
