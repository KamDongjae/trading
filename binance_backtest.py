# -*- coding: utf-8 -*-
"""
binance_backtest.py
-------------------
[2026-11-23 신설, 사용자 요청] 바이낸스 탭을 붙이기 전 검증용 1년 백테스트.
지금 판정 규칙(판정 v2 숏)과 자동진입 규칙을 바이낸스 선물 1시간봉에 그대로 적용해서
빗썸/업비트 원화 1시간봉(history/)에 적용한 결과와 비교한다.

데이터
  - 바이낸스: history_binance/binance_{코인}_1h.csv (binance_history_downloader.py)
  - 원화:     history/{업비트|빗썸}_{코인}_1h.csv (history_downloader.py)
모델(워크포워드, 미래 정보 안 씀)
  - 매달 초, 그 시점 3일 전까지의 원화 history로 prob_model 레시피(p_up 24h 분류 + exp4 4h 회귀)를 새로 학습해
    그 달 예측. 지금 서버가 원화 데이터로 학습한 모델을 쓰는 것과 같은 조건.
판정(서버 apply_prob_filter 판정 v2 숏과 같게)
  - 숏: exp4 < -0.5 그리고 p_up < 0.45, 단 유니버스 평균 4h 등락 ≥ +1%면 대기
  - 숏 손절차단: 숏 판정 시작가 대비 24h 안에 +3%면 그 코인 숏 24h 차단
  - 유니버스: 매시 24h 거래대금 상위 TOP_N(서버처럼 40개), 상장 7일 이상
  - 롱은 판정에 옛 점수식(롱점수·매집+조건식)이 필요해 이 백테스트에선 제외(하락장 롱 제외라 최근 진입도 0건)
자동진입(클라 기본값)
  - 숏 판정이 새로 뜨면 진입, 같은 코인 보유 중이면 건너뜀, 상장 180일 미만 건너뜀
  - $30·3배·48h 정리, 강한 숏(exp4 ≤ -1.0) $60, 슬리피지+수수료 -2.1%(증거금 대비), 48h 안 고가가 +31.3% 넘으면 청산(-90%)
  - 강제청산 코인 같은 방향 48h 차단(11/23 추가분) 적용/미적용 둘 다 계산
결과: reports/binance_backtest_YYYYMMDD.md, reports/binance_backtest_trades.csv(gitignore *.csv)

사용법: run_binance_backtest.bat 더블클릭 (또는 python binance_backtest.py [--months 12])
"""
import os
import sys
import glob
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

TOP_N = 40
MIN_AGE_VERDICT_H = 7 * 24
MIN_AGE_AUTO_H = 180 * 24
EXP4_MAX, PUP_MAX, MKT_R4_BLOCK = -0.5, 0.45, 1.0
STOP_PCT, STOP_WATCH_H, STOP_BLOCK_H = 3.0, 24, 24
USD, LEV, HOLD_H = 30.0, 3, 48
STRONG_EXP4, STRONG_MULT = -1.0, 2.0
COST = 0.021
LIQ_MOVE = 0.313
LIQ_BLOCK_H = 48


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_dir(d, pattern):
    frames = {}
    for f in sorted(glob.glob(os.path.join(d, pattern))):
        base = os.path.basename(f)
        src, tk = base.split('_', 2)[:2]
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


def feature_table(frames, universe_top=None):
    keep = None
    if universe_top:
        # 매시 24h 거래대금 상위 N(상장 7일 이상)을 먼저 정해서, 그 행만 피처를 남긴다(500종목 메모리 절약)
        V = pd.DataFrame({tk: d['value'].rolling(24, min_periods=1).sum()
                          .where(np.arange(len(d)) >= max(MIN_AGE_VERDICT_H, 200)) for tk, d in frames.items()})
        keep = V.rank(axis=1, ascending=False, method='first') <= universe_top
        if 'BTC' in keep.columns:
            keep['BTC'] = V['BTC'].notna()
    out = []
    for tk, d in frames.items():
        if keep is not None:
            mk = keep[tk].reindex(d.index).fillna(False).astype(bool)
            if not mk.any():
                continue
        f = pm.build_features(d)
        f['ticker'] = tk
        f['age_h'] = np.arange(len(d), dtype=float)
        f['val24'] = d['value'].rolling(24, min_periods=1).sum()
        f['high'] = d['high']
        f['f24'] = (d['close'].shift(-24) / d['close'] - 1) * 100
        f['f4'] = (d['close'].shift(-4) / d['close'] - 1) * 100
        if keep is not None:
            f = f[mk.values]
        f = f[f['age_h'] >= 200]
        out.append(f)
    X = pd.concat(out).reset_index()
    X = X.rename(columns={X.columns[0]: 'ts'})
    X = X[~X['_stale']]
    if universe_top:
        X = X[X['age_h'] >= MIN_AGE_VERDICT_H]
        X['_rk'] = X.groupby('ts')['val24'].rank(ascending=False, method='first')
        X = X[(X['_rk'] <= universe_top) | (X['ticker'] == 'BTC')]
    X = pm.add_cross_section(X)
    X['x24'] = X['f24'] - X.groupby('ts')['f24'].transform('mean')
    X['x4'] = X['f4'] - X.groupby('ts')['f4'].transform('mean')
    X['mkt_r4'] = X.groupby('ts')['r4'].transform('mean')
    if universe_top:
        X = X[(X['_rk'] <= universe_top)]
    return X


