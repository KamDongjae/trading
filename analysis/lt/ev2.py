import pandas as pd, numpy as np
X=pd.read_pickle('/home/claude/lt/XM.pkl')
T=X[X.pred_x.notna()&X.f24.notna()].copy()
T['half']=np.where(T.ts<'2026-01-01','A(25.04~25.12)','B(26.01~26.10)')
# 1) IC
def ic(col,sign=1):
    s=T[T.ts.dt.hour%4==0].groupby('ts').apply(lambda g:g[col].corr(g.x24,method='spearman')*sign)
    return round(s.mean(),3), round(s.mean()/s.std()*np.sqrt(len(s)/6),1)
print('IC(24h 초과수익과 순위상관, 평균 / t)')
for c,s in [('pred_x',1),('p_up',1),('recent_pct',-1),('r24',-1),('rsi',-1),('long_score',1),('short_score',-1),('prepump_score',1),('preshort_score',-1)]:
    print(f'  {c:15s}',ic(c,s), ' A',T[T.half.str[0]=='A'].pipe(lambda d:None) or '')
for hf in sorted(T.half.unique()):
    d=T[(T.half==hf)&(T.ts.dt.hour%4==0)]
    print(hf,{c:round(d.groupby('ts').apply(lambda g:g[c].corr(g.x24,method='spearman')*s).mean(),3) for c,s in [('pred_x',1),('p_up',1),('recent_pct',-1),('long_score',1),('short_score',-1)]})
# 2) 일일 포트폴리오 (09시 진입, 24h 보유, 수수료 왕복 0.1%)
D=T[T.ts.dt.hour==9].copy()
FEE=0.1
def port(name,longsel,shortsel):
    out=[]
    for ts,g in D.groupby('ts'):
        L=g[longsel(g)]; S=g[shortsel(g)]
        rl=L.f24.mean()-FEE if len(L) else np.nan; rs=-S.f24.mean()-FEE if len(S) else np.nan
        out.append((ts,rl,rs,len(L),len(S)))
    o=pd.DataFrame(out,columns=['ts','L','S','nL','nS']).set_index('ts')
    o['LS']=o[['L','S']].mean(axis=1)
    res=[]
    for leg in ['L','S','LS']:
        for hf,sub in [('전체',o),('A',o[o.index<'2026-01-01']),('B',o[o.index>='2026-01-01'])]:
            r=sub[leg].fillna(0)/100; act=sub[leg].notna()
            eq=(1+r).cumprod(); mdd=(eq/eq.cummax()-1).min()*100
            res.append({'전략':name,'다리':leg,'기간':hf,'거래일':int(act.sum()),'일평균%':round(sub[leg].mean(),3),'승률%':round((sub[leg][act]>0).mean()*100,1),
                        '누적%':round((eq.iloc[-1]-1)*100,1),'MDD%':round(mdd,1),'샤프(연)':round(r[act].mean()/r[act].std()*np.sqrt(365),2) if act.sum()>5 else np.nan})
    return res
K=5
rk=lambda g,c,asc: g[c].rank(ascending=asc,method='first')<=K
R=[]
R+=port('현행 판정',lambda g:g.verdict=='LONG',lambda g:g.verdict=='SHORT')
R+=port('3일 역추세 순위',lambda g:rk(g,'recent_pct',True),lambda g:rk(g,'recent_pct',False))
R+=port('ML 순위(초과수익)',lambda g:rk(g,'pred_x',False),lambda g:rk(g,'pred_x',True))
R+=port('ML 확률 p≥0.55',lambda g:(g.p_up>=0.55)&rk(g,'p_up',False),lambda g:(g.p_up<=0.45)&rk(g,'p_up',True))
R+=port('BTC 보유',lambda g:g.ticker=='BTC',lambda g:g.ticker=='__')
R+=port('전체 균등(시장)',lambda g:g.ticker==g.ticker,lambda g:g.ticker=='__')
P=pd.DataFrame(R); pd.set_option('display.width',250); pd.set_option('display.max_rows',300)
print(P.to_string(index=False)); P.to_csv('/home/claude/lt/ev2_port.csv',index=False)
T.to_pickle('/home/claude/lt/T.pkl')
