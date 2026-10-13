import pandas as pd, numpy as np
T=pd.read_pickle('/home/claude/lt/T.pkl')
T['rk_up']=T.groupby('ts').p_up.rank(ascending=False); T['rk_dn']=T.groupby('ts').p_up.rank()
def ep(m,cd=12):
    d=T[m].sort_values(['ticker','ts']); keep=[]; last={}
    for i,(tk,t) in enumerate(zip(d.ticker.values,d.ts.values)):
        if tk in last and (t-last[tk])<np.timedelta64(cd,'h'): continue
        last[tk]=t; keep.append(i)
    return d.iloc[keep]
def st(e,s):
    return {'n':len(e),**{f'승{h}':round(((e[f'f{h}']*s)>0).mean()*100,1) for h in [4,12,24]},'초과24':round((e.x24*s).mean(),2),'수익24':round((e.f24*s).mean(),2),'10x청산24h':round(((e.dn24 if s>0 else e.up24)>=9.5).mean()*100,1)}
rows=[]
for name,m,s in [('현행 판정 LONG',T.verdict=='LONG',1),('ML 롱 p≥0.60·상위3',(T.p_up>=0.60)&(T.rk_up<=3),1),('ML 롱 p≥0.65·상위3',(T.p_up>=0.65)&(T.rk_up<=3),1),
                 ('현행 판정 SHORT',T.verdict=='SHORT',-1),('ML 숏 p≤0.40·하위3',(T.p_up<=0.40)&(T.rk_dn<=3),-1),('ML 숏 p≤0.35·하위3',(T.p_up<=0.35)&(T.rk_dn<=3),-1),
                 ('판정LONG ∩ ML p≥0.55',(T.verdict=='LONG')&(T.p_up>=0.55),1),('판정SHORT ∩ ML p≤0.45',(T.verdict=='SHORT')&(T.p_up<=0.45),-1)]:
    e=ep(m)
    for hf in ['전체','A(25.04~25.12)','B(26.01~26.10)']:
        ee=e if hf=='전체' else e[e.half==hf]
        rows.append({'신호':name,'기간':hf,**st(ee,s)})
pd.set_option('display.width',250); print(pd.DataFrame(rows).to_string(index=False))
# 캘리브레이션
b=pd.cut(T.p_up,[0,.35,.4,.45,.5,.55,.6,.65,1])
print(T.groupby(b,observed=True).apply(lambda g:pd.Series({'n':len(g),'실제상승%':round((g.f24>0).mean()*100,1)})).to_string())
print('월별 시장 평균 24h', T.groupby(T.ts.dt.to_period('Q')).f24.mean().round(2).to_dict())
