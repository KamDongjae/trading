import pandas as pd, numpy as np
X=pd.read_pickle('X.pkl').sort_values(['ts','ticker']).reset_index(drop=True)
v7=(X.rsi<35)|((X.recent_pct<-5)&(X.chg_30m<-1))
pp=X.pp>=80; atr=X.atr_pct
def episodes(m):
    s=X[m]; keep=[]; last={}
    for i,t,b in zip(s.index,s.ticker,s.ts):
        lb=last.get(t)
        if lb is None or (b-lb)>=pd.Timedelta(hours=12): keep.append(i); last[t]=b
    return X.loc[keep]
H=[1,4,12,24,48]
def st(E):
    r={}
    for h in H:
        e=E.dropna(subset=[f'f{h}'])
        r[h]=((e[f'f{h}']>0).mean()*100, e[f'x{h}'].mean(), (e[f'mae{h}']<=-9).mean()*100, len(e))
    return r
per=[('24.10~12','2024-10-01','2025-01-01'),('25.01~06','2025-01-01','2025-07-01'),('25.07~12','2025-07-01','2026-01-01'),('26.01~06','2026-01-01','2026-07-01'),('26.07~11','2026-07-01','2026-12-01')]
sets={'pp≥80':pp,'pp≥80 & ATR≥1.2':pp&(atr>=1.2),'pp≥80 & ATR<1.2':pp&(atr<1.2),
      'pp≥80&v7':pp&v7,'pp≥80&v7 & ATR≥1.2':pp&v7&(atr>=1.2),'pp≥80&v7 & ATR<1.2':pp&v7&(atr<1.2),
      'pp≥80&v7 & ATR≥1.0':pp&v7&(atr>=1.0),'pp≥80&v7 & ATR≥1.5':pp&v7&(atr>=1.5)}
EP={k:episodes(m) for k,m in sets.items()}
rows=[]
for k,E in EP.items():
    for pn,a,b in [('전체','2000','2100')]+per:
        e=E[(E.ts>=a)&(E.ts<b)]; s=st(e)
        rows.append(dict(set=k,per=pn,n=len(e),**{f'w{h}':round(s[h][0],1) for h in H},**{f'x{h}':round(s[h][1],2) for h in H},**{f'liq{h}':round(s[h][2],1) for h in H}))
R=pd.DataFrame(rows); R.to_csv('res.csv',index=False)
pd.set_option('display.width',250); pd.set_option('display.max_columns',30)
print(R[['set','per','n','w1','w4','w12','w24','w48','x12','x24','liq12','liq24','liq48']].to_string())
# regime split
for k in ['pp≥80&v7','pp≥80&v7 & ATR≥1.2','pp≥80','pp≥80 & ATR≥1.2']:
    E=EP[k]; print(k,{g:(len(e),round((e.f24>0).mean()*100,1),round(e.x24.mean(),2)) for g,e in E.groupby('reg')})
# random baseline
print('rand w24',(X.f24>0).mean()*100, 'w12',(X.f12>0).mean()*100)
