# -*- coding: utf-8 -*-
"""[2026-11-26 사용자 요청] '7일 급등 후 꺾이면 숏' 별도 규칙 백테스트 (모델 판정 없이)
신호: 최근 7일 안에 7일 상승률이 X% 이상 찍힌 코인이, 최근 7일 고가 대비 D% 이상 내려온 첫 1h봉 종가에 숏
대상: 바이낸스 24h 거래대금 1-300위(이번엔 7일 급등·ATR 제외 없이), 밈·스테이블·1000배수 제외, 상장 ≥365/730일
$30·3배·보유 24h/48h · 비용 진입0.7%+정리0.7%(-4.2%) · 역행 31.3% 강제청산(-90%) · 같은 코인 보유 중/청산 후 7일 건너뜀"""
import os, sys, json, itertools, time
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binance_backtest2 as b2, binance_backtest3 as b3, binance_backtest4 as b4, binance_backtest8 as b8
t0 = time.time()
with open(os.path.join(b2.BIN_DIR, '_symbols.json'), encoding='utf-8') as f:
    sym = json.load(f)
onboard = {m['coin']: pd.Timestamp(int(m['onboard']), unit='ms') + pd.Timedelta(hours=9) for m in sym.values() if m.get('onboard')}
sub = b3.fetch_subtypes(); meme = {c for c, v in sub.items() if any('meme' in str(x).lower() for x in v)}
bf = b2.load_dir(b2.BIN_DIR, 'binance_*_1h.csv')
end = max(d.index[-1] for d in bf.values())
ts0 = (end.normalize() - pd.DateOffset(months=12)).replace(day=1); split = ts0 + pd.DateOffset(months=6)
M = b4.daily_metrics(bf)
uni = b8.band_universe(M, onboard, meme, 1, 300, atr_max=999, r7_max=999)
LIQ, LEV, COST = 1 / 3 - 0.02, 3, 0.042

def signals(X, D, age_min):
    ev = []
    for tk, d in bf.items():
        if tk not in onboard: continue
        d = d[d.index >= ts0 - pd.Timedelta(days=8)]
        if len(d) < 400: continue
        c, h = d['close'], d['high']
        r7 = c / c.shift(168) - 1
        pumped = r7.rolling(168, min_periods=1).max() >= X / 100
        peak = h.rolling(168, min_periods=1).max()
        cond = pumped & (c <= peak * (1 - D / 100))
        edge = cond & ~cond.shift(1, fill_value=False)
        for ts in d.index[edge.values]:
            if ts < ts0: continue
            if (ts - onboard[tk]).days < age_min: continue
            if tk not in uni.get(ts.normalize(), ()): continue
            ev.append((ts, tk, float(r7.loc[ts]) * 100, float(c.loc[ts] / peak.loc[ts] - 1) * 100))
    return sorted(ev)

def sim(ev, H):
    hold, liqb, out = {}, {}, []
    never = pd.Timestamp('1900-01-01')
    for ts, tk, r7, dd in ev:
        if ts < hold.get(tk, never) or ts < liqb.get(tk, never): continue
        d = bf[tk]; i = d.index.get_loc(ts); px = float(d['close'].iloc[i])
        fut = d.iloc[i + 1:i + 1 + H]
        if len(fut) < H: continue
        mv = fut['high'].values / px - 1; hit = np.where(mv >= LIQ)[0]
        if len(hit):
            ex = fut.index[hit[0]]; roi = -0.9; liqb[tk] = ex + pd.Timedelta(days=7); liq = True
        else:
            ex = fut.index[-1]; roi = LEV * -(fut['close'].iloc[-1] / px - 1) - COST; liq = False
        hold[tk] = ex
        out.append((ts, tk, liq, roi, roi * 30.0, float(mv.max()) * 100))
    t = pd.DataFrame(out, columns=['ts', 'tk', 'liq', 'roi', 'pnl', 'maxadv'])
    return t