def walk_forward_predict(T, K, months):
    """T: 테스트 테이블, K: 학습용 원화 테이블. 달마다 그 전 데이터로 학습해 예측."""
    start = T['ts'].max().normalize() - pd.DateOffset(months=months)
    edges = pd.date_range(start.replace(day=1), T['ts'].max() + pd.DateOffset(months=1), freq='MS')
    parts = []
    L = K[K['f24'].notna()]
    L = L[L['ts'].dt.hour % 2 == 0]
    for a, b in zip(edges[:-1], edges[1:]):
        te = T[(T['ts'] >= a) & (T['ts'] < b)]
        if te.empty:
            continue
        tr = L[L['ts'] < a - pd.Timedelta(days=3)]
        if len(tr) < 20000:
            log(f"{a:%Y-%m} 학습 데이터 부족({len(tr)}) — 건너뜀")
            continue
        t0 = time.time()
        m = pm._new_model().fit(tr[pm.FEATURES], (tr['f24'] > 0).astype(int))
        tr4 = tr[tr['x4'].notna()]
        m4 = pm._new_reg().fit(tr4[pm.FEATURES], tr4['x4'].clip(-15, 15))
        te = te.assign(p_up=m.predict_proba(te[pm.FEATURES])[:, 1], exp4=m4.predict(te[pm.FEATURES]))
        parts.append(te)
        log(f"{a:%Y-%m} 학습 {len(tr):,}행 → 예측 {len(te):,}행 ({time.time() - t0:.0f}s)")
    return pd.concat(parts) if parts else pd.DataFrame()


def simulate(P, frames, liq_block=True):
    """P: 예측 붙은 유니버스 행. 판정 → 자동진입 → 48h 정리 시뮬. 거래 목록 반환."""
    P = P.sort_values(['ts', 'ticker'])
    raw = (P['exp4'] < EXP4_MAX) & (P['p_up'] < PUP_MAX)
    P = P.assign(raw=raw, blocked_mkt=raw & (P['mkt_r4'] >= MKT_R4_BLOCK))
    trades = []
    prev_short = {}
    track = {}       # tk -> (판정 시작가, ts)
    stop_until = {}  # tk -> ts
    hold_until = {}  # tk -> ts
    liq_until = {}   # tk -> ts
    for ts, g in P.groupby('ts', sort=True):
        for r in g.itertuples(index=False):
            tk = r.ticker
            px = r.p
            # 손절차단 추적(서버 _short_stop_apply와 같은 순서)
            tr = track.get(tk)
            if tr:
                if (ts - tr[1]).total_seconds() > STOP_WATCH_H * 3600:
                    track.pop(tk, None)
                elif px >= tr[0] * (1 + STOP_PCT / 100):
                    stop_until[tk] = ts + pd.Timedelta(hours=STOP_BLOCK_H)
                    track.pop(tk, None)
            v_short = bool(r.raw) and not bool(r.blocked_mkt)
            raw_short = v_short
            if v_short and ts < stop_until.get(tk, ts - pd.Timedelta(hours=1)):
                v_short = False
            elif raw_short and not prev_short.get(('raw', tk)):
                track[tk] = (px, ts)
            prev_short[('raw', tk)] = raw_short
            was = prev_short.get(tk, False)
            prev_short[tk] = v_short
            if not v_short or was:
                continue
            # 자동진입
            if r.age_h < MIN_AGE_AUTO_H:
                continue
            if ts < hold_until.get(tk, ts - pd.Timedelta(hours=1)):
                continue
            if liq_block and ts < liq_until.get(tk, ts - pd.Timedelta(hours=1)):
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
            trades.append({'ts': ts, 'ticker': tk, 'amount': amt, 'p_up': round(r.p_up, 3), 'exp4': round(r.exp4, 2),
                           'atr_pct': round(r.atr_pct, 2), 'r168': round(r.r168, 1), 'max_adverse_pct': round(adv * 100, 1),
                           'liq': liq, 'roi': round(roi, 4), 'pnl': round(roi * amt, 2)})
    return pd.DataFrame(trades)


