import pandas as pd, numpy as np
X=pd.read_pickle('/home/claude/lt/XS.pkl')
X=X.dropna(subset=['f24','x24']).copy()
X['half']=np.where(X.ts<'2025-10-13','Y1(24.10~25.10)','Y2(25.10~26.10)')
def episodes(m, cd=12):
    d=X[m].sort_values(['ticker','ts']); keep=[]; last={}
    for tk,t in zip(d.ticker.values,d.ts.values):
        if tk in last and (t-last[tk])<np.timedelta64(cd,'h'): continue
        last[tk]=t; keep.append(True) if False else None
        keep.append((tk,t))
    k=pd.DataFrame(keep,columns=['ticker','ts'])
    return d.merge(k,on=['ticker','ts'])
def stat(e,sign):
    o={'n':len(e)}
    for h in [4,12,24]:
        o[f'win{h}']=round(((e[f'f{h}']*sign)>0).mean()*100,1)
        o[f'xs{h}']=round((e[f'x{h}']*sign).mean(),2)
    o['ret24']=round((e.f24*sign).mean(),2)
    o['liq10x_24h']=round(((e.dn24 if sign>0 else e.up24)>=9.5).mean()*100,1)
    return o
rows=[]
sigs={'판정 LONG':(X.verdict=='LONG',1),' └ 롱점수50':(X.v_why=='L50',1),' └ 매집80+조건':(X.v_why=='PP',1),
      '판정 SHORT':(X.verdict=='SHORT',-1),' └ 숏점수50':(X.v_why=='S50',-1),' └ 분산80+조건':(X.v_why=='PS',-1),
      '무작위 롱(기준)':(X.ts.dt.hour%6==0,1)}
for name,(m,s) in sigs.items():
    e=episodes(m)
    for hf in ['전체']+sorted(X.half.unique()):
        ee=e if hf=='전체' else e[e.half==hf]
        rows.append({'신호':name,'기간':hf,**stat(ee,s)})
    for rg in ['상승장','하락장','횡보장','고변동성']:
        ee=e[e.reg==rg]
        if len(ee)>=30: rows.append({'신호':name,'기간':'레짐:'+rg,**stat(ee,s)})
R=pd.DataFrame(rows); pd.set_option('display.width',250); pd.set_option('display.max_rows',200)
print(R.to_string(index=False))
R.to_csv('/home/claude/lt/ev1.csv',index=False)
# 점수 구간별 단조성
for col,s in [('long_score',1),('short_score',-1),('prepump_score',1),('preshort_score',-1)]:
    b=pd.cut(X[col],[-1,30,40,50,60,70,101])
    print(col, X.groupby(b,observed=True).apply(lambda g:pd.Series({'n':len(g),'x24':round((g.x24*s).mean(),2),'win24':round(((g.f24*s)>0).mean()*100,1)})).to_string())
