import pandas as pd, numpy as np, glob, os, sys
sys.path.insert(0,'/home/claude/test')
import trading_server as srv
SRC='/mnt/user-data/uploads/trading/history'
frames={}
for f in sorted(glob.glob(f'{SRC}/*_1h.csv')):
    base=os.path.basename(f); ex,tk,_=base.split('_',2)
    if tk in frames and ex=='bithumb': continue
    d=pd.read_csv(f); d['ts']=pd.to_datetime(d.ts_kst)
    d=d.set_index('ts').sort_index()[['open','high','low','close','volume','value']].astype(float)
    d=d[~d.index.duplicated()]
    d=d.asfreq('1h'); d['close']=d.close.ffill(); 
    for c in ['open','high','low']: d[c]=d[c].fillna(d.close)
    d['volume']=d.volume.fillna(0); d['value']=d.value.fillna(0)
    frames[tk]=d
print('tickers',len(frames))
out=[]
for tk,d in frames.items():
    c=d.close
    f=pd.DataFrame(index=d.index); f['ticker']=tk; f['p']=c
    delta=c.diff(); g=delta.clip(lower=0); l=-delta.clip(upper=0)
    rs=g.ewm(alpha=1/14,adjust=False).mean()/l.ewm(alpha=1/14,adjust=False).mean().replace(0,np.nan)
    f['rsi']=100-100/(1+rs); f['rsi_delta']=f.rsi-f.rsi.shift(4)
    ma=c.rolling(20).mean(); sd=c.rolling(20).std()
    f['bb_percent']=((c-(ma-2*sd))/(4*sd)*100)
    for n in [20,60,120]: f[f'ema{n}']=c.ewm(span=n,adjust=False).mean()
    f['ema20_slope_pct']=(f.ema20/f.ema20.shift(3)-1)*100
    tr=pd.concat([d.high-d.low,(d.high-c.shift()).abs(),(d.low-c.shift()).abs()],axis=1).max(axis=1)
    f['atr_pct']=(tr.rolling(14).mean()/c*100).round(2)
    f['vol_z']=((d.volume-d.volume.rolling(20).mean())/d.volume.rolling(20).std()).round(2)
    rng=(d.high-d.low).replace(0,np.nan); mfv=(((c-d.low)-(d.high-c))/rng).fillna(0)*d.volume
    cvd=mfv.rolling(2,min_periods=1).sum(); f['cvd_diff']=cvd-cvd.shift(1); f['vol_window_sum']=d.volume.rolling(2).sum()
    f['extension_pct']=(c/c.shift(10)-1)*100
    f['recent_pct']=(c/c.shift(72)-1)*100
    f['price_cv_24h']=c.rolling(24).std()/c.rolling(24).mean()
    f['box_high']=d.high.rolling(20).max(); f['box_low']=d.low.rolling(20).min()
    f['vol_24h_m']=(d.value.rolling(24).sum()/1e6).round()
    f['chg_1h']=(c/c.shift(1)-1)*100
    for h in [1,4,24,72,168,720]: f[f'r{h}']=(c/c.shift(h)-1)*100
    for h in [4,12,24,48,72]: f[f'f{h}']=(c.shift(-h)/c-1)*100
    lo=d.low[::-1].rolling(24,min_periods=24).min()[::-1].shift(-1); hi=d.high[::-1].rolling(24,min_periods=24).max()[::-1].shift(-1)
    f['dn24']=(1-lo/c)*100; f['up24']=(hi/c-1)*100
    f['stale']=d.volume.rolling(24).sum()==0
    out.append(f)
X=pd.concat(out).reset_index().rename(columns={'index':'ts'})
X=X[X.ts>=X.ts.min()+pd.Timedelta(days=10)]
X=X[~X.stale]
# market context + regime proxy (server detect_market_regime 방식)
g=X.groupby('ts')
X['mkt_r24']=g.r24.transform('mean'); X['mkt_r72']=g.r72.transform('mean')
X['breadth']=g.apply(lambda s:((s.p>s.ema60)).mean()).reindex(X.ts).values if False else X.assign(b=(X.p>X.ema60)).groupby('ts').b.transform('mean')
bull=((X.ema20>X.ema60)&(X.ema60>X.ema120)&(X.p>X.ema20)); bear=((X.ema20<X.ema60)&(X.ema60<X.ema120)&(X.p<X.ema20))
X['_b']=bull; X['_s']=bear
gb=X.groupby('ts'); nb=gb._b.transform('sum'); ns=gb._s.transform('sum'); aatr=gb.atr_pct.transform('mean')
tb=(nb-ns)/(nb+ns).replace(0,np.nan)
X['reg']=np.select([aatr>=2,tb>=0.15,tb<=-0.15],['고변동성','상승장','하락장'],'횡보장')
btc=X[X.ticker=='BTC'].set_index('ts')[['r24','r72','r168']].add_prefix('btc_')
X=X.merge(btc,left_on='ts',right_index=True,how='left')
for h in [4,12,24,48,72]: X[f'x{h}']=X[f'f{h}']-X.groupby('ts')[f'f{h}'].transform('mean')
X.drop(columns=['_b','_s','stale']).to_pickle('/home/claude/lt/X.pkl')
print(X.shape, X.ts.min(), X.ts.max(), X.reg.value_counts(normalize=True).round(2).to_dict())
