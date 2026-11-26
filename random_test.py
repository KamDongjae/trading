# -*- coding: utf-8 -*-
"""[2026-11-26 사용자 요청] 랜덤 10개 · 12h 보유 · 50번 — 현재 V3 숏 규칙(12h 보유로 맞춤)과 비교
매일 09시(KST) 거래대금 51-250위(1-50위·251-300위 뺌, 밈·스테이블·1000배수 코인 제외, 상장 1년 미만 제외, 상장 기준·ATR<3·7일<+50% 필터 후) 코인 중 무작위 10개 진입 · $30 · 3배 · 12h 정리 · 비용 진입0.7%+정리0.7%(증거금 -4.2%) · 역행 31.3% 강제청산(-90%) · 같은 코인 보유 중이면 건너뜀"""
import os, sys, json
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binance_backtest2 as b2, binance_backtest3 as b3, binance_backtest4 as b4, binance_backtest8 as b8, binance_backtest14 as b14

HOLD, LEV, COST, LIQ = 12, 3, 0.042, 1/3 - 0.02
with open(os.path.join(b2.BIN_DIR, '_symbols.json'), encoding='utf-8') as f:
    sym = json.load(f)
onboard = {m['coin']: pd.Timestamp(int(m['onboard']), unit='ms') + pd.Timedelta(hours=9) for m in sym.values() if m.get('onboard')}
sub = b3.fetch_subtypes(); meme = {c for c, v in sub.items() if any('meme' in str(x).lower() for x in v)}
bf = b2.load_dir(b2.BIN_DIR, 'binance_*_1h.csv')
end = max(d.index[-1] for d in bf.values())
ts0 = (end.normalize() - pd.DateOffset(months=12)).replace(day=1)
M = b4.daily_metrics(bf)
P = b14.get_preds(bf, onboard, meme, M, ts0, end)
uni = b8.band_universe(M, onboard, meme, *b14.FULL)
P = P[b3.membership_mask(P['ts'], P['ticker'].values, uni)]

# 거래 결과 미리 계산: (ts, ticker) -> (short roi, long roi, liq_s, liq_l)
cache = {}
def outcome(ts, tk, side):
    k = (ts, tk, side)
    if k in cache: return cache[k]
    d = bf[tk]; i = d.index.get_loc(ts); px = float(d['close'].iloc[i])
    fut = d.iloc[i + 1:i + 1 + HOLD]
    if len(fut) < HOLD: cache[k] = None; return None
    mv = (fut['high'] / px - 1) if side == 'short' else (1 - fut['low'] / px)
    if float(mv.max()) >= LIQ:
        ex = fut.index[(mv >= LIQ).values][0]; r = (-0.9, True, ex)
    else:
        ret = fut['close'].iloc[-1] / px - 1
        r = (LEV * (-ret if side == 'short' else ret) - COST, False, fut.index[-1])
    cache[k] = r; return r

def run(entries, side):
    hold, rows = {}, []
    for ts, tk, amt in entries:
        if ts < hold.get(tk, pd.Timestamp('1900-01-01')): continue
        o = outcome(ts, tk, side)
        if o is None: continue
        roi, liq, ex = o; hold[tk] = ex
        rows.append((ts, roi, liq, roi * amt))
    t = pd.DataFrame(rows, columns=['ts', 'roi', 'liq', 'pnl'])
    mon = t.groupby(t['ts'].dt.to_period('M'))['pnl'].sum()
    eq = t.sort_values('ts')['pnl'].cumsum(); dd = float((eq - eq.cummax()).min())
    return dict(n=len(t), win=(t.roi > 0).mean() * 100, roi=t.roi.mean() * 100, liq=int(t.liq.sum()), liqp=t.liq.mean() * 100,
                pnl=t.pnl.sum(), mneg=int((mon < 0).sum()), mon=len(mon), dd=dd)

uni_mid = b8.band_universe(M, onboard, meme, 51, 250)
groups = {}
for ts in pd.date_range(ts0 + pd.Timedelta(hours=9), end - pd.Timedelta(hours=HOLD + 1), freq='24h'):
    tks = [tk for tk in uni_mid.get(ts.normalize(), ()) if tk in bf and ts in bf[tk].index and tk in onboard and (ts - onboard[tk]).days >= 365]  # 상장 1년 미만 제외
    if len(tks) >= 10: groups[ts] = np.array(sorted(tks))
print('랜덤 대상 일수', len(groups), '하루 평균 후보', np.mean([len(v) for v in groups.values()]))
res = {'short': [], 'long': []}
for seed in range(50):
    rng = np.random.default_rng(seed)
    ent = []
    for ts, tks in groups.items():
        for tk in rng.choice(tks, size=min(10, len(tks)), replace=False):
            ent.append((ts, tk, 30.0))
    for side in res: res[side].append(run(ent, side))

s0 = b14.events(P, 'short')
ref = run([(e[0], e[1], b3.USD * (b3.STRONG_MULT if e[3] <= b3.STRONG_EXP4 else 1.0)) for e in s0], 'short')

def line(name, r):
    return f"| {name} | {r['n']:.0f} | {r['win']:.0f}% | {r['roi']:+.1f}% | {r['liq']:.0f} ({r['liqp']:.1f}%) | ${r['pnl']:+,.0f} | {r['mneg']:.0f}/{r['mon']:.0f} | ${r['dd']:+,.0f} |"
L = [f"시험 {ts0:%Y-%m-%d}~{end:%Y-%m-%d} · 12h 보유 · $30·3배 · 비용 -4.2% · 청산 31.3%", "",
     "| 방식 | 진입 | 승률 | 건당 ROI | 청산 | $손익(1년) | 마이너스 달 | 최대 낙폭 |", "|---|---|---|---|---|---|---|---|",
     line('현재 V3 숏 규칙 (12h로 맞춤)', ref)]
for side in res:
    df = pd.DataFrame(res[side]); med = df.median()
    L.append(line(f'랜덤 10개 {side} 50번 중앙값', med))
    L.append(f"| ↳ {side} 50번 범위 | | {df.win.min():.0f}~{df.win.max():.0f}% | {df.roi.min():+.1f}~{df.roi.max():+.1f}% | {df.liqp.min():.1f}~{df.liqp.max():.1f}% | ${df.pnl.min():+,.0f} ~ ${df.pnl.max():+,.0f} | 50번 중 플러스 {int((df.pnl>0).sum())}번 | |")
print("\n".join(L))
