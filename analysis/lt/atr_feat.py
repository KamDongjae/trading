import pandas as pd, numpy as np, glob, os
from pp7 import _prepump_v7
SRC='/mnt/user-data/uploads/trading/history'
frames={}
for f in sorted(glob.glob(f'{SRC}/*_1h.csv'), key=lambda x: 'bithumb' in x):
    base=os.path.basename(f); ex,tk,_=base.split('_',2)
    if tk in frames: continue
    d=pd.read_csv(f); d['ts']=pd.to_datetime(d.ts_kst)
    d=d.set_index('ts').sort_index()[['open','high','low','close','volume','value']].astype(float)
    d=d[~d.index.duplicated()].asfreq('1h'); d['close']=d.close.ffill()
    for c in ['open','high','low']: d[c]=d[c].fillna(d.close)
    d['volume']=d.volume.fillna(0); frames[tk]=(d,ex)
out=[]
for tk,(d,ex) in frames.items():
    c=d.close; f=pd.DataFrame(index=d.index); f['ticker']=tk; f['ex']=ex; f['p']=c
    delta=c.diff(); g=delta.clip(lower=0); l=-delta.clip(upper=0)
    rs=g.ewm(alpha=1/14,adjust=False).mean()/l.ewm(alpha=1/14,adjust=False).mean().replace(0,np.nan)
    f['rsi']=100-100/(1+rs)
    ma=c.rolling(20).mean(); sd=c.rolling(20).std(); f['bb_percent']=(c-(ma-2*sd))/(4*sd)*100
    for n in [20,60,120]: f[f'ema{n}']=c.ewm(span=n,adjust=False).mean()
    tr=pd.concat([d.high-d.low,(d.high-c.shift()).abs(),(d.low-c.shift()).abs()],axis=1).max(axis=1)
    f['atr_pct']=(tr.rolling(14).mean()/c*100).round(2)
    f['recent_pct']=(c/c.shift(72)-1)*100
    f['price_cv_24h']=c.rolling(24).std()/c.rolling(24).mean()
    f['box_high']=d.high.rolling(20).max(); f['box_low']=d.low.rolling(20).min()
    f['chg_30m']=(c/c.shift(1)-1)*100/2
    f['r24b']=(c/c.shift(24)-1)*100
    for h in [1,4,12,24,48]:
        f[f'f{h}']=(c.shift(-h)/c-1)*100
        lo=d.low[::-1].rolling(h,min_periods=h).min()[::-1].shift(-1)
        f[f'mae{h}']=(lo/c-1)*100
    f['stale']=d.volume.rolling(24).sum()==0
    out.append(f)
X=pd.concat(out).reset_index().rename(columns={'index':'ts'})
X=X[X.ts>=X.ts.min()+pd.Timedelta(days=10)]; X=X[~X.stale]
bull=(X.ema20>X.ema60)&(X.ema60>X.ema120)&(X.p>X.ema20); bear=(X.ema20<X.ema60)&(X.ema60<X.ema120)&(X.p<X.ema20)
X['_b']=bull; X['_s']=bear; gb=X.groupby('ts'); nb=gb._b.transform('sum'); ns=gb._s.transform('sum'); aatr=gb.atr_pct.transform('mean')
tb=(nb-ns)/(nb+ns).replace(0,np.nan)
X['reg']=np.select([aatr>=2,tb>=0.15,tb<=-0.15],['고변동성','상승장','하락장'],'횡보장')
for h in [1,4,12,24,48]: X[f'x{h}']=X[f'f{h}']-X.groupby('ts')[f'f{h}'].transform('mean')
X['mkt24b']=X.groupby('ts').r24b.transform('mean')
X=X.dropna(subset=['rsi','bb_percent','atr_pct','ema120','recent_pct','price_cv_24h','box_high'])
A=X[['p','box_high','box_low','ema20','rsi','recent_pct','atr_pct','price_cv_24h','reg']].to_numpy()
X['pp']=[_prepump_v7(*r) for r in A]
X.drop(columns=['_b','_s','stale']).reset_index(drop=True).to_pickle('X.pkl')
print(X.shape,X.ts.min(),X.ts.max(),X.ticker.nunique(),(X.pp>=80).mean())
