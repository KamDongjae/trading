# -*- coding: utf-8 -*-
"""
binance_backtest3.py
--------------------
[2026-11-25 신설, 사용자 요청] 바이낸스 선물 '전체'에서 밈·급변·신규상장을 걸러낸 대상 코인으로 판정 성적 검증.
(바이낸스 탭은 지금 원화 거래소 코인 28개만 봄 → 바이낸스 전체로 넓히기 위한 필터 기준 검증)

대상 코인(매일 09시 KST 갱신, 전날까지 데이터로 계산):
  - USDT 무기한(_symbols.json = 지금 거래 중) · 스테이블 제외 · 1000PEPE 같은 배수 코인 제외
  - 상장 AGE일 이상(onboardDate) — 365 / 730 비교
  - 밈 제외(바이낸스 exchangeInfo underlyingSubType 에 'Meme') — 있음/없음 비교
  - 급변 이력 제외: 최근 30일 중 하루 종가 변화 ±30% 이상인 날이 있으면 제외
  - 30일 일거래대금 중앙값 상위 40, 이미 들어간 코인은 60위 안이면 유지
진입 필터(신호 단위): 1h ATR < 3/4/5%/없음 × 숏은 7일 상승률 < 30/50%/없음
모델: M_krw(원화 history 학습) / M_bin(바이낸스 대상 코인 풀로 학습) — 월별 워크포워드(그 달 3일 전까지)
규칙: 판정 v2 숏(exp4<-0.5 · p_up<0.45 · 시장 4h≥+1% 대기 · 숏 손절 차단) · $30·3배·48h · 강한숏(exp4≤-1) $60 ·
      비용 -2.1% · 역행 31.3% 청산 · 청산 코인 7일 차단 · 같은 코인 보유 중 건너뜀
롱(근사): 점수 조건 없이 모델만(p_up≥0.55 · exp4>0.2), 대상 코인 평균 24h<0이면 대기 — 과거 OI·CVD가 없어 실제 판정과 다름
통과: 앞 6개월·뒤 6개월 둘 다 + · 청산 6% 이하
결과: reports/binance_backtest3_YYYYMMDD.md, reports/binance_backtest3_trades.csv
"""
import os
import sys
import json
import time
import argparse
from datetime import datetime

import numpy as np
import pandas as pd
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import prob_model as pm  # noqa: E402
import binance_backtest2 as b2  # noqa: E402

BIN_DIR = b2.BIN_DIR
KRW_DIR = b2.KRW_DIR
REPORT_DIR = b2.REPORT_DIR
log = b2.log

STABLES = {"USDT", "USDC", "FDUSD", "TUSD", "BUSD", "USD1", "USDP", "DAI", "USDE", "RLUSD", "PYUSD", "XUSD", "EUR", "BFUSD"}
TOP_N, KEEP_N = 40, 60
SHOCK_PCT = 30.0
USD, LEV, HOLD_H = 30.0, 3, 48
STRONG_EXP4, STRONG_MULT = -1.0, 2.0
COST, LIQ_MOVE = 0.021, 0.313
LIQ_BLOCK_H = 168
EXP4_MAX, PUP_MAX, MKT_R4_BLOCK = -0.5, 0.45, 1.0
STOP_PCT, STOP_WATCH_H, STOP_BLOCK_H = 3.0, 24, 24
LONG_PUP, LONG_EXP4 = 0.55, 0.2


def fetch_subtypes():
    path = os.path.join(BIN_DIR, "_subtypes.json")
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=20)
        r.raise_for_status()
        out = {}
        for s in r.json().get("symbols", []):
            if s.get("quoteAsset") == "USDT" and s.get("contractType") == "PERPETUAL":
                out[s["baseAsset"].upper()] = list(s.get("underlyingSubType") or []) + [str(s.get("underlyingType") or "")]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        return out
    except Exception as e:
        log(f"exchangeInfo 조회 실패({e}) — 저장된 파일 사용")
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}


