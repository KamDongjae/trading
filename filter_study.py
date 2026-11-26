# -*- coding: utf-8 -*-
"""[2026-11-26 사용자 요청] V3 숏 — 가드에 걸린 후보 분석 + 청산 1/60(1.67%) 이하 목표 필터 탐색
판정 v2 숏 원판정(가드 없음)을 전부 뽑아 48h/24h/12h 결과를 미리 계산 → 필터 조합별로 순차 시뮬(같은 코인 보유 중 건너뜀, 청산 코인 7일 차단)
비용 진입0.7%+정리0.7%(3배 -4.2%), $30(강한숏 $60), 역행 31.3% 강제청산(-90%)"""
import os, sys, json, itertools, time
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binance_backtest2 as b2, binance_backtest3 as b3, binance_backtest4 as b4, binance_backtest8 as b8, binance_backtest14 as b14
t0 = time.time()
with open(os.path.join(b2.BIN_DIR, '_symbols.json'), encoding='utf-8') as f:
    sym = json.load(f)
onboard = {m['coin']: pd.Timestamp(int(m['onboard']), unit='ms') + pd.Timedelta(hours=9) for m in sym.values() if m.get('onboard')}
sub = b3.fetch_subtypes(); meme = {c for c, v in sub.items() if any('meme' in str(x).lower() for x in v)}
bf = b2.load_dir(b2.BIN_DIR, 'binance_*_1h.csv')
end = max(d.index[-1] for d in bf.values())
ts0 = (end.normalize() - pd.DateOffset(months=12)).replace(day=1); split = ts0 + pd.DateOffset(months=6)
M = b4.daily_metrics(bf)
P = b14.get_preds(bf, onboard, meme, M, ts0, end)
uni = b8.band_universe(M, onboard, meme, *b14.FULL)
P = P[b3.membership_mask(P['ts'], P['ticker'].values, uni)]
feat = P.set_index(['ts', 'ticker'])[['rsi', 'r24', 'mkt_r24', 'mkt_r4']]
ev = b14.events(P, 'short', guard=False)
LIQ, LEV, COST = 1 / 3 - 0.02, 3, 0.042
rows = []
for ts, tk, sd, exp4, pup, atr, r168, age in ev:
    d = bf[tk]; i = d.index.get_loc(ts); px = float(d['close'].iloc[i])
    fut = d.iloc[i + 1:i + 49]
    if len(fut) < 48: continue
    mv = fut['high'].values / px - 1
    r = dict(ts=ts, tk=tk, exp4=exp4, p_up=pup, atr=atr, r168=r168, age=age, amt=60.0 if exp4 <= b3.STRONG_EXP4 else 30.0)
    f = feat.loc[(ts, tk)]
    r.update(rsi=float(f.rsi), r24=float(f.r24), mkt_r24=float(f.mkt_r24))
    # 직전 지표
    past = d.iloc[max(0, i - 168):i + 1]
    r['dd_from_7dhigh'] = (px / past['high'].max() - 1) * 100
    r['vol_ratio'] = float(d['volume'].iloc[max(0, i - 23):i + 1].sum() / max(d['volume'].iloc[max(0, i - 167):i + 1].sum() / 7, 1e-9)) if 'volume' in d else np.nan
    for H in (12, 24, 48):
        m = mv[:H]; hit = np.where(m >= LIQ)[0]
        if len(hit):
            r[f'liq{H}'] = True; r[f'roi{H}'] = -0.9; r[f'ex{H}'] = fut.index[hit[0]]
        else:
            r[f'liq{H}'] = False; r[f'roi{H}'] = LEV * -(fut['close'].iloc[H - 1] / px - 1) - COST; r[f'ex{H}'] = fut.index[H - 1]
    r['maxadv48'] = float(mv.max()) * 100
    rows.append(r)
E = pd.DataFrame(rows).sort_values(['ts', 'exp4']).reset_index(drop=True)
E.to_pickle(os.path.join(os.path.dirname(b14.PRED_PKL), 'short_events_raw.pkl'))
print(f"원판정 숏 {len(E)}건 · 준비 {time.time()-t0:.0f}s")

def sim(mask, H=48):
    sub = E[mask]
    hold, liqb, out = {}, {}, []
    never = pd.Timestamp('1900-01-01')
    for r in sub.itertuples():
        if r.ts < hold.get(r.tk, never) or r.ts < liqb.get(r.tk, never): continue
        liq = getattr(r, f'liq{H}'); ex = getattr(r, f'ex{H}')
        hold[r.tk] = ex
        if liq: liqb[r.tk] = ex + pd.Timedelta(hours=b3.LIQ_BLOCK_H)
        out.append((r.ts, liq, getattr(r, f'roi{H}') * r.amt))
    t = pd.DataFrame(out, columns=['ts', 'liq', 'pnl'])
    if t.empty: return None
    h1 = t[t.ts < split]; h2 = t[t.ts >= split]
    mon = t.groupby(t.ts.dt.to_period('M')).pnl.sum()
    return dict(n=len(t), liq=int(t.liq.sum()), liqp=t.liq.mean() * 100, pnl=t.pnl.sum(), n1=len(h1), n2=len(h2),
                l1=h1.liq.mean() * 100 if len(h1) else 0, l2=h2.liq.mean() * 100 if len(h2) else 0,
                p1=h1.pnl.sum(), p2=h2.pnl.sum(), worst=mon.min(), mneg=int((mon < 0).sum()))

