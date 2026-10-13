import pandas as pd, numpy as np, sys, time
sys.path.insert(0,'/home/claude/test')
import trading_server as srv
X=pd.read_pickle('/home/claude/lt/X.pkl')
X=X.sort_values(['ticker','ts']).reset_index(drop=True)
# dwell hours (consecutive from end)
def dwell(b):
    g=(~b).cumsum(); return b.groupby([X.ticker,g]).cumsum().astype(float)
X['rsi_dwell_os_h']=dwell(X.rsi<40); X['rsi_dwell_ob_h']=dwell(X.rsi>60)
X['bb_dwell_low_h']=dwell(X.bb_percent<30); X['bb_dwell_high_h']=dwell(X.bb_percent>70)
X['chg_30m']=X.chg_1h/2  # 30분 변동 근사(1h봉 기반)
X=X.dropna(subset=['rsi','bb_percent','atr_pct','ema120','recent_pct','price_cv_24h','rsi_delta'])
cols=['rsi','bb_percent','cvd_diff','vol_window_sum','chg_30m','chg_1h','extension_pct','vol_z','rsi_delta','atr_pct','ema20','ema60','ema120','p','vol_24h_m','reg','ema20_slope_pct','recent_pct','price_cv_24h','rsi_dwell_os_h','bb_dwell_low_h','rsi_dwell_ob_h','bb_dwell_high_h','box_high','box_low']
A=X[cols].to_dict('records')
L=np.zeros(len(A),int);S=np.zeros(len(A),int);PP=np.zeros(len(A),int);PS=np.zeros(len(A),int)
t=time.time()
for i,r in enumerate(A):
    vz=0.0 if not np.isfinite(r['vol_z']) else r['vol_z']; cd=r['cvd_diff'] if np.isfinite(r['cvd_diff']) else 0.0
    ex=r['extension_pct'] if np.isfinite(r['extension_pct']) else 0.0
    L[i]=srv.calculate_long_score(r['rsi'],r['bb_percent'],cd,r['vol_window_sum'],1.0,0.0,r['chg_30m'],r['chg_1h'],ex,vz,r['rsi_delta'],r['atr_pct'],r['ema20'],r['ema60'],None,0.0,None,r['p'],r['ema120'],r['vol_24h_m'],r['reg'],exchange='bithumb',ema20_slope_pct=r['ema20_slope_pct'],recent_pct_3d=r['recent_pct'],price_cv_24h=r['price_cv_24h'],rsi_dwell_os_h=r['rsi_dwell_os_h'],bb_dwell_low_h=r['bb_dwell_low_h'])
    S[i]=srv.calculate_short_score(r['rsi'],r['bb_percent'],cd,r['vol_window_sum'],1.0,0.0,r['chg_30m'],r['chg_1h'],ex,vz,r['rsi_delta'],r['atr_pct'],r['ema20'],r['ema60'],None,0.0,None,r['p'],r['ema120'],r['vol_24h_m'],r['reg'],exchange='bithumb',ema20_slope_pct=r['ema20_slope_pct'],price_cv_24h=r['price_cv_24h'],rsi_dwell_ob_h=r['rsi_dwell_ob_h'],bb_dwell_high_h=r['bb_dwell_high_h'],recent_pct=r['recent_pct'])
    PP[i]=srv._prepump_v7(r['p'],r['box_high'],r['box_low'],r['ema20'],r['rsi'],r['recent_pct'],r['atr_pct'],r['price_cv_24h'],r['reg'])
    PS[i]=srv._preshort_v7(r['p'],r['box_high'],r['box_low'],r['ema20'],r['rsi'],r['recent_pct'],r['atr_pct'],r['bb_percent'])
    if i==20000: print('eta min',(time.time()-t)/20000*len(A)/60,flush=True)
X['long_raw']=L; X['short_score']=S; X['prepump_score']=PP; X['preshort_score']=PS
X['long_score']=np.where(X.atr_pct<srv.LONG_LOW_ATR_THRESHOLD,(X.long_raw*srv.LONG_LOW_ATR_FACTOR).astype(int),X.long_raw)
# verdict (OI/funding 없음 → oi=0, funding=0, ls=1.0)
E=X.assign(oi_change_pct=0.0,funding=0.0,ls_ratio=1.0)
tr=lambda s:s.replace('&&',' and ').replace('||',' or ')
lc=E.eval(tr(srv.VERDICT_LONG_COND)); sc=E.eval(tr(srv.VERDICT_SHORT_COND))
l50=X.long_score>=50; s50=X.short_score>=50; ppok=X.prepump_score>=80; psok=X.preshort_score>=80
shc=(s50|psok)&sc
X['verdict']=np.where((l50|(ppok&lc))&~shc,'LONG',np.where(shc&~l50,'SHORT',''))
X['v_why']=np.where(X.verdict=='LONG',np.where(l50,'L50','PP'),np.where(X.verdict=='SHORT',np.where(s50,'S50','PS'),''))
X.to_pickle('/home/claude/lt/XS.pkl')
print(X.verdict.value_counts(), X.v_why.value_counts())
