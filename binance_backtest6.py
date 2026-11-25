# -*- coding: utf-8 -*-
"""
binance_backtest6.py
--------------------
[2026-11-25 신설, 사용자 요청] 과최적화 확인 — 원화 거래소 코인 28개(D)를 반으로 나눠 '처음 보는 코인' 성적 보기.

- 판정 규칙은 지금 값 그대로(판정 v2 숏: exp4<-0.5·p_up<0.45·시장 4h≥+1% 대기·숏 손절 차단), 아무것도 다시 맞추지 않음
- D(BTC 빼고 27개)를 무작위로 13/14 두 묶음(A/B)으로 나눔. BTC는 시장 기준 피처라 학습·피처엔 항상 포함, 거래 대상은 그 묶음만 — 3가지 다른 나눔(seed 1·2·3)
- 모델: 원화 history로 월별 워크포워드 학습. 단 '시험하는 묶음 코인은 학습에서 뺌'(코인 단위 표본 외)
         비교용으로 '전부 넣고 학습'(지금 방식) 모델로도 같은 묶음을 돌림
- 시험: 바이낸스 1h봉, 그 묶음 코인만 대상(시장 평균·순위 피처도 그 묶음 안에서), $30·3배·48h·강한숏 $60·비용 -2.1%·역행 31.3% 청산·청산 코인 7일 차단·상장 180일↑
모델 재학습은 3개월마다(13번 학습해야 해서 시간 단축, 모든 비교 모델 같은 조건)
판단: 표본 외(코인 뺀 모델) 성적이 A·B 둘 다 + 이고 '전부 넣은 모델'과 비슷하면 → 진짜 특성
      표본 외에서 크게 떨어지거나 한쪽만 + 이면 → 과최적화(코인을 외운 것)
결과: reports/binance_backtest6_YYYYMMDD.md, reports/binance_backtest6_trades.csv
"""
import os
import sys
import json
import time
import random
import argparse
from datetime import datetime

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import binance_backtest2 as b2  # noqa: E402
import binance_backtest3 as b3  # noqa: E402

log = b2.log
MIN_AGE_AUTO_D = 180
SEEDS = (1, 2, 3)


def table_for(bf, onboard, coins, test_start):
    cs = set(coins)

    def mask(tk, idx):
        return np.full(len(idx), tk in cs, dtype=bool)
    return b3.build_table({t: bf[t] for t in coins}, onboard, mask, test_start)