# 1) 가드별로 막힌 후보 성적
cur = (E.age >= 730) & (E.atr < 2.5) & (E.r168 < 50)
out = ["## 1. 지금 가드에 걸린 후보 성적 (48h, 겹침 무시 단순 집계)", "", "| 묶음 | 건수 | 청산율 | 평균 ROI | 승률 |", "|---|---|---|---|---|"]
def agg(name, m):
    s = E[m]
    if len(s): out.append(f"| {name} | {len(s)} | {s.liq48.mean()*100:.1f}% | {s.roi48.mean()*100:+.1f}% | {(s.roi48>0).mean()*100:.0f}% |")
agg('통과(현재 진입)', cur)
agg('상장 730일 미만으로 막힘', E.age < 730)
for lo, hi in [(0, 180), (180, 365), (365, 540), (540, 730)]:
    agg(f'  ↳ 상장 {lo}~{hi}일', (E.age >= lo) & (E.age < hi))
agg('ATR ≥2.5로 막힘', E.atr >= 2.5)
for lo, hi in [(2.5, 3), (3, 4), (4, 99)]:
    agg(f'  ↳ ATR {lo}~{hi}', (E.atr >= lo) & (E.atr < hi))
agg('7일 +50% 이상으로 막힘', E.r168 >= 50)
# 2) 통과분 중 청산 건 특징 vs 비청산
out += ["", "## 2. 통과분: 청산 vs 비청산 특징 (중앙값)", "", "| 지표 | 청산 | 비청산 |", "|---|---|---|"]
C = E[cur]
for c in ['exp4', 'p_up', 'atr', 'r168', 'age', 'rsi', 'r24', 'mkt_r24', 'dd_from_7dhigh', 'vol_ratio']:
    out.append(f"| {c} | {C[C.liq48][c].median():.2f} | {C[~C.liq48][c].median():.2f} |")
# 3) 그리드
grid = dict(age=[180, 365, 540, 730], atr=[2.0, 2.5, 3.0, 4.0], r168=[20, 35, 50], rsi=[0, 50, 60], r24=[99, 15, 10, 5], H=[12, 24, 48])
res = []
for age, atr, r168, rsi, r24, H in itertools.product(*grid.values()):
    m = (E.age >= age) & (E.atr < atr) & (E.r168 < r168) & (E.rsi >= rsi) & (E.r24 < r24)
    s = sim(m, H)
    if s: res.append(dict(age=age, atr=atr, r168=r168, rsi=rsi, r24=r24, H=H, **s))
R = pd.DataFrame(res)
R.to_csv(os.path.join(b2.REPORT_DIR, 'filter_study_grid.csv'), index=False, encoding='utf-8-sig')
ok = R[(R.l1 <= 1.67) & (R.l2 <= 1.67) & (R.p1 > 0) & (R.p2 > 0)].sort_values(['n', 'pnl'], ascending=False)
def tbl(df, title):
    L = ["", f"## {title}", "", "| 상장≥ | ATR< | 7일< | RSI≥ | 24h< | 보유 | 진입 | 청산 | 청산율 앞/뒤 | $손익 | 앞/뒤 | 최악월 | 마이너스달 |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in df.itertuples():
        L.append(f"| {r.age} | {r.atr} | {r.r168} | {r.rsi} | {r.r24 if r.r24<99 else '-'} | {r.H}h | {r.n} | {r.liq} ({r.liqp:.1f}%) | {r.l1:.1f}% / {r.l2:.1f}% | ${r.pnl:+,.0f} | ${r.p1:+,.0f} / ${r.p2:+,.0f} | ${r.worst:+,.0f} | {r.mneg} |")
    return L
base = R[(R.age == 730) & (R.atr == 2.5) & (R.r168 == 50) & (R.rsi == 0) & (R.r24 == 99)]
out += tbl(base.sort_values('H'), '3. 현재 가드 기준 (보유시간별)')
out += tbl(ok.head(15), '4. 청산 ≤1.67%(60건에 1건, 앞·뒤 6개월 둘 다) + 앞뒤 플러스 — 진입 많은 순 상위 15')
out += tbl(ok.sort_values('pnl', ascending=False).head(10), '5. 같은 조건 — $손익 높은 순 상위 10')
print("\n".join(out)); print(f"\n소요 {time.time()-t0:.0f}s · 조합 {len(R)} · 통과 {len(ok)}")