def is_multiplier(c):
    return c[:1].isdigit() and any(c.startswith(p) for p in ("1000000", "10000", "1000", "100", "1M"))


def daily_universe(frames, onboard, age_d, meme_set, use_meme):
    """매일(KST 날짜) 대상 코인 집합. 그 날 09시부터 다음 날 09시 전까지 적용, 계산은 전날까지 데이터."""
    dv, dc = {}, {}
    for tk, d in frames.items():
        g = d.resample("1D")
        dv[tk] = g["value"].sum()
        dc[tk] = g["close"].last()
    V = pd.DataFrame(dv).sort_index()
    C = pd.DataFrame(dc).sort_index()
    med = V.rolling(30, min_periods=20).median().shift(1)
    ret = C.pct_change().abs().mul(100)
    shock = ret.rolling(30, min_periods=1).max().shift(1) >= SHOCK_PCT
    days = V.index
    age = pd.DataFrame({tk: (days - onboard.get(tk, frames[tk].index[0])).days for tk in V.columns}, index=days)
    bad = {tk for tk in V.columns if tk in STABLES or is_multiplier(tk) or (use_meme and tk in meme_set)}
    elig = (age >= age_d) & ~shock & med.notna()
    for tk in bad:
        if tk in elig.columns:
            elig[tk] = False
    rank = med.where(elig).rank(axis=1, ascending=False, method="first")
    members, out = set(), {}
    for day in days:
        r = rank.loc[day]
        keep = {t for t in members if r.get(t, np.nan) <= KEEP_N}
        new = list(r[r <= TOP_N].sort_values().index)
        for t in new:
            if len(keep) >= TOP_N:
                break
            keep.add(t)
        members = keep
        out[day] = frozenset(members)
    return out


def membership_mask(index_ts, tickers, uni):
    """행별(ts, ticker) 대상 여부. 09시 전은 전날 목록."""
    day = (pd.DatetimeIndex(index_ts) - pd.Timedelta(hours=9)).normalize()
    return np.array([t in uni.get(dd, ()) for dd, t in zip(day, tickers)], dtype=bool)


def build_table(frames, onboard, pool_mask_fn, test_start):
    out = []
    for tk, d in frames.items():
        mk = pool_mask_fn(tk, d.index)
        mk &= (d.index.hour % 2 == 0) | (d.index >= test_start)
        if not mk.any():
            continue
        f = pm.build_features(d)
        f['ticker'] = tk
        ob = onboard.get(tk, d.index[0])
        f['age_d'] = (d.index - ob).total_seconds() / 86400
        f['f24'] = (d['close'].shift(-24) / d['close'] - 1) * 100
        f['f4'] = (d['close'].shift(-4) / d['close'] - 1) * 100
        out.append(f[mk])
    X = pd.concat(out).reset_index()
    X = X.rename(columns={X.columns[0]: 'ts'})
    X = X[~X['_stale']]
    for c in pm.FEATURES:
        if c in X.columns:
            X[c] = X[c].astype('float32')
    return b2._finish(X)


def short_events(P):
    """판정 v2 숏 상태머신 → '새로 뜬 판정' 이벤트 목록(진입 필터 적용 전)."""
    P = P.sort_values(['ts', 'ticker'])
    raw = (P['exp4'] < EXP4_MAX) & (P['p_up'] < PUP_MAX) & ~(P['mkt_r4'] >= MKT_R4_BLOCK)
    P = P.assign(raw=raw.values)
    ev = []
    prev, prev_raw, track, stop_until = {}, {}, {}, {}
    never = pd.Timestamp('1900-01-01')
    for r in P.itertuples(index=False):
        tk, ts, px = r.ticker, r.ts, r.p
        tr = track.get(tk)
        if tr:
            if (ts - tr[1]).total_seconds() > STOP_WATCH_H * 3600:
                track.pop(tk, None)
            elif px >= tr[0] * (1 + STOP_PCT / 100):
                stop_until[tk] = ts + pd.Timedelta(hours=STOP_BLOCK_H)
                track.pop(tk, None)
        v = bool(r.raw)
        if v and ts < stop_until.get(tk, never):
            v = False
        elif r.raw and not prev_raw.get(tk):
            track[tk] = (px, ts)
        prev_raw[tk] = bool(r.raw)
        was = prev.get(tk, False)
        prev[tk] = v
        if v and not was:
            ev.append((ts, tk, 'short', float(r.exp4), float(r.p_up), float(r.atr_pct), float(r.r168), float(r.age_d)))
    return ev