def run_half(T, pred, bf, coins=None):
    P = T.join(pred, how='inner')
    if P.empty:
        return pd.DataFrame()
    P = P.assign(mkt_r4=P.groupby('ts')['r4'].transform('mean'))
    ev = [e for e in b3.short_events(P) if e[7] >= MIN_AGE_AUTO_D and (coins is None or e[1] in coins)]
    return b3.run_entries(ev, bf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--months', type=int, default=12)
    a = ap.parse_args()
    t0 = time.time()
    with open(os.path.join(b2.BIN_DIR, '_symbols.json'), encoding='utf-8') as f:
        sym = json.load(f)
    onboard = {m['coin']: pd.Timestamp(int(m['onboard']), unit='ms') + pd.Timedelta(hours=9)
               for m in sym.values() if m.get('onboard')}
    kf = b2.load_dir(b2.KRW_DIR, '*_1h.csv')
    bf = b2.load_dir(b2.BIN_DIR, 'binance_*_1h.csv')
    D = sorted(set(kf) & set(bf))
    end = max(d.index[-1] for d in bf.values())
    test_start = (end.normalize() - pd.DateOffset(months=a.months)).replace(day=1)
    split = test_start + pd.DateOffset(months=a.months // 2)
    edges = pd.date_range(test_start, end + pd.DateOffset(months=3), freq='3MS')  # 시간 줄이려고 3개월마다 재학습(전 모델 같은 조건)
    log(f"D {len(D)}개 · 시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d}")

    K_all = b2.krw_table(kf)
    halves = []
    Dx = [t for t in D if t != 'BTC']  # BTC는 시장 기준(btc_r24 등 피처)이라 항상 학습·피처에 두고, 나누는 대상에선 뺌
    for sd in SEEDS:
        c = Dx[:]
        random.Random(sd).shuffle(c)
        A, B = sorted(c[:len(c) // 2]), sorted(c[len(c) // 2:])
        halves += [(sd, 'A', A), (sd, 'B', B)]
    pred_cache_full = {}
    rows, allt = [], []
    # 전체 D(지금 방식) 기준선
    TD = table_for(bf, onboard, D, test_start)
    TD = TD[TD['ts'] >= test_start]
    pfull_D = b2.walk_forward(TD, K_all, edges, 'full·D전체')
    trD = run_half(TD, pfull_D, bf)
    rows.append(('D 28개 전체 · 전부 넣고 학습(지금 방식)', trD))
    for sd, nm, coins in halves:
        T = table_for(bf, onboard, coins + ['BTC'], test_start)
        T = T[T['ts'] >= test_start]
        K_ex = b2.krw_table({t: d for t, d in kf.items() if t not in set(coins)})
        p_oos = b2.walk_forward(T, K_ex, edges, f"seed{sd}{nm}·코인뺌")
        p_in = b2.walk_forward(T, K_all, edges, f"seed{sd}{nm}·전부")
        for label, pr in (('처음 보는 코인(학습에서 뺌)', p_oos), ('전부 넣고 학습', p_in)):
            tr = run_half(T, pr, bf, set(coins))
            key = f"나눔{sd} 묶음{nm} · {label}"
            rows.append((key, tr))
            if not tr.empty:
                allt.append(tr.assign(seed=sd, half=nm, model=label))
        log(f"나눔{sd} 묶음{nm} 끝: {', '.join(coins)}")

    os.makedirs(b2.REPORT_DIR, exist_ok=True)
    if allt:
        pd.concat(allt).to_csv(os.path.join(b2.REPORT_DIR, 'binance_backtest6_trades.csv'), index=False, encoding='utf-8-sig')
    lines = [f"# 과최적화 확인: 원화 거래소 코인 반 나눠 처음 보는 코인 성적 ({datetime.now():%Y-%m-%d %H:%M})", "",
             f"시험 {test_start:%Y-%m-%d}~{end:%Y-%m-%d} (앞/뒤 나눔 {split:%Y-%m-%d}) · 판정 규칙 그대로 · 바이낸스 1h봉 · 숏 $30·3배·48h·강한숏 $60·청산 코인 7일 차단·상장 180일↑",
             "모델 3개월마다 재학습 · '처음 보는 코인' = 그 묶음 코인을 모델 학습(원화 history)에서 빼고 학습한 모델로 시험. ✅ = 앞뒤 6개월 + · 청산 ≤6%", "",
             "| 대상 · 모델 | 진입 | 승률 | 건당 ROI | 청산 | $손익(1년) | 앞/뒤 6개월 | 최악월 | 마이너스 달 | 통과 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for key, tr in rows:
        lines.append(b2.row(key, b2.stats(tr, split)))
    lines += ["", "## 묶음 구성"]
    for sd, nm, coins in halves:
        lines.append(f"- 나눔{sd} 묶음{nm}: {', '.join(coins)}")
    lines += ["", "## 코인별 손익(처음 보는 코인 모델, 전 나눔 합)", ""]
    if allt:
        X = pd.concat(allt)
        X = X[X['model'].str.startswith('처음')]
        g = X.groupby('ticker').agg(n=('pnl', 'size'), pnl=('pnl', 'sum'), liq=('liq', 'sum')).sort_values('pnl')
        lines.append("| 코인 | 진입 | 청산 | $손익 |")
        lines.append("|---|---|---|---|")
        for t, r in g.iterrows():
            lines.append(f"| {t} | {int(r['n'])} | {int(r['liq'])} | ${r['pnl']:+,.1f} |")
    lines += ["", f"소요 {time.time() - t0:.0f}초"]
    out = os.path.join(b2.REPORT_DIR, f"binance_backtest6_{datetime.now():%Y%m%d}.md")
    with open(out, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines) + "\n")
    log(f"결과 저장: {out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
