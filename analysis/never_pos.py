import pandas as pd, numpy as np
exec(open('sim_lev.py').read().split("FEE=0.0004")[0])
series={k:(g.t.values,g.price_krw.values.astype(float)) for k,g in d.groupby('ticker')}
d['hour']=pd.to_datetime(d.timestamp).dt.hour
def episodes(idx, side, H=12):
    rows=[];last={}
    for i in idx:
        tk=d.at[i,'ticker'];t0=d.at[i,'t']
        if tk in last and t0-last[tk]<12*3600: continue
        T,P=series[tk]
        a=np.searchsorted(T,t0,side='right'); e=np.searchsorted(T,t0+H*3600,side='left')
        if e>=len(T) or e<=a or T[e]-(t0+H*3600)>3600 or T[a]-t0>3600: continue
        last[tk]=t0
        p0=d.at[i,'price_krw']; w=P[a:e+1]
        if side=='long': mfe=w.max()/p0-1; mae=1-w.min()/p0; ex=P[e]/p0-1
        else: mfe=1-w.min()/p0; mae=w.max()/p0-1; ex=1-P[e]/p0
        # 첫 30분 내 수익 여부
        e30=np.searchsorted(T,t0+1800,side='right'); w30=P[a:e30] if e30>a else P[a:a+1]
        mfe30=(w30.max()/p0-1) if side=='long' else (1-w30.min()/p0)
        rows.append(dict(i=i,mfe=mfe*100,mae=mae*100,ex=ex*100,mfe30=mfe30*100))
    r=pd.DataFrame(rows); r=r.join(d.loc[r.i.values].reset_index(drop=True)); return r
sigs={'long_score>=50':('long',d.index[d.long_score>=50]),'short_score>=50':('short',d.index[d.short_score>=50]),
      'long_cond':('long',d.index[d.long_cond]),'short_cond':('short',d.index[d.short_cond]),
      '매집>=80(롱)':('long',d.index[d.prepump_score>=80]),'분산>=80(숏)':('short',d.index[d.preshort_score>=80])}
feats=['rsi','rsi_delta','bb_percent','recent_pct','atr_pct','vol_z','funding','oi_change_pct','ls_ratio','chg_30m_pct','pp_l','pp_s','long_score','short_score','prepump_score','preshort_score','vol_24h_m','hour']
import pickle
res={}
for name,(side,idx) in sigs.items():
    r=episodes(idx,side); r['never']=(r.mfe<=0.15)   # 수수료(왕복 0.08%)+여유 이하 = 12h 내내 수익권 못 감
    res[name]=r
    print(f"\n### {name}: n={len(r)}  12h 내내 수익권 못감 {r.never.mean()*100:.0f}% | 12h 종료 손실 {(r.ex<0).mean()*100:.0f}% | mfe중앙 {r.mfe.median():.2f}% mae중앙 {r.mae.median():.2f}%")
    print("   regime별 never%:", {k:f"{v*100:.0f}%({n})" for k,v,n in [(g,r[r.reg==g].never.mean(),(r.reg==g).sum()) for g in ['하락','횡보','상승'] if (r.reg==g).sum()]})
    if r.never.sum()>=8:
        from scipy.stats import mannwhitneyu
        out=[]
        for f in feats:
            a=r[r.never][f].dropna(); b=r[~r.never][f].dropna()
            if len(a)<5 or len(b)<5 or a.nunique()<2: continue
            u=mannwhitneyu(a,b); auc=u.statistic/(len(a)*len(b))
            out.append((abs(auc-0.5),f,auc,a.median(),b.median(),u.pvalue))
        for _,f,auc,ma,mb,p in sorted(out,reverse=True)[:6]:
            print(f"   {f:15s} never중앙{ma:9.2f} vs ok중앙{mb:9.2f}  AUC(never가 높을확률){auc:.2f} p={p:.3f}")
pickle.dump(res,open('never_res.pkl','wb'))
