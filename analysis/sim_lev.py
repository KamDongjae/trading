import pandas as pd, numpy as np
d=pd.read_pickle('full_scored_cond_fwdret.pkl').sort_values(['ticker','timestamp']).reset_index(drop=True)
d['t']=d.timestamp.astype('datetime64[s]').astype('int64')
# 레짐 프록시: 6h 시장평균 recent_pct 3분위
d['b']=d.t//(6*3600)
m=d.groupby('b').recent_pct.mean(); q1,q2=m.quantile([1/3,2/3])
d['reg']=d.b.map(lambda b: '하락' if m[b]<=q1 else ('횡보' if m[b]<=q2 else '상승'))
FEE=0.0004; LIQ=0.9
series={k:(g.t.values,g.price_krw.values.astype(float)) for k,g in d.groupby('ticker')}
def sim(sig_idx, side, H, lev, stop=None):
    out=[]; last={}
    for i in sig_idx:
        tk=d.at[i,'ticker']; t0=d.at[i,'t']
        if tk in last and t0-last[tk]<24*3600: continue
        T,P=series[tk]
        a=np.searchsorted(T,t0,side='right'); e=np.searchsorted(T,t0+H*3600,side='left')
        if e>=len(T) or e<=a: continue
        if T[e]-(t0+H*3600)>3600 or T[a]-t0>3600: continue   # 데이터 공백 구간 제외
        last[tk]=t0
        p0=d.at[i,'price_krw']; w=P[a:e+1]
        adv = (w.max()/p0-1) if side=='short' else (1-w.min()/p0)   # 최대 역행폭
        ex=(P[e]/p0-1)*(1 if side=='long' else -1)
        if adv>=LIQ/lev: r=-LIQ*100; kind='liq'
        elif stop and adv>=stop: r=(-stop*lev-2*FEE*lev)*100; kind='stop'
        else: r=(ex*lev-2*FEE*lev)*100; kind='hold'
        out.append((r,kind))
    return out
def summ(name,o):
    if not o: print(f"{name}: n=0"); return
    r=np.array([x[0] for x in o]); liq=np.mean([x[1]=='liq' for x in o])
    bs=[r[np.random.randint(0,len(r),len(r))].mean() for _ in range(2000)]
    print(f"{name:34s} n={len(r):4d} 평균{r.mean():+7.1f}% [{np.percentile(bs,2.5):+6.1f},{np.percentile(bs,97.5):+6.1f}] 중앙{np.median(r):+6.1f}% 승률{(r>0).mean()*100:3.0f}% 청산{liq*100:3.0f}%")
np.random.seed(0)
sigs={
 '숏 횡보 pp_s>=20&rp>=15 (검증규칙)': ('short', d.index[(d.reg=='횡보')&(d.pp_s>=20)&(d.recent_pct>=15)]),
 '롱 횡보 pp_l>=10&rp<=-5 (검증규칙)': ('long',  d.index[(d.reg=='횡보')&(d.pp_l>=10)&(d.recent_pct<=-5)]),
 '숏 점수>=50 (전체레짐)': ('short', d.index[d.short_score>=50]),
 '롱 점수>=50 (전체레짐)': ('long', d.index[d.long_score>=50]),
 '숏 조건식': ('short', d.index[d.short_cond]),
 '롱 조건식': ('long', d.index[d.long_cond]),
 '기준선: 무작위 숏(전행)': ('short', d.index[::97]),
 '기준선: 무작위 롱(전행)': ('long', d.index[::97]),
}
import sys
for H in (12,24,48):
  for lev in (5,10,15):
    print(f"\n=== 보유 {H}h / 레버리지 {lev}x (청산선 {LIQ/lev*100:.1f}%) ===")
    for k,(side,idx) in sigs.items():
        summ(k, sim(idx,side,H,lev))
