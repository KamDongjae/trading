import pandas as pd, numpy as np, time, sys
import prob_model as pm
from pp7 import _prepump_v7
H='/mnt/user-data/uploads/trading/history'
frames=pm._load_history(H)
out=[]
for tk,d in frames.items():
    f=pm.build_features(d); f['ticker']=tk
    c=d.close; hi=d.high
    for h in [1,4,12,24,48]: f[f'f{h}']=(c.shift(-h)/c-1)*100
    for h in [12,24]:
        f[f'up{h}']=(hi[::-1].rolling(h,min_periods=h).max()[::-1].shift(-1)/c-1)*100
    f['box_high']=hi.rolling(20).max(); f['box_low']=d.low.rolling(20).min()
    f['ema20']=c.ewm(span=20,adjust=False).mean()
    f=f.iloc[200:]; out.append(f)
X=pd.concat(out).reset_index().rename(columns={'index':'ts'})
X=pm.add_cross_section(X); X=X[~X._stale].copy()
for h in [1,4,12,24,48]: X[f'x{h}']=X[f'f{h}']-X.groupby('ts')[f'f{h}'].transform('mean')
X['mkt_r4']=X.groupby('ts').r4.transform('mean')
reg=X.reg_c.map({2:'고변동성',1:'상승장',-1:'하락장',0:'횡보장'})
A=np.column_stack([X.p,X.box_high,X.box_low,X.ema20,X.rsi.fillna(50),X.recent_pct,X.atr_pct,X.price_cv_24h,reg])
X['pp']=[_prepump_v7(*r) if np.isfinite(r[5]) and np.isfinite(r[6]) else np.nan for r in A]
X=X.sort_values(['ts','ticker']).reset_index(drop=True)
print(X.shape,X.ts.min(),X.ts.max(),flush=True)
F=pm.FEATURES
X['p_up']=np.nan; X['exp4']=np.nan
months=pd.date_range('2025-01-01','2026-11-01',freq='MS')
for m in months:
    t=time.time()
    L=X[(X.ts<m-pd.Timedelta(days=3))&X.f24.notna()]; L=L[L.ts.dt.hour%2==0]
    te=(X.ts>=m)&(X.ts<m+pd.offsets.MonthBegin(1))
    clf=pm._new_model().fit(L[F],(L.f24>0).astype(int))
    L4=L[L.x4.notna()]; rg=pm._new_reg().fit(L4[F],L4.x4.clip(-15,15))
    X.loc[te,'p_up']=clf.predict_proba(X.loc[te,F])[:,1]; X.loc[te,'exp4']=rg.predict(X.loc[te,F])
    print(m.date(),len(L),round(time.time()-t),flush=True)
X.to_pickle('/home/claude/lt/WF.pkl')
print('done')
