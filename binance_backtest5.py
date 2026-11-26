# -*- coding: utf-8 -*-
"""
binance_backtest5.py
--------------------
[2026-11-25 신설, 사용자 요청] 바이낸스 탭 대상 = 원화 거래소 코인(D, history/ ∩ 바이낸스 선물) + '필터 먼저 → 상위 N' 코인 합치기 검증.
binance_backtest4.py 에서 통과한 'F = 24h 거래대금 상위40 · ATR<3 · 7일<50' 등을 D에 더했을 때 D 단독 성적이 유지되는지.

비교: D 단독 / D+F(상위40·ATR<3·7일<50) / D+F(상위40·ATR<5·7일<50) / D+F(상위60·ATR<3·7일<50) / F 단독(상위40·ATR<3·7일<50)
      × 모델 M_krw / M_bin × 진입 때 ATR·7일 재확인 없음/있음
공통: 자동진입 상장 180일↑(실제 클라와 같음), 판정 v2 숏, $30·3배·48h, 강한숏 $60, 비용 -4.2%, 역행 31.3% 청산, 청산 코인 7일 차단
통과: 앞뒤 6개월 + · 청산 ≤6%
결과: reports/binance_backtest5_YYYYMMDD.md, reports/binance_backtest5_trades.csv
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
    log(f"바이낸스 {len(bf)}종목 · 원화 거래소 코인(D) {len(D)}개: {', '.join(sorted(D))}")
    M = b4.daily_metrics(bf)
    days = M[0].index
    Dday = {day: frozenset(t for t in D if (day - onboard.get(t, bf[t].index[0])).days >= 7) for day in days}
    F = {
        'F40·ATR<3·7일<50': b4.universe(M, onboard, meme_set, '24h', 40, 3.0, 50.0),
        'F40·ATR<5·7일<50': b4.universe(M, onboard, meme_set, '24h', 40, 5.0, 50.0),
        'F60·ATR<3·7일<50': b4.universe(M, onboard, meme_set, '24h', 60, 3.0, 50.0),
    }
    unis = {'D 단독': (Dday, None, None)}
    for k, u in F.items():
        am = 3.0 if 'ATR<3' in k else 5.0
        unis[f'D + {k}'] = ({d: Dday[d] | u.get(d, frozenset()) for d in days}, am, 50.0)
    unis['F40·ATR<3·7일<50 단독'] = (F['F40·ATR<3·7일<50'], 3.0, 50.0)
    for k, (u, _, _) in unis.items():
        sz = [len(v) for d, v in u.items() if d >= test_start]
        nm = set().union(*[v for d, v in u.items() if d >= test_start])
        log(f"[{k}] 하루 평균 {np.mean(sz):.1f}개 · 거쳐간 {len(nm)}개")
    pool = {}
    for u, _, _ in unis.values():
        for day, s in u.items():
            for t in s:
                pool.setdefault(t, set()).add(day)

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
    preds = {'M_krw': b2.walk_forward(T, K, edges, 'M_krw'), 'M_bin': b2.walk_forward(T, B, edges, 'M_bin')}

    rows, allt = [], []
    for un, (uni, am, r7) in unis.items():
        mask = b3.membership_mask(T['ts'], T['ticker'].values, uni)
        Tu = T[mask]
        for mn, pr in preds.items():
            P = Tu.join(pr, how='inner')
            if P.empty:
                continue
            P = P.assign(mkt_r4=P.groupby('ts')['r4'].transform('mean'), u_r24=P.groupby('ts')['r24'].transform('mean'))
            sev = [e for e in b3.short_events(P) if e[7] >= MIN_AGE_AUTO_D]
            for chk in ((False, True) if am else (False,)):
                tr = b3.run_entries(sev, bf, am if chk else None, r7 if chk else None)
                tr = tr.assign(in_D=tr['ticker'].isin(D)) if not tr.empty else tr
                rows.append(((un, mn, chk), tr))
                if not tr.empty:
                    allt.append(tr.assign(universe=un, model=mn, entry_check=chk))
        log(f"{un} 끝")

    os.makedirs(b2.REPORT_DIR, exist_ok=True)
    if allt:
        pd.concat(allt).to_csv(os.path.join(b2.REPORT_DIR, 'binance_backtest5_trades.csv'), index=False, encoding='utf-8-sig')

    def label(k):
        un, mn, chk = k
        return f"{un} · {mn}{' · 진입때 ATR·7일 재확인' if chk else ''}"

    res = [(label(k), b2.stats(t, split), k, t) for k, t in rows]
    res.sort(key=lambda x: (0 if (x[1]['n'] > 0 and x[1]['h1'] > 0 and x[1]['h2'] > 0 and x[1]['liqp'] <= 6) else 1, -float(x[1]['pnl'])))
    lines = [f"# 바이낸스 탭 대상 합치기 검증: 원화 거래소 코인 + 필터 후 상위 N ({datetime.now():%Y-%m-%d %H:%M})", "",
             f"시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d} (앞/뒤 나눔 {split:%Y-%m-%d}) · D = 원화 거래소 코인 ∩ 바이낸스 선물 {len(D)}개 · F = 매일 09시 밈·스테이블·배수 제외, 상장 1년↑, 30일 하루 ±30% 이력 제외, ATR·7일 필터 후 24h 거래대금 상위 N(1.5N 유지)",
             "숏 판정 v2 · 자동진입 상장 180일↑ · $30·3배·48h · 강한숏 $60 · 비용 -4.2% · 역행 31.3% 청산 · 청산 코인 7일 차단 · 통과 = 앞뒤 6개월 + · 청산 ≤6%", "",
             "| 대상 · 모델 | 진입 | 승률 | 건당 ROI | 청산 | $손익(1년) | 앞/뒤 6개월 | 최악월 | 마이너스 달 | 통과 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for nm, s, k, t in res:
        lines.append(b2.row(nm, s))
    lines += ["", "## 합친 조합에서 D 코인 / 추가된 F 코인 나눠 보기", "",
              "| 대상 · 모델 | D 코인 진입·청산·$ | 추가 코인 진입·청산·$ |", "|---|---|---|"]
    for nm, s, k, t in res:
        if t.empty or k[0] == 'D 단독' or 'in_D' not in t.columns:
            continue
        dd, ff = t[t['in_D']], t[~t['in_D']]
        lines.append(f"| {nm} | {len(dd)}건 · {int(dd['liq'].sum())} · ${dd['pnl'].sum():+,.0f} | {len(ff)}건 · {int(ff['liq'].sum())} · ${ff['pnl'].sum():+,.0f} |")
    best = [x for x in res if x[1]['n'] and x[1]['h1'] > 0 and x[1]['h2'] > 0 and x[1]['liqp'] <= 6]
    if best:
        nm, s, k, tb = max(best, key=lambda x: x[1]['pnl'])
        lines += ["", f"## 통과 중 1년 손익 최고: {nm}", "", "| 월 | 진입 | 청산 | $손익 |", "|---|---|---|---|"]
        for m, g in tb.groupby(tb['ts'].dt.to_period('M')):
            lines.append(f"| {m} | {len(g)} | {int(g['liq'].sum())} | ${g['pnl'].sum():+,.1f} |")
        liqs = tb[tb['liq']]
        if len(liqs):
            lines.append("")
            lines.append("청산: " + ", ".join(f"{r.ticker} {r.ts:%m/%d}" for r in liqs.itertuples()))
    lines += ["", f"소요 {time.time() - t0:.0f}초"]
    out = os.path.join(b2.REPORT_DIR, f"binance_backtest5_{datetime.now():%Y%m%d}.md")
    with open(out, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines) + "\n")
    log(f"결과 저장: {out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
