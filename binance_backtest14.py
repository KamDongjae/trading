# -*- coding: utf-8 -*-
"""
binance_backtest14.py
---------------------
[2026-11-26 신설, 사용자 요청] 바이낸스 V3 — 숏을 서버 규칙과 하나씩 맞춰 보고, 롱도 검증.
대상: 필터 후 24h 거래대금 1-300위 (bt13과 같음) · 모델 V3 (예측은 bt13과 같은 방식, RSI·24h 등락·REF 시장 24h 평균 열 추가해서 다시 만듦)
가드: 상장 730일↑ · 1h ATR<2.5 · 7일<+50% (판정이 새로 뜬 순간 기준 — 백테스트 정의)
숏(판정 v2: exp4<-0.5 · p_up<0.45 · REF 시장 4h≥+1% 대기 · 숏 손절 차단):
  S0 기준(bt13 추천과 같아야 함: 327건 +$1,063)
  S1 + RSI≥50 규칙(11/25 서버 공통 규칙)
  S2 + 클라 재신호 가드(같은 코인·방향 판정을 6시간 안에 본 적 있으면 다시 떠도 새 신호 아님)
  S3 가드를 '매 시각'(판정이 이어지는 동안 가드가 풀리면 그때 진입 — 11/26 0시 서버 방식)
롱(판정 v2 롱의 모델 부분: p_up≥0.55 · exp4>+0.2. 서버는 여기에 점수 롱 후보 조건이 더 붙음 — 과거 OI·롱숏비가 없어 재현 불가, 그래서 이 결과는 상한 근사):
  L0 모델 조건만 · L1 + 하락장 대기 근사(REF 시장 24h 평균 ≤ 0 이면 대기) · L2 L0인데 가드 없음(비교)
공통: $30(강한숏 exp4≤-1 $60) · 3배 · 48h · 비용 4.2% · 역행 31.3% 강제청산(같은 코인 7일 차단)
통과: 앞뒤 6개월 + · 청산 ≤6%
결과: reports/binance_backtest14_YYYYMMDD.md, reports/binance_backtest14_trades.csv
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
PRED_PKL = os.path.join(CACHE_DIR, 'binance_v3_preds_1-300_v2.pkl')
FULL = (1, 300)
AGE_MIN, ATR_MAX, R7_MAX = 730, 2.5, 50.0
LONG_PUP, LONG_EXP4 = 0.55, 0.2
RSI_MIN = 50
RESIGNAL_H = 6


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
    cols = ['ts', 'ticker', 'p', 'atr_pct', 'r168', 'age_d', 'mkt_r4', 'mkt_r24', 'rsi', 'r24']
    P = X.loc[pr3.index, cols].join(pr3)
    os.makedirs(CACHE_DIR, exist_ok=True)
    P.to_pickle(PRED_PKL)
    log(f"V3 예측 저장: {PRED_PKL} ({len(P):,}행)")
    return P


def events(P, side, rsi_min=0, bear_proxy=False, guard_mode='start', guard=True):
    """판정 상태머신(코인별 시간순). 반환: 새로 뜬 판정 목록 (ts, tk, side, exp4, pup, atr, r168, age).
    guard_mode='start': 판정이 뜬 순간 가드에 걸리면 그 판정이 끝날 때까지 진입 없음(백테스트 기준 정의)
    guard_mode='any'  : 판정이 이어지는 동안 가드가 풀리는 첫 시각에 새 신호(가드 대기 → 판정으로 바뀌는 서버 표시 방식)"""
    P = P.sort_values(['ts', 'ticker'])
    if side == 'short':
        raw = (P['exp4'] < b3.EXP4_MAX) & (P['p_up'] < b3.PUP_MAX) & ~(P['mkt_r4'] >= b3.MKT_R4_BLOCK)
        if rsi_min:
            raw &= P['rsi'] >= rsi_min
    else:
        raw = (P['p_up'] >= LONG_PUP) & (P['exp4'] > LONG_EXP4)
        if bear_proxy:
            raw &= P['mkt_r24'] > 0
    gd = (P['age_d'] >= AGE_MIN) & (P['atr_pct'] < ATR_MAX) & (P['r168'] < R7_MAX) if guard else pd.Series(True, index=P.index)
    P = P.assign(raw=raw.values, gd=gd.values)
    ev = []
    prev, prev_raw, track, stop_until, blocked = {}, {}, {}, {}, {}
    never = pd.Timestamp('1900-01-01')
    for r in P.itertuples(index=False):
        tk, ts, px = r.ticker, r.ts, r.p
        v = bool(r.raw)
        if side == 'short':   # 숏 손절 차단(서버 _short_stop_apply 와 같음)
            tr = track.get(tk)
            if tr:
                if (ts - tr[1]).total_seconds() > b3.STOP_WATCH_H * 3600:
                    track.pop(tk, None)
                elif px >= tr[0] * (1 + b3.STOP_PCT / 100):
                    stop_until[tk] = ts + pd.Timedelta(hours=b3.STOP_BLOCK_H)
                    track.pop(tk, None)
            if v and ts < stop_until.get(tk, never):
                v = False
            elif r.raw and not prev_raw.get(tk):
                track[tk] = (px, ts)
            prev_raw[tk] = bool(r.raw)
        was = prev.get(tk, False)
        if guard_mode == 'start':
            if v and not was:
                blocked[tk] = not r.gd
            shown = v and not blocked.get(tk, False)
            new = v and not was and not blocked[tk]
        else:
            shown = v and bool(r.gd)
            new = shown and not prev.get(('shown', tk), False)
            prev[('shown', tk)] = shown
        prev[tk] = v
        if new:
            ev.append((ts, tk, side, float(r.exp4), float(r.p_up), float(r.atr_pct), float(r.r168), float(r.age_d)))
    return ev


def resignal_filter(ev_all_shown_times, ev):
    """클라 재신호 가드 근사: 같은 코인·방향 판정이 직전 6시간 안에 보였으면(이번 판정 시작 전) 새 신호로 안 봄."""
    out = []
    for e in ev:
        last = ev_all_shown_times.get(e[1])
        if last is not None:
            prior = last[last < e[0]]
            if len(prior) and (e[0] - prior.max()).total_seconds() < RESIGNAL_H * 3600:
                continue
        out.append(e)
    return out


def run_side(events_, frames, side):
    """b12.run_lev(3배, 가드는 이미 이벤트에서 처리) 롱·숏 공용."""
    trades = []
    hold_until, liq_until = {}, {}
    never = pd.Timestamp('1900-01-01')
    lev, liq_move, cost = 3, 1.0 / 3 - 0.02, b3.COST
    for ts, tk, sd, exp4, pup, atr, r168, age in sorted(events_, key=lambda e: (e[0], e[3] if side == 'short' else -e[3], e[1])):
        if ts < hold_until.get(tk, never) or ts < liq_until.get(tk, never):
            continue
        d = frames[tk]
        i = d.index.get_loc(ts)
        px = float(d['close'].iloc[i])
        fut = d.iloc[i + 1:i + 1 + b3.HOLD_H]
        if len(fut) < b3.HOLD_H:
            continue
        amt = b3.USD * (b3.STRONG_MULT if (side == 'short' and exp4 <= b3.STRONG_EXP4) else 1.0)
        mv = (fut['high'] / px - 1) if side == 'short' else (1 - fut['low'] / px)
        adv = float(mv.max())
        liq = adv >= liq_move
        if liq:
            roi = -0.9
            ex = fut.index[(mv >= liq_move).values][0]
            liq_until[tk] = ex + pd.Timedelta(hours=b3.LIQ_BLOCK_H)
        else:
            ret = fut['close'].iloc[-1] / px - 1
            roi = lev * (-ret if side == 'short' else ret) - cost
            ex = fut.index[-1]
        hold_until[tk] = ex
        trades.append({'ts': ts, 'exit_ts': ex, 'ticker': tk, 'side': side, 'amount': amt, 'exp4': round(exp4, 2),
                       'p_up': round(pup, 3), 'atr_pct': round(atr, 2), 'r168': round(r168, 1), 'age_d': int(age),
                       'max_adverse_pct': round(adv * 100, 1), 'liq': liq, 'roi': round(roi, 4), 'pnl': round(roi * amt, 2)})
    return pd.DataFrame(trades)


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
    uni = b8.band_universe(M, onboard, meme_set, *FULL)
    P = P[b3.membership_mask(P['ts'], P['ticker'].values, uni)]
    log(f"대상 행 {len(P):,}")

    runs = []

    def add(name, side, ev):
        tr = run_side(ev, bf, side)
        s = b2.stats(tr, split)
        dd = b9.max_dd(tr)
        hs = b12.half_stats(tr, split)
        runs.append((name, side, s, hs, dd, tr, len(ev)))
        log(f"{name}: 판정 {len(ev)} → {s['n']}건 ${s['pnl']:+,.0f} (앞 {s['h1']:+,.0f} / 뒤 {s['h2']:+,.0f}) 청산 {s['liqp'] if s['n'] else 0:.1f}% 낙폭 ${dd:+,.0f}")

    s0 = events(P, 'short')
    add('S0 숏 기준(bt13 추천과 같음)', 'short', s0)
    s1 = events(P, 'short', rsi_min=RSI_MIN)
    add('S1 숏 + RSI≥50', 'short', s1)
    # 재신호 가드: 판정이 '보이는' 시각(가드 통과 여부 무관하게 화면 판정) — 근사로 원판정(raw, 손절차단 후) 시각 사용
    Ps = P.sort_values('ts')
    raw_s = (Ps['exp4'] < b3.EXP4_MAX) & (Ps['p_up'] < b3.PUP_MAX) & ~(Ps['mkt_r4'] >= b3.MKT_R4_BLOCK)
    shown = Ps[raw_s & (Ps['age_d'] >= AGE_MIN) & (Ps['atr_pct'] < ATR_MAX) & (Ps['r168'] < R7_MAX)]
    st = {tk: pd.DatetimeIndex(g['ts']) for tk, g in shown.groupby('ticker')}
    add('S2 숏 + 클라 재신호 6h 가드', 'short', resignal_filter(st, s0))
    add('S3 숏 가드 매 시각(0시 서버 방식)', 'short', events(P, 'short', guard_mode='any'))
    l0 = events(P, 'long')
    add('L0 롱 모델 조건(p_up≥0.55·exp4>+0.2)', 'long', l0)
    add('L1 롱 + 하락장 대기 근사(REF 24h 평균≤0 대기)', 'long', events(P, 'long', bear_proxy=True))
    add('L2 롱 가드 없음(비교)', 'long', events(P, 'long', guard=False))
    add('L3 롱 가드 없음 + 하락장 대기 근사', 'long', events(P, 'long', bear_proxy=True, guard=False))

    os.makedirs(b2.REPORT_DIR, exist_ok=True)
    allt = [r[5].assign(run=r[0]) for r in runs if not r[5].empty]
    if allt:
        pd.concat(allt).to_csv(os.path.join(b2.REPORT_DIR, 'binance_backtest14_trades.csv'), index=False, encoding='utf-8-sig')
    L = [f"# 바이낸스 V3 — 숏 서버 규칙 맞추기 + 롱 검증 ({datetime.now():%Y-%m-%d %H:%M})", "",
         f"시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d} (앞/뒤 나눔 {split:%Y-%m-%d}) · 대상 필터 후 1-300위 · V3 · 가드 상장 {AGE_MIN}일↑·ATR<{ATR_MAX}·7일<+{R7_MAX:g}% · $30(강한숏 $60)·3배·48h·비용 4.2%·청산 31.3%(7일 차단)",
         "롱 결과는 서버 롱의 '점수 롱 후보' 조건을 뺀 상한 근사(과거 OI·롱숏비가 없어 재현 불가). 하락장 대기 근사 = REF 시장 24h 평균 ≤ 0 이면 대기", "",
         "| 시험 | 판정 | 진입 | 승률 | 건당 ROI | 청산 | $손익(1년) | 앞/뒤 6개월 | 청산율 앞/뒤 | 최악월 | 마이너스 달 | 최대 낙폭 | 통과 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, side, s, hs, dd, tr, ne in runs:
        if not s['n']:
            L.append(f"| {name} | {ne} | 0 | – | – | – | – | – | – | – | – | – | – |")
            continue
        ok = s['h1'] > 0 and s['h2'] > 0 and s['liqp'] <= 6
        L.append(f"| {name} | {ne} | {s['n']} | {s['win']:.0f}% | {s['roi']:+.1f}% | {s['liq']} ({s['liqp']:.1f}%) | ${s['pnl']:+,.0f} | "
                 f"${s['h1']:+,.0f} / ${s['h2']:+,.0f} | {hs['h1']['liqp']:.1f}% / {hs['h2']['liqp']:.1f}% | ${s['worst']:+,.0f} | {s['mneg']}/{s['mon']} | ${dd:+,.0f} | {'✅' if ok else '❌'} |")
    for name, side, s, hs, dd, tr, ne in runs:
        if side != 'long' or tr.empty:
            continue
        L += ["", f"## 월별: {name}", "", "| 월 | 진입 | 청산 | $손익 |", "|---|---|---|---|"]
        for m, g in tr.groupby(tr['ts'].dt.to_period('M')):
            L.append(f"| {m} | {len(g)} | {int(g['liq'].sum())} | ${g['pnl'].sum():+,.1f} |")
    L += ["", f"소요 {time.time() - t0:.0f}초"]
    out = os.path.join(b2.REPORT_DIR, f"binance_backtest14_{datetime.now():%Y%m%d}.md")
    with open(out, 'w', encoding='utf-8') as f:
        f.write("\n".join(L) + "\n")
    log(f"결과 저장: {out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
