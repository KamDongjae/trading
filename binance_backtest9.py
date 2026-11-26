# -*- coding: utf-8 -*-
"""
binance_backtest9.py
--------------------
[2026-11-25 신설, 사용자 요청] 거래대금 순위 구간(81-200 등) + 포트폴리오 위험 한도 시험.
대상: 매일 09시 밈·스테이블·배수 제외, 상장 1년↑, 30일 하루 ±30% 이력 제외, ATR<3, 7일<50 → 24h 거래대금 순위 구간
      81-200위 / 121-200위 / 121-160위(기준)
위험 한도: 제한 없음 / 동시 숏 10·20개 / 증거금 합 $300·$600 / 손절(가격 +10%·+15% 역행) / 동시 20 + 손절 15%
  - 동시·증거금 한도에 걸리면 그 신호는 건너뜀(같은 시각 여러 신호는 exp4 낮은(강한) 순으로 먼저)
  - 손절: 48h 안에 고가가 진입가 +X% 닿으면 그 시점 청산, 손실 = 3배 × X + 비용 4.2% (청산 7일 차단은 강제청산에만)
공통: 판정 v2 숏 · 모델 M_krw · 자동진입 상장 180일↑ · $30·3배·48h · 강한숏 $60 · 비용 -4.2% · 역행 31.3% 강제청산 · 진입 때 ATR·7일 재확인
지표: 기존 + 최대 동시 포지션 / 최대 증거금 / 최대 낙폭(청산 시각 기준 누적손익 고점 대비)
통과: 앞뒤 6개월 + · 청산 ≤6%
결과: reports/binance_backtest9_YYYYMMDD.md, reports/binance_backtest9_trades.csv
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

log = b2.log
MIN_AGE_AUTO_D = 180
BANDS = [(81, 200), (121, 200), (121, 160)]
LIMITS = {
    '제한 없음': dict(max_n=None, max_margin=None, sl=None),
    '동시 10개': dict(max_n=10, max_margin=None, sl=None),
    '동시 20개': dict(max_n=20, max_margin=None, sl=None),
    '증거금 $300': dict(max_n=None, max_margin=300.0, sl=None),
    '증거금 $600': dict(max_n=None, max_margin=600.0, sl=None),
    '손절 10%': dict(max_n=None, max_margin=None, sl=0.10),
    '손절 15%': dict(max_n=None, max_margin=None, sl=0.15),
    '동시 20 + 손절 15%': dict(max_n=20, max_margin=None, sl=0.15),
}


def run_limited(events, frames, max_n=None, max_margin=None, sl=None, atr_max=3.0, r168_max=50.0):
    """b3.run_entries 와 같은 규칙 + 동시 포지션·증거금 한도·손절."""
    trades = []
    hold_until, liq_until = {}, {}
    open_pos = []  # (exit_ts, amount)
    never = pd.Timestamp('1900-01-01')
    max_open, max_mg = 0, 0.0
    for ts, tk, side, exp4, pup, atr, r168, age in sorted(events, key=lambda e: (e[0], e[3], e[1])):
        if side != 'short':
            continue
        if atr_max is not None and not (atr < atr_max):
            continue
        if r168_max is not None and not (r168 < r168_max):
            continue
        if ts < hold_until.get(tk, never) or ts < liq_until.get((tk, side), never):
            continue
        open_pos = [p for p in open_pos if p[0] > ts]
        amt = b3.USD * (b3.STRONG_MULT if exp4 <= b3.STRONG_EXP4 else 1.0)
        if max_n is not None and len(open_pos) >= max_n:
            continue
        if max_margin is not None and sum(p[1] for p in open_pos) + amt > max_margin:
            continue
        d = frames[tk]
        i = d.index.get_loc(ts)
        px = float(d['close'].iloc[i])
        fut = d.iloc[i + 1:i + 1 + b3.HOLD_H]
        if len(fut) < b3.HOLD_H:
            continue
        up = fut['high'] / px - 1
        adv = float(up.max())
        liq = adv >= b3.LIQ_MOVE
        stop = (sl is not None) and adv >= sl
        if stop:
            hit = fut.index[(up >= sl).values][0]
            liq_hit = fut.index[(up >= b3.LIQ_MOVE).values][0] if liq else None
            if liq_hit is not None and liq_hit <= hit:
                stop = False  # 같은 봉에서 강제청산까지 → 강제청산 처리
            else:
                liq = False
        if liq:
            roi = -0.9
            ex = fut.index[(up >= b3.LIQ_MOVE).values][0]
            liq_until[(tk, side)] = ex + pd.Timedelta(hours=b3.LIQ_BLOCK_H)
        elif stop:
            roi = -b3.LEV * sl - b3.COST
            ex = hit
        else:
            roi = b3.LEV * -(fut['close'].iloc[-1] / px - 1) - b3.COST
            ex = fut.index[-1]
        hold_until[tk] = ex
        open_pos.append((ex, amt))
        max_open = max(max_open, len(open_pos))
        max_mg = max(max_mg, sum(p[1] for p in open_pos))
        trades.append({'ts': ts, 'exit_ts': ex, 'ticker': tk, 'side': side, 'amount': amt, 'exp4': round(exp4, 2),
                       'p_up': round(pup, 3), 'atr_pct': round(atr, 2), 'r168': round(r168, 1), 'age_d': int(age),
                       'max_adverse_pct': round(adv * 100, 1), 'liq': liq, 'stop': stop,
                       'roi': round(roi, 4), 'pnl': round(roi * amt, 2)})
    return pd.DataFrame(trades), max_open, max_mg


def max_dd(t):
    if t.empty:
        return 0.0
    eq = t.sort_values('exit_ts')['pnl'].cumsum()
    return float((eq - eq.cummax().clip(lower=0)).min())


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
    unis = {f'{lo}-{hi}위': b8.band_universe(M, onboard, meme_set, lo, hi) for lo, hi in BANDS}
    stat_lines = []
    for k, u in unis.items():
        sz = [len(v) for d, v in u.items() if d >= test_start]
        nm = set().union(*[v for d, v in u.items() if d >= test_start])
        stat_lines.append(f"| {k} | {np.mean(sz):.1f} | {len(nm)} |")
        log(f"[{k}] 하루 평균 {np.mean(sz):.1f}개 · 거쳐간 {len(nm)}개")

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
        for ln, lim in LIMITS.items():
            tr, mo, mm = run_limited(sev, bf, **lim)
            s = b2.stats(tr, split)
            res.append((f"{un} · {ln}", s, tr, mo, mm, max_dd(tr), int(tr['stop'].sum()) if not tr.empty else 0))
            if not tr.empty:
                allt.append(tr.assign(universe=un, limit=ln))
            log(f"{un} · {ln}: {len(tr)}건 ${s['pnl']:+,.0f} · 최대동시 {mo} · 최대증거금 ${mm:,.0f}")

    os.makedirs(b2.REPORT_DIR, exist_ok=True)
    if allt:
        pd.concat(allt).to_csv(os.path.join(b2.REPORT_DIR, 'binance_backtest9_trades.csv'), index=False, encoding='utf-8-sig')
    L = [f"# 거래대금 순위 구간 + 포트폴리오 위험 한도 ({datetime.now():%Y-%m-%d %H:%M})", "",
         f"시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d} (앞/뒤 나눔 {split:%Y-%m-%d}) · 매일 09시 밈·스테이블·배수 제외, 상장 1년↑, 30일 하루 ±30% 이력 제외, ATR<3, 7일<50 → 24h 거래대금 순위 구간 · 모델 M_krw · 진입 때 ATR·7일 재확인",
         "숏 판정 v2 · 자동진입 상장 180일↑ · $30·3배·48h · 강한숏 $60 · 비용 -4.2% · 역행 31.3% 강제청산(7일 차단) · 손절 = 가격 +X% 닿으면 청산(손실 3X+4.2%) · 통과 = 앞뒤 6개월 + · 청산 ≤6%", "",
         "| 대상 | 하루 평균 코인 | 1년간 거쳐간 코인 |", "|---|---|---|"] + stat_lines
    L += ["", "| 대상 · 한도 | 진입 | 승률 | 건당 ROI | 청산 | $손익(1년) | 앞/뒤 6개월 | 최악월 | 마이너스 달 | 통과 | 손절 | 최대 동시 | 최대 증거금 | 최대 낙폭 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for nm, s, tr, mo, mm, dd, ns in res:
        L.append(b2.row(nm, s) + f" {ns} | {mo} | ${mm:,.0f} | ${dd:+,.0f} |")
    okr = [x for x in res if x[1]['n'] and x[1]['h1'] > 0 and x[1]['h2'] > 0 and x[1]['liqp'] <= 6]
    if okr:
        nm, s, tb, mo, mm, dd, ns = max(okr, key=lambda x: x[1]['pnl'] / max(1.0, -x[5]))
        L += ["", f"## 통과 중 손익/최대낙폭 비율 최고: {nm}", "", "| 월 | 진입 | 청산 | 손절 | $손익 |", "|---|---|---|---|---|"]
        for m, g in tb.groupby(tb['ts'].dt.to_period('M')):
            L.append(f"| {m} | {len(g)} | {int(g['liq'].sum())} | {int(g['stop'].sum())} | ${g['pnl'].sum():+,.1f} |")
    L += ["", f"소요 {time.time() - t0:.0f}초"]
    out = os.path.join(b2.REPORT_DIR, f"binance_backtest9_{datetime.now():%Y%m%d}.md")
    with open(out, 'w', encoding='utf-8') as f:
        f.write("\n".join(L) + "\n")
    log(f"결과 저장: {out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