def st(t):
    if t.empty: return None
    a, b = t[t.ts < split], t[t.ts >= split]
    mon = t.groupby(t.ts.dt.to_period('M')).pnl.sum()
    eq = t.pnl.cumsum()
    return dict(n=len(t), liq=int(t.liq.sum()), liqp=t.liq.mean() * 100, roi=t.roi.mean() * 100, win=(t.roi > 0).mean() * 100,
                pnl=t.pnl.sum(), p1=a.pnl.sum(), p2=b.pnl.sum(), l1=a.liq.mean() * 100 if len(a) else 0, l2=b.liq.mean() * 100 if len(b) else 0,
                worst=mon.min(), mneg=int((mon < 0).sum()), mon=len(mon), dd=float((eq - eq.cummax()).min()), coins=t.tk.nunique())

rows, keep = [], {}
for X, D, age in itertools.product([50, 80, 100], [10, 15, 20, 30], [365, 730]):
    ev = signals(X, D, age)
    for H in (24, 48):
        t = sim(ev, H); s = st(t)
        if s: rows.append(dict(X=X, D=D, age=age, H=H, **s)); keep[(X, D, age, H)] = t
R = pd.DataFrame(rows)
R.to_csv(os.path.join(b2.REPORT_DIR, 'pump_fade_grid.csv'), index=False, encoding='utf-8-sig')
L = [f"# 7일 급등 후 꺾이면 숏 — 별도 규칙 백테스트 ({pd.Timestamp.now():%Y-%m-%d %H:%M})", "",
     f"시험 {ts0:%Y-%m-%d}~{end:%Y-%m-%d} (앞/뒤 {split:%Y-%m-%d}) · 대상 거래대금 1-300위(7일 급등·ATR 제외 없음), 밈·스테이블 제외 · $30·3배 · 비용 -4.2% · 청산 31.3%(7일 차단)",
     "신호: 최근 7일 안에 7일 상승률 ≥X% → 7일 고가 대비 D% 이상 하락한 첫 봉 종가에 숏", "",
     "| 7일 급등 X | 고점대비 D | 상장≥ | 보유 | 진입 | 코인 | 승률 | 건당 ROI | 청산 | 청산율 앞/뒤 | $손익 | 앞/뒤 6개월 | 최악월 | 마이너스 달 | 최대 낙폭 |",
     "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
for r in R.sort_values(['X', 'D', 'age', 'H']).itertuples():
    L.append(f"| {r.X}% | -{r.D}% | {r.age}일 | {r.H}h | {r.n} | {r.coins} | {r.win:.0f}% | {r.roi:+.1f}% | {r.liq} ({r.liqp:.1f}%) | {r.l1:.1f}% / {r.l2:.1f}% | ${r.pnl:+,.0f} | ${r.p1:+,.0f} / ${r.p2:+,.0f} | ${r.worst:+,.0f} | {r.mneg}/{r.mon} | ${r.dd:+,.0f} |")
ok = R[(R.p1 > 0) & (R.p2 > 0) & (R.n >= 20)].sort_values('pnl', ascending=False)
L += ["", f"앞·뒤 6개월 둘 다 플러스 & 20건 이상: {len(ok)}개 / {len(R)}개"]
if len(ok):
    b = ok.iloc[0]; t = keep[(b.X, b.D, b.age, b.H)]
    L += ["", f"## 최고 조합 거래 목록 (X {b.X}% · D -{b.D}% · 상장 {b.age}일 · {b.H}h)", "", "| 진입 | 코인 | 숏 ROI | 청산 | 최대 역행 |", "|---|---|---|---|---|"]
    for r in t.itertuples():
        L.append(f"| {r.ts:%Y-%m-%d %H시} | {r.tk} | {r.roi*100:+.1f}% | {'청산' if r.liq else ''} | {r.maxadv:.1f}% |")
L += ["", f"소요 {time.time()-t0:.0f}초"]
out = os.path.join(b2.REPORT_DIR, f"pump_fade_{pd.Timestamp.now():%Y%m%d}.md")
open(out, 'w', encoding='utf-8').write("\n".join(L) + "\n")
print("\n".join(L[:60]))