def summarize(t, name):
    if t.empty:
        return f"| {name} | 0 | – | – | – | – | – | – |"
    months = max(1, (t['ts'].max() - t['ts'].min()).days / 30.4)
    mon = t.groupby(t['ts'].dt.to_period('M'))['pnl'].sum()
    return (f"| {name} | {len(t)} | {(t['roi'] > 0).mean() * 100:.0f}% | {t['roi'].mean() * 100:+.1f}% | "
            f"{int(t['liq'].sum())} ({t['liq'].mean() * 100:.1f}%) | ${t['pnl'].sum():+,.0f} | "
            f"${mon.min():+,.0f} | {len(t) / months:.1f} |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--months', type=int, default=12)
    ap.add_argument('--bin-dir', default=BIN_DIR)
    ap.add_argument('--krw-dir', default=KRW_DIR)
    a = ap.parse_args()
    t0 = time.time()
    log("원화 history 읽는 중")
    kf = load_dir(a.krw_dir, '*_1h.csv')
    K = feature_table(kf)
    log(f"원화 {len(kf)}종목 {len(K):,}행")
    log("바이낸스 history 읽는 중")
    bf = load_dir(a.bin_dir, 'binance_*_1h.csv')
    B = feature_table(bf, universe_top=TOP_N)
    log(f"바이낸스 {len(bf)}종목, 유니버스 행 {len(B):,}")
    KU = feature_table(kf, universe_top=TOP_N)

    PB = walk_forward_predict(B, K, a.months)
    PK = walk_forward_predict(KU, K, a.months)
    res = {}
    for nm, P, fr in [('binance', PB, bf), ('krw', PK, kf)]:
        res[nm] = simulate(P, fr, liq_block=True)
        res[nm + '_noblock'] = simulate(P, fr, liq_block=False)

    os.makedirs(REPORT_DIR, exist_ok=True)
    allt = pd.concat([v.assign(src=k) for k, v in res.items() if not v.empty])
    allt.to_csv(os.path.join(REPORT_DIR, 'binance_backtest_trades.csv'), index=False, encoding='utf-8-sig')
    tb = res['binance']
    lines = [f"# 바이낸스 선물 1년 백테스트 ({datetime.now():%Y-%m-%d %H:%M})", "",
             f"판정 v2 숏(exp4<{EXP4_MAX}·p_up<{PUP_MAX}·시장4h≥+{MKT_R4_BLOCK}%면 대기·+{STOP_PCT}% 손절차단) · "
             f"유니버스 24h 거래대금 상위 {TOP_N} · 자동진입 상장 180일↑ · ${USD:g}·{LEV}배·{HOLD_H}h · 강한숏 ${USD * STRONG_MULT:g} · "
             f"비용 -{COST * 100:.1f}% · 역행 {LIQ_MOVE * 100:.1f}%↑ 청산 · 모델 월별 워크포워드(원화 history로 학습) · 롱 제외", "",
             f"기간: {tb['ts'].min() if not tb.empty else '-'} ~ {tb['ts'].max() if not tb.empty else '-'}", "",
             "| 데이터 | 진입 | 승률 | 건당 ROI | 청산 | $손익 | 최악월 | 월 진입 |",
             "|---|---|---|---|---|---|---|---|",
             summarize(res['binance'], '바이낸스 (청산 48h 차단)'),
             summarize(res['binance_noblock'], '바이낸스 (차단 없음)'),
             summarize(res['krw'], '원화 history (청산 48h 차단)'),
             summarize(res['krw_noblock'], '원화 history (차단 없음)'), ""]
    if not tb.empty:
        lines += ["## 바이낸스 월별", "", "| 월 | 진입 | 청산 | $손익 |", "|---|---|---|---|"]
        for m, g in tb.groupby(tb['ts'].dt.to_period('M')):
            lines.append(f"| {m} | {len(g)} | {int(g['liq'].sum())} | ${g['pnl'].sum():+,.1f} |")
        lines += ["", "## 바이낸스 청산 목록", ""]
        for r in tb[tb['liq']].itertuples():
            lines.append(f"- {r.ts:%Y-%m-%d %H:%M} {r.ticker} ${r.amount:g} p_up {r.p_up} exp4 {r.exp4} ATR {r.atr_pct}% 7일 {r.r168:+.1f}%")
        lines += ["", "## 바이낸스 ATR 구간별", "", "| ATR% | 진입 | 건당 ROI | 청산 |", "|---|---|---|---|"]
        for lo, hi in [(0, 1), (1, 2), (2, 3), (3, 99)]:
            g = tb[(tb['atr_pct'] >= lo) & (tb['atr_pct'] < hi)]
            if len(g):
                lines.append(f"| {lo}~{hi} | {len(g)} | {g['roi'].mean() * 100:+.1f}% | {int(g['liq'].sum())} |")
    lines += ["", f"소요 {time.time() - t0:.0f}초"]
    out = os.path.join(REPORT_DIR, f"binance_backtest_{datetime.now():%Y%m%d}.md")
    with open(out, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines) + "\n")
    log(f"결과 저장: {out}")
    print("\n".join(lines))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
