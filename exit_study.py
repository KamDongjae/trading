# -*- coding: utf-8 -*-
"""[2026-11-26 사용자 요청] 지금 V3 숏 진입은 그대로 두고 '정리(컷) 규칙'만 바꿔보기
진입: bt14 S0와 같음(상장 730일↑·ATR<2.5·7일<+50%) · $30(강한숏 $60)·3배 · 비용 진입0.7%+정리0.7%(-4.2%) · 31.3% 역행 강제청산(-90%, 7일 차단)
정리 규칙 두 개(사용자 요청): ① 청산방지 컷 = 가격 +SL% 닿으면 손절 ② 수익 유지 컷 = 이익이 A% 이상 났다가 최대이익 지점에서 B%p 되돌리면 정리. 둘 다 안 걸리면 최대보유 시간에 정리
같은 봉에서 손절·익절 둘 다 닿으면 손절 먼저(보수적). 손절·익절·추적 체결가 = 그 가격 그대로(정리 슬리피지는 비용 4.2%에 포함)"""
import os, sys, json, itertools, time
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binance_backtest2 as b2, binance_backtest3 as b3, binance_backtest14 as b14
t0 = time.time()
E = pd.read_pickle(os.path.join(os.path.dirname(b14.PRED_PKL), 'short_events_raw.pkl'))
E = E[(E.age >= 730) & (E.atr < 2.5) & (E.r168 < 50)].sort_values(['ts', 'exp4']).reset_index(drop=True)
bf = b2.load_dir(b2.BIN_DIR, 'binance_*_1h.csv')
split = pd.Timestamp('2026-05-01')
LIQ, LEV, COST, MAXH = 1 / 3 - 0.02, 3, 0.042, 120
paths = {}
for r in E.itertuples():
    d = bf[r.tk]; i = d.index.get_loc(r.ts); px = float(d['close'].iloc[i])
    fut = d.iloc[i + 1:i + 1 + MAXH]
    paths[(r.ts, r.tk)] = (fut.index, fut['high'].values / px - 1, fut['low'].values / px - 1, fut['close'].values / px - 1)

def exit_one(key, H, SL, TP, TR):
    idx, hi, lo, cl = paths[key]
    n = min(H, len(cl))
    if n < H: return None
    best = 0.0  # 숏 최대 이익(가격 하락폭)
    for j in range(n):
        if hi[j] >= LIQ: return idx[j], -0.9, 'liq'
        if SL and hi[j] >= SL: return idx[j], -LEV * SL - COST, 'sl'
        if TR and best >= TR[0] and hi[j] >= -best + TR[1]: return idx[j], LEV * (best - TR[1]) - COST, 'tr'
        if TP and lo[j] <= -TP: return idx[j], LEV * TP - COST, 'tp'
        best = max(best, -lo[j])
    return idx[n - 1], LEV * -cl[n - 1] - COST, 'time'

def sim(H, SL, TP, TR):
    hold, liqb, out = {}, {}, []
    never = pd.Timestamp('1900-01-01')
    for r in E.itertuples():
        if r.ts < hold.get(r.tk, never) or r.ts < liqb.get(r.tk, never): continue
        o = exit_one((r.ts, r.tk), H, SL, TP, TR)
        if o is None: continue
        ex, roi, why = o; hold[r.tk] = ex
        if why == 'liq': liqb[r.tk] = ex + pd.Timedelta(days=7)
        out.append((r.ts, why, roi, roi * r.amt))
    t = pd.DataFrame(out, columns=['ts', 'why', 'roi', 'pnl'])
    a, b = t[t.ts < split], t[t.ts >= split]
    mon = t.groupby(t.ts.dt.to_period('M')).pnl.sum(); eq = t.pnl.cumsum()
    return dict(n=len(t), liq=int((t.why == 'liq').sum()), sl=int((t.why == 'sl').sum()), tp=int((t.why == 'tp').sum()), tr=int((t.why == 'tr').sum()),
                win=(t.roi > 0).mean() * 100, roi=t.roi.mean() * 100, pnl=t.pnl.sum(), p1=a.pnl.sum(), p2=b.pnl.sum(),
                worst=mon.min(), mavg=mon.mean(), mmed=mon.median(), mneg=int((mon < 0).sum()), dd=float((eq - eq.cummax()).min()))