def long_events(P):
    P = P.sort_values(['ts', 'ticker'])
    raw = (P['p_up'] >= LONG_PUP) & (P['exp4'] > LONG_EXP4) & (P['u_r24'] >= 0)
    P = P.assign(raw=raw.values)
    ev, prev = [], {}
    for r in P.itertuples(index=False):
        v = bool(r.raw)
        if v and not prev.get(r.ticker, False):
            ev.append((r.ts, r.ticker, 'long', float(r.exp4), float(r.p_up), float(r.atr_pct), float(r.r168), float(r.age_d)))
        prev[r.ticker] = v
    return ev


def run_entries(events, frames, atr_max=None, r168_max=None):
    trades = []
    hold_until, liq_until = {}, {}
    never = pd.Timestamp('1900-01-01')
    for ts, tk, side, exp4, pup, atr, r168, age in sorted(events):
        if atr_max is not None and not (atr < atr_max):
            continue
        if side == 'short' and r168_max is not None and not (r168 < r168_max):
            continue
        if ts < hold_until.get(tk, never) or ts < liq_until.get((tk, side), never):
            continue
        d = frames[tk]
        i = d.index.get_loc(ts)
        px = float(d['close'].iloc[i])
        fut = d.iloc[i + 1:i + 1 + HOLD_H]
        if len(fut) < HOLD_H:
            continue
        amt = USD * (STRONG_MULT if (side == 'short' and exp4 <= STRONG_EXP4) else 1.0)
        if side == 'short':
            adv = fut['high'].max() / px - 1
            hitmask = (fut['high'] / px - 1) >= LIQ_MOVE
            move = -(fut['close'].iloc[-1] / px - 1)
        else:
            adv = 1 - fut['low'].min() / px
            hitmask = (1 - fut['low'] / px) >= LIQ_MOVE
            move = fut['close'].iloc[-1] / px - 1
        liq = adv >= LIQ_MOVE
        if liq:
            roi = -0.9
            hit = fut.index[hitmask][0]
            hold_until[tk] = hit
            liq_until[(tk, side)] = hit + pd.Timedelta(hours=LIQ_BLOCK_H)
        else:
            roi = LEV * move - COST
            hold_until[tk] = fut.index[-1]
        trades.append({'ts': ts, 'ticker': tk, 'side': side, 'amount': amt, 'exp4': round(exp4, 2), 'p_up': round(pup, 3),
                       'atr_pct': round(atr, 2), 'r168': round(r168, 1), 'age_d': int(age),
                       'max_adverse_pct': round(adv * 100, 1), 'liq': liq, 'roi': round(roi, 4), 'pnl': round(roi * amt, 2)})
    return pd.DataFrame(trades)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--months', type=int, default=12)
    a = ap.parse_args()
    t0 = time.time()
    with open(os.path.join(BIN_DIR, '_symbols.json'), encoding='utf-8') as f:
        sym = json.load(f)
    onboard = {m['coin']: pd.Timestamp(int(m['onboard']), unit='ms') + pd.Timedelta(hours=9)
               for m in sym.values() if m.get('onboard')}
    sub = fetch_subtypes()
    meme_set = {c for c, v in sub.items() if any('meme' in str(x).lower() for x in v)}
    log(f"밈 분류 코인 {len(meme_set)}개: {', '.join(sorted(meme_set)[:40])}{' …' if len(meme_set) > 40 else ''}")

    log("원화 history 읽는 중")
    kf = b2.load_dir(KRW_DIR, '*_1h.csv')
    K = b2.krw_table(kf)
    log("바이낸스 history 읽는 중")
    bf = b2.load_dir(BIN_DIR, 'binance_*_1h.csv')
    end = max(d.index[-1] for d in bf.values())
    test_start = (end.normalize() - pd.DateOffset(months=a.months)).replace(day=1)
    split = test_start + pd.DateOffset(months=a.months // 2)
    log(f"바이낸스 {len(bf)}종목 · 시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d} (반 나눔 {split:%Y-%m-%d})")

    unis = {}
    for age in (365, 730):
        for use_meme in (True, False):
            key = f"상장{age // 365}년↑ · {'밈 제외' if use_meme else '밈 포함'}"
            unis[key] = daily_universe(bf, onboard, age, meme_set, use_meme)
            sizes = [len(v) for k, v in unis[key].items() if k >= test_start]
            names = set().union(*[v for k, v in unis[key].items() if k >= test_start])
            log(f"[{key}] 하루 평균 {np.mean(sizes):.1f}개 · 시험기간 거쳐간 코인 {len(names)}개")
    pool_days = {}
    for u in unis.values():
        for day, s in u.items():
            pool_days[day] = pool_days.get(day, frozenset()) | s
    pool_set = {}
    for day, s in pool_days.items():
        for t in s:
            pool_set.setdefault(t, set()).add(day)

    def pool_mask(tk, idx):
        days = pool_set.get(tk)
        if not days:
            return np.zeros(len(idx), dtype=bool)
        dd = (idx - pd.Timedelta(hours=9)).normalize()
        return np.isin(dd.values, np.array(sorted(days), dtype='datetime64[ns]'))

    B = build_table(bf, onboard, pool_mask, test_start)
    log(f"바이낸스 풀 테이블 {len(B):,}행 · {B['ticker'].nunique()}종목")
    T = B[B['ts'] >= test_start].copy()
    edges = pd.date_range(test_start, end + pd.DateOffset(months=1), freq='MS')
    preds = {'M_krw': b2.walk_forward(T, K, edges, 'M_krw'), 'M_bin': b2.walk_forward(T, B, edges, 'M_bin')}

    ATRS = [None, 5, 4, 3]
    R168S = [None, 50, 30]
    rows, allt = [], []
    for un, uni in unis.items():
        mask = membership_mask(T['ts'], T['ticker'].values, uni)
        Tu = T[mask]
        for mn, pr in preds.items():
            P = Tu.join(pr, how='inner')
            if P.empty:
                continue
            P = P.assign(mkt_r4=P.groupby('ts')['r4'].transform('mean'),
                         u_r24=P.groupby('ts')['r24'].transform('mean'))
            sev = short_events(P)
            lev_ = long_events(P)
            for atr in ATRS:
                for r168 in R168S:
                    tr = run_entries(sev, bf, atr, r168)
                    key = (un, mn, 'short', atr, r168)
                    rows.append((key, tr))
                    if not tr.empty:
                        allt.append(tr.assign(universe=un, model=mn, atr_max=atr, r168_max=r168))
            for atr in ATRS:
                tr = run_entries(lev_, bf, atr, None)
                rows.append(((un, mn, 'long', atr, None), tr))
                if not tr.empty:
                    allt.append(tr.assign(universe=un, model=mn, atr_max=atr, r168_max=None))
            log(f"{un} · {mn}: 숏 판정 {len(sev)}회 · 롱(근사) {len(lev_)}회")

    os.makedirs(REPORT_DIR, exist_ok=True)
    if allt:
        pd.concat(allt).to_csv(os.path.join(REPORT_DIR, 'binance_backtest3_trades.csv'), index=False, encoding='utf-8-sig')

    def label(k):
        un, mn, side, atr, r168 = k
        f = []
        if atr:
            f.append(f"ATR<{atr}%")
        if r168:
            f.append(f"7일<{r168}%")
        return f"{un} · {mn} · {' · '.join(f) if f else '진입필터 없음'}"

    def tab(side):
        res = [(label(k), b2.stats(t, split)) for k, t in rows if k[2] == side]
        res.sort(key=lambda x: (0 if (x[1]['n'] > 0 and x[1]['h1'] > 0 and x[1]['h2'] > 0 and x[1]['liqp'] <= 6) else 1, -float(x[1]['pnl'])))
        return res

    lines = [f"# 바이낸스 전체 필터 검증 ({datetime.now():%Y-%m-%d %H:%M})", "",
             f"데이터 바이낸스 1h봉 · 시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d} (앞/뒤 나눔 {split:%Y-%m-%d}) · $30·3배·48h · 강한숏 $60 · 비용 -2.1% · 역행 31.3% 청산 · 청산 코인 7일 차단",
             f"대상 코인: 스테이블·배수코인 제외, 상장 1년/2년↑, 밈(바이낸스 분류 {len(meme_set)}개) 제외/포함, 최근 30일 하루 ±{SHOCK_PCT:g}% 이상 변동 이력 제외, 30일 거래대금 중앙값 상위 {TOP_N}(이미 들어간 건 {KEEP_N}위까지 유지), 매일 09시 갱신",
             "통과(✅) = 앞·뒤 6개월 둘 다 + · 청산 6% 이하. 지금 거래 중인 종목만 데이터가 있음(상장폐지 코인 빠짐 → 결과가 약간 좋게 나올 수 있음)", "",
             "## 숏 (판정 v2 그대로)", "",
             "| 대상 · 모델 · 진입 필터 | 진입 | 승률 | 건당 ROI | 청산 | $손익(1년) | 앞/뒤 6개월 | 최악월 | 마이너스 달 | 통과 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for name, s in tab('short'):
        lines.append(b2.row(name, s))
    lines += ["", "## 롱 (근사 — 점수 조건 없이 모델 p_up≥0.55·exp4>0.2, 대상 평균 24h<0이면 대기)", "",
              "| 대상 · 모델 · 진입 필터 | 진입 | 승률 | 건당 ROI | 청산 | $손익(1년) | 앞/뒤 6개월 | 최악월 | 마이너스 달 | 통과 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for name, s in tab('long'):
        lines.append(b2.row(name, s))
    lines += ["", "참고(이전 검증): 원화 거래소 코인 28개·원화 모델 숏 $+564·청산 5.9%(7일 차단 근사 $+613·4.7%) / 바이낸스 24h 거래대금 상위40 원화 모델 -$609·청산 21.2%", ""]
    best = [(k, t) for k, t in rows if k[2] == 'short' and not t.empty]
    best = [(k, t, b2.stats(t, split)) for k, t in best]
    best = [x for x in best if x[2]['h1'] > 0 and x[2]['h2'] > 0 and x[2]['liqp'] <= 6]
    if best:
        k, tb, s = max(best, key=lambda x: x[2]['pnl'])
        lines += [f"## 통과 중 숏 1년 손익 최고: {label(k)}", "", "| 월 | 진입 | 청산 | $손익 |", "|---|---|---|---|"]
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
        lines += ["## 숏 통과 조합 없음"]
    lines += ["", f"밈 분류 코인: {', '.join(sorted(meme_set))}", "", f"소요 {time.time() - t0:.0f}초"]
    out = os.path.join(REPORT_DIR, f"binance_backtest3_{datetime.now():%Y%m%d}.md")
    with open(out, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines) + "\n")
    log(f"결과 저장: {out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
