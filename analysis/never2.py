import pandas as pd, numpy as np, pickle
from scipy.stats import mannwhitneyu
res=pickle.load(open('never_res.pkl','rb'))
feats=['rsi','rsi_delta','bb_percent','recent_pct','atr_pct','vol_z','funding','oi_change_pct','ls_ratio','chg_30m_pct','vol_24h_m','hour']
def show(name,r):
    r=r.copy(); r['neg']=r.ex<0
    print(f"\n### {name} n={len(r)} 12h종료손실 {r.neg.mean()*100:.0f}%")
    out=[]
    for f in feats:
        a=r[r.neg][f].dropna(); b=r[~r.neg][f].dropna()
        if len(a)<8 or len(b)<8 or a.nunique()<2: continue
        u=mannwhitneyu(a,b); auc=u.statistic/(len(a)*len(b)); out.append((abs(auc-.5),f,auc,a.median(),b.median(),u.pvalue))
    for _,f,auc,ma,mb,p in sorted(out,reverse=True)[:5]:
        print(f"   {f:14s} 손실군 {ma:9.2f} vs 이익군 {mb:9.2f} AUC {auc:.2f} p={p:.3f}")
for k in ['long_score>=50','short_score>=50','매집>=80(롱)','long_cond','short_cond']: show(k,res[k])
# 필터 후보 테스트 (전/후반 분할)
def test(name,r,mask):
    r=r.sort_values('timestamp'); h=len(r)//2
    def st(x): 
        return f"n={len(x):3d} never{(x.mfe<=0.15).mean()*100:3.0f}% 12h손실{(x.ex<0).mean()*100:3.0f}% 평균12h{x.ex.mean():+.2f}% mfe중앙{x.mfe.median():.2f}%"
    m=mask(r)
    print(f"\n[{name}]\n  전체 기준 : {st(r)}\n  필터 통과 : {st(r[m])}\n  필터 탈락 : {st(r[~m])}")
    print(f"  전반 통과 : {st(r.iloc[:h][m.iloc[:h]])} | 탈락 {st(r.iloc[:h][~m.iloc[:h]])}")
    print(f"  후반 통과 : {st(r.iloc[h:][m.iloc[h:]])} | 탈락 {st(r.iloc[h:][~m.iloc[h:]])}")
r=res['long_score>=50']
for thr in (0.8,0.9,1.0):
    test(f'long_score>=50 & atr>={thr}',r,lambda x,t=thr:(x.atr_pct>=t))
test('long_score>=50 & not(횡보 & atr<1)',r,lambda x:~((x.reg=='횡보')&(x.atr_pct<1.0)))
r=res['매집>=80(롱)']
test('매집>=80 & recent_pct<=-0.5',r,lambda x:x.recent_pct<=-0.5)
test('매집>=80 & rsi<=47',r,lambda x:x.rsi<=47)
test('매집>=80 & bb<=40',r,lambda x:x.bb_percent<=40)
print("\n=========== long_cond 추가 ===========")
r=res['long_cond']
test('long_cond & rsi<=50',r,lambda x:x.rsi<=50)
test('long_cond & bb<=50',r,lambda x:x.bb_percent<=50)
r=res['short_cond']
test('short_cond & vol_z<=0',r,lambda x:x.vol_z<=0)
