import pandas as pd, numpy as np, time
from sklearn.ensemble import HistGradientBoostingRegressor, HistGradientBoostingClassifier
X=pd.read_pickle('/home/claude/lt/XS.pkl').sort_values(['ts','ticker']).reset_index(drop=True)
X['d_e20']=(X.p/X.ema20-1)*100; X['d_e60']=(X.ema20/X.ema60-1)*100; X['d_e120']=(X.ema60/X.ema120-1)*100
X['boxpos']=(X.p-X.box_low)/(X.box_high-X.box_low).replace(0,np.nan)
X['lvol']=np.log1p(X.vol_24h_m.clip(lower=0))
for c in ['r24','r72','r168','r720','rsi','atr_pct','vol_z']:
    X[f'rk_{c}']=X.groupby('ts')[c].rank(pct=True)
X['reg_c']=X.reg.map({'상승장':1,'하락장':-1,'횡보장':0,'고변동성':2})
X['hour']=X.ts.dt.hour
F=['rsi','rsi_delta','bb_percent','d_e20','d_e60','d_e120','ema20_slope_pct','atr_pct','vol_z','extension_pct','recent_pct','price_cv_24h','boxpos','lvol',
   'r1','r4','r24','r72','r168','r720','mkt_r24','mkt_r72','breadth','btc_r24','btc_r72','btc_r168','reg_c',
   'rk_r24','rk_r72','rk_r168','rk_r720','rk_rsi','rk_atr_pct','rk_vol_z','rsi_dwell_os_h','rsi_dwell_ob_h','long_score','short_score','prepump_score','preshort_score']
months=pd.date_range('2025-04-01','2026-10-01',freq='MS')
X['pred_x']=np.nan; X['p_up']=np.nan
for m in months:
    t=time.time()
    tr=X[(X.ts<m-pd.Timedelta(days=3))&X.f24.notna()]
    tr=tr[tr.ts.dt.hour%2==0]
    te=(X.ts>=m)&(X.ts<m+pd.offsets.MonthBegin(1))
    if te.sum()==0: continue
    yr=tr.x24.clip(-15,15)
    reg=HistGradientBoostingRegressor(max_iter=200,learning_rate=0.05,max_leaf_nodes=31,min_samples_leaf=400,l2_regularization=1.0).fit(tr[F],yr)
    clf=HistGradientBoostingClassifier(max_iter=200,learning_rate=0.05,max_leaf_nodes=31,min_samples_leaf=400,l2_regularization=1.0).fit(tr[F],(tr.f24>0).astype(int))
    X.loc[te,'pred_x']=reg.predict(X.loc[te,F]); X.loc[te,'p_up']=clf.predict_proba(X.loc[te,F])[:,1]
    print(m.date(),len(tr),round(time.time()-t,1),flush=True)
X.to_pickle('/home/claude/lt/XM.pkl')
