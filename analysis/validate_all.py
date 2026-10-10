import pandas as pd, numpy as np, re, warnings
warnings.filterwarnings('ignore')
d=pd.read_csv('market_data_log_v3_postgap_merged.csv')
d['timestamp']=pd.to_datetime(d.timestamp)
d=d.sort_values(['ticker','timestamp']).reset_index(drop=True)
d['t']=d.timestamp.astype('datetime64[s]').astype('int64')
SPLIT=pd.Timestamp('2026-10-08')
# 레짐 프록시: 6h 시장평균 recent_pct, 3분위 경계는 학습구간으로만 산정
d['b']=d.t//(6*3600)
m=d.groupby('b').recent_pct.mean()
bt=d[d.timestamp<SPLIT].b.unique(); q1,q2=m.loc[bt].quantile([1/3,2/3])
d['reg']=d.b.map(lambda b:'하락' if m[b]<=q1 else ('횡보' if m[b]<=q2 else '상승'))
def cond(fn):
    out=pd.Series(False,index=d.index)
    for ln in open(fn,encoding='utf-8'):
        p=ln.rstrip('\n').split('\t')
        if len(p)>=4 and p[1]=='1':
            e=p[3].replace('&&',' and ').replace('||',' or ')
            try: out|=d.eval(e).fillna(False)
            except Exception as ex: print('cond err',ex)
    return out
d['long_cond']=cond('/mnt/attach/outputs/long_conditions_import_v3.txt')
d['short_cond']=cond('/mnt/attach/outputs/short_conditions_import.txt')
print('조건식 신호 행수',d.long_cond.sum(),d.short_cond.sum())
FEE=0.0004;LIQ=0.9
series={k:(g.t.values,g.price_krw.values.astype(float)) for k,g in d.groupby('ticker')}
def eps(idx,side,H=12,cool=12):
    rows=[];last={}
    for i in idx:
        tk=d.at[i,'ticker'];t0=d.at[i,'t']
        if tk in last and t0-last[tk]<cool*3600: continue
        T,P=series[tk]
        a=np.searchsorted(T,t0,side='right');e=np.searchsorted(T,t0+H*3600,side='left')
        if e>=len(T) or e<=a or T[e]-(t0+H*3600)>3600 or T[a]-t0>3600: continue
        last[tk]=t0;p0=d.at[i,'price_krw'];w=P[a:e+1]
        if side=='long': mfe=w.max()/p0-1;mae=1-w.min()/p0;ex=P[e]/p0-1
        else: mfe=1-w.min()/p0;mae=w.max()/p0-1;ex=1-P[e]/p0
        rows.append((i,mfe*100,mae*100,ex*100))
    r=pd.DataFrame(rows,columns=['i','mfe','mae','ex']); return r.join(d.loc[r.i.values].reset_index(drop=True))
def lev_ret(r,lev):
    liq=r.mae>=LIQ/lev*100
    return np.where(liq,-LIQ*100,r.ex*lev-2*FEE*lev*100)
def st(r):
    if len(r)==0: return "n=  0"
    l10=lev_ret(r,10)
    return (f"n={len(r):3d} 미양전{(r.mfe<=.15).mean()*100:3.0f}% 12h손실{(r.ex<0).mean()*100:3.0f}% "
            f"12h평균{r.ex.mean():+5.2f}% 10x평균{l10.mean():+6.1f}% 승률{(r.ex>0).mean()*100:3.0f}%")
def cmp(name,side,base_mask,filt_mask):
    print(f"\n■ {name}")
    for per,pm in (('학습(~10/7)',d.timestamp<SPLIT),('홀드아웃(10/8~)',d.timestamp>=SPLIT)):
        b=eps(d.index[base_mask&pm],side); f=b[b.i.isin(d.index[filt_mask])]; x=b[~b.i.isin(d.index[filt_mask])]
        print(f" {per}\n   기준    : {st(b)}\n   필터통과: {st(f)}\n   필터탈락: {st(x)}")
# ---- 점수 필터 (로그된 점수에 오프라인으로 감산 규칙 적용; 로그 점수가 이미 감산본인지 점검)
lowatr=d.atr_pct<0.9
for per,pm in (('학습',d.timestamp<SPLIT),('홀드아웃',d.timestamp>=SPLIT)):
    s=pm&(d.long_score>=50)
    print(f"[점검] {per}: long_score>=50 중 ATR<0.9 비율 {lowatr[s].mean()*100:.1f}% (n={s.sum()})  | 매집>=80 중 BB>40 비율 {(d.bb_percent>40)[pm&(d.prepump_score>=80)].mean()*100:.1f}%")
cmp('롱 점수>=50 : ATR>=0.9 필터','long',d.long_score>=50,d.atr_pct>=0.9)
cmp('매집>=80(롱) : BB<=40 필터','long',d.prepump_score>=80,d.bb_percent<=40)
cmp('롱 조건식 : BB<=50 필터','long',d.long_cond,d.bb_percent<=50)
cmp('숏 점수>=50 (필터 없음, 비교용)','short',d.short_score>=50,d.short_score>=50)
cmp('숏 조건식 (필터 없음, 비교용)','short',d.short_cond,d.short_cond)
cmp('분산>=80(숏)','short',d.preshort_score>=80,d.preshort_score>=80)
cmp('횡보 숏 규칙 pp_s>=20&rp>=15','short',(d.reg=='횡보')&(d.pp_s>=20)&(d.recent_pct>=15),(d.reg=='횡보')&(d.pp_s>=20)&(d.recent_pct>=15))
cmp('횡보 롱 규칙 pp_l>=10&rp<=-5','long',(d.reg=='횡보')&(d.pp_l>=10)&(d.recent_pct<=-5),(d.reg=='횡보')&(d.pp_l>=10)&(d.recent_pct<=-5))
# 시장 드리프트
for per,pm in (('학습',d.timestamp<SPLIT),('홀드아웃',d.timestamp>=SPLIT)):
    ix=d.index[pm][::53]
    print(f"\n[기준선 {per}] 무작위 롱 {st(eps(ix,'long',cool=0))}\n                 무작위 숏 {st(eps(ix,'short',cool=0))}")
    print("   레짐 분포:",d[pm].reg.value_counts(normalize=True).round(2).to_dict())
