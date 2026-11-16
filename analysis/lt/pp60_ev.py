import pandas as pd, numpy as np, prob_model as pm
X=pd.read_pickle('/home/claude/lt/WF.pkl')
fr=pm._load_history('/mnt/user-data/uploads/trading/history')
hi=pd.concat([d[['high']].assign(ticker=t) for t,d in fr.items()]).reset_index().rename(columns={'index':'ts'})
X=X.merge(hi,on=['ts','ticker'],how='left')
X=X[X.p_up.notna()].sort_values(['ticker','ts']).reset_index(drop=True)
base=(X.exp4<-0.5)&(X.p_up<0.45)&~(X.mkt_r4>=1.0)
def verdicts(extra):
    cand=(base&extra).values
    ok=np.zeros(len(X),bool)
    for tk,idx in X.groupby('ticker').indices.items():
        idx=np.sort(idx); ts=X.ts.values[idx]; h=X.high.values[idx]; c=X.p.values[idx]
        watches=[]; block=np.datetime64('1970-01-01')
        for j,i in enumerate(idx):
            t=ts[j]
            nw=[]
            for (t0,p0) in watches:
                if t-t0>np.timedelta64(24,'h'): continue
                if t>t0 and h[j]>=p0*1.03: block=t+np.timedelta64(24,'h'); continue
                nw.append((t0,p0))
            watches=nw
            if cand[i] and t>=block:
                ok[i]=True; watches.append((t,c[j]))
    return ok
def episodes(m):
    s=X[m].sort_values('ts'); keep=[]; last={}
    for i,t,b in zip(s.index,s.ticker,s.ts):
        lb=last.get(t)
        if lb is None or b-lb>=pd.Timedelta(hours=12): keep.append(i); last[t]=b
    return X.loc[keep]
V0=verdicts(pd.Series(True,index=X.index)); V1=verdicts(X.pp<60)
E0=episodes(V0); E1=episodes(V1)
per=[('전체','2025-01-01','2027-01-01'),('25.01~06','2025-01-01','2025-07-01'),('25.07~12','2025-07-01','2026-01-01'),('26.01~06','2026-01-01','2026-07-01'),('26.07~11','2026-07-01','2026-12-01')]
H=[1,4,12,24,48]
def st(E):
    r={'n':len(E)}
    for h in H:
        e=E.dropna(subset=[f'f{h}']); r[f'w{h}']=(e[f'f{h}']<0).mean()*100; r[f'x{h}']=-e[f'x{h}'].mean()
    for h in [12,24]:
        e=E.dropna(subset=[f'up{h}']); r[f'liq{h}']=(e[f'up{h}']>=9).mean()*100
    e=E.dropna(subset=['f12','up12'])
    roi=np.where(e.up12>=9.4,-100,np.maximum(-100,-e.f12*10-7))
    r['roi']=roi.mean(); r['sum']=roi.sum(); r['nliq']=(e.up12>=9.4).sum()
    return r
rows=[]
for pn,a,b in per:
    for nm,E in [('현행',E0),('+매집<60',E1)]:
        e=E[(E.ts>=a)&(E.ts<b)]; rows.append(dict(per=pn,set=nm,**st(e)))
R=pd.DataFrame(rows).round(2); pd.set_option('display.width',250); pd.set_option('display.max_columns',40)
print(R.to_string())
# excluded group
Ex=E0[E0.pp>=60]
print('빠지는 쪽(pp>=60, 현행 에피소드 기준)'); print(pd.DataFrame([dict(per=pn,**st(Ex[(Ex.ts>=a)&(Ex.ts<b)])) for pn,a,b in per]).round(2).to_string())
# regimes
for nm,E in [('현행',E0),('+매집<60',E1)]:
    print(nm,{g:(len(e),round((e.f24<0).mean()*100,1),round(-e.x24.mean(),2)) for g,e in E.groupby(X.reg_c.loc[E.index])})
# pp bins within current episodes
E0b=E0.assign(b=pd.cut(E0.pp,[-1,20,40,60,80,101]))
print(E0b.groupby('b',observed=True).apply(lambda e:pd.Series(st(e)),include_groups=False).round(2).to_string())
# other cutoffs
for cut in [40,50,70,80]:
    E=episodes(verdicts(X.pp<cut)); s=st(E); print('pp<',cut,{k:round(v,2) for k,v in s.items()})
print('rand short w12',(X.f12<0).mean()*100,'w24',(X.f24<0).mean()*100)
R.to_csv('/home/claude/lt/res_pp60.csv',index=False)