rows = []
Hs = [48, 72, 96]
SLs = [0, 0.06, 0.08, 0.10, 0.12, 0.15, 0.20, 0.25]
TPs = [0]
TRs = [None] + [(a, b) for a in (0.03, 0.05, 0.08, 0.10, 0.15) for b in (0.02, 0.03, 0.05, 0.08) if b < a]
for H, SL, TP, TR in itertools.product(Hs, SLs, TPs, TRs):
    s = sim(H, SL, TP, TR)
    rows.append(dict(H=H, SL=SL, TP=TP, TR='-' if TR is None else f'{TR[0]*100:.0f}%후 +{TR[1]*100:.0f}%', **s))
R = pd.DataFrame(rows)
R.to_csv(os.path.join(b2.REPORT_DIR, 'exit_study_grid.csv'), index=False, encoding='utf-8-sig')
def f(r):
    return (f"| {r.H}h | {'+%.0f%%' % (r.SL*100) if r.SL else '-'} | {'-%.0f%%' % (r.TP*100) if r.TP else '-'} | {r.TR} | {r.n} | {r.win:.0f}% | {r.roi:+.1f}% | "
            f"{r.liq} / {r.sl} / {r.tp} / {r.tr} | ${r.pnl:+,.0f} | ${r.p1:+,.0f} / ${r.p2:+,.0f} | ${r.mavg:+.0f} / ${r.mmed:+.0f} | ${r.worst:+,.0f} | {r.mneg} | ${r.dd:+,.0f} |")
H = ["| 최대보유 | ①청산방지(가격) | 익절 | ②수익유지 | 진입 | 승률 | 건당 ROI | 청산/①/익절/② | $손익(1년) | 앞/뒤 6개월 | 월 평균/중앙 | 최악월 | 마이너스달 | 최대낙폭 |",
     "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
L = [f"# V3 숏 정리 규칙 비교 ({pd.Timestamp.now():%Y-%m-%d %H:%M})", "", f"진입은 지금 규칙 그대로(327건 근처), 비용 -4.2%, 조합 {len(R)}개", "", "## 지금 (48h 고정)", ""] + H
L += [f(r) for r in R[(R.H == 48) & (R.SL == 0) & (R.TP == 0) & (R.TR == '-')].itertuples()]
L += ["", "## ① 청산방지 컷만 (48h)", ""] + H + [f(r) for r in R[(R.H == 48) & (R.TR == '-')].itertuples()]
L += ["", "## ② 수익 유지 컷만 (48h)", ""] + H + [f(r) for r in R[(R.H == 48) & (R.SL == 0)].sort_values('pnl', ascending=False).itertuples()]
ok = R[(R.p1 > 0) & (R.p2 > 0)]
L += ["", "## ①+② 같이 — 앞·뒤 6개월 둘 다 플러스 중 $손익 상위 15", ""] + H + [f(r) for r in ok.sort_values('pnl', ascending=False).head(15).itertuples()]
L += ["", "## 앞·뒤 플러스 중 최대낙폭 작은 순(손익 ≥ 지금의 90%) 상위 10", ""] + H
base = R[(R.H == 48) & (R.SL == 0) & (R.TP == 0) & (R.TR == '-')].pnl.iloc[0]
L += [f(r) for r in ok[ok.pnl >= base * 0.9].sort_values('dd', ascending=False).head(10).itertuples()]
L += ["", f"소요 {time.time()-t0:.0f}초"]
out = os.path.join(b2.REPORT_DIR, f"exit_study_{pd.Timestamp.now():%Y%m%d}.md")
open(out, 'w', encoding='utf-8').write("\n".join(L) + "\n"); print("\n".join(L))
