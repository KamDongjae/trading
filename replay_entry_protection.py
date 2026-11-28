from collections import defaultdict
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
FEE = .0004
SLIP = .007

def annual_data():
    source = ROOT.parent / 'trading_cache/binance_v3_preds_1-300.pkl'
    p = pd.read_pickle(source).sort_values(['ticker', 'ts'])
    guard = (p.age_d >= 730) & (p.atr_pct < 2.5) & (p.r168 < 50)
    p['side'] = np.select(
        [guard & (p.p_up >= .55) & (p.exp4 > .2),
         guard & (p.p_up < .45) & (p.exp4 < -.5) & (p.mkt_r4 < 1)],
        [1, -1])
    p['rank'] = p.exp4 * p.side
    return p[['ticker','ts','side','rank']], '2025-11-23', '2026-11-23', '2026-05-23'

def recent_data():
    source = ROOT / 'market_data_log_binance_202611.csv'
    columns = ['timestamp', 'ticker', 'price_usd', 'verdict', 'prepump_score', 'preshort_score']
    p = pd.read_csv(source, usecols=columns, dtype=str)
    p = p.rename(columns={'timestamp':'ts','price_usd':'price'})
    p['ts'] = pd.to_datetime(p.ts, errors='coerce')
    for column in ('price', 'prepump_score', 'preshort_score'):
        p[column] = pd.to_numeric(p[column], errors='coerce')
    p = p.dropna(subset=['ts', 'ticker', 'price'])
    p = p.sort_values(['ticker','ts']).drop_duplicates(['ticker','ts'])
    p['side'] = p.verdict.map({'LONG':1,'SHORT':-1}).fillna(0).astype(int)
    p['rank'] = pd.Series(np.where(p.side == 1,p.prepump_score,p.preshort_score), index=p.index).fillna(-1)
    return p, '2026-11-23', '2026-11-29', '2026-11-27'

def events(p, start, end, hours, annual):
    hold = hours * 3600
    candidates = p[(p.side != 0) & (p.side != p.groupby('ticker').side.shift())
                   & (p.ts >= start) & (p.ts < end)]
    missing = 0
    trades = []
    for ticker, rows in candidates.groupby('ticker'):
        if annual:
            file = ROOT / 'history_binance' / f'binance_{ticker}_1h.csv'
            if not file.exists():
                missing += len(rows)
                continue
            prices = pd.read_csv(file,usecols=['ts_kst','open']).rename(columns={'ts_kst':'ts','open':'price'})
            prices.ts = pd.to_datetime(prices.ts)
        else:
            prices = p.loc[p.ticker == ticker,['ts','price']]
        prices = prices.dropna().sort_values('ts').drop_duplicates('ts')
        ts = prices.ts.to_numpy(dtype='datetime64[s]').astype('int64')
        px = prices.price.to_numpy(dtype=float)
        available = 0
        for row in rows.itertuples(index=False):
            signal = int(row.ts.value // 10**9)
            if signal < available:
                continue
            i = np.searchsorted(ts,signal,side='right')
            if i == len(ts) or ts[i]-signal > (3600 if annual else 1200):
                missing += 1
                continue
            j = np.searchsorted(ts,ts[i]+hold)
            if j == len(ts) or ts[j]-ts[i]-hold > (0 if annual else 1200) or np.max(np.diff(ts[i:j+1])) > (3600 if annual else 1800):
                missing += 1
                continue
            available = ts[j]
            fill = px[i]*(1+row.side*SLIP)
            notional = 90
            gross = row.side*(px[i:j+1]*(1-row.side*SLIP)/fill-1)
            net = np.maximum(-30,notional*gross)-2*notional*FEE
            trades.append((signal,row.ts,ticker,row.side,row.rank,ts[i:j+1],net))
    return trades, missing

def simulate(trades, cap, trigger):
    batches = defaultdict(list)
    for t in trades:
        batches[t[0],t[3]].append(t)
    selected = [t for batch in batches.values()
                for t in sorted(batch,key=lambda t:(-t[4],t[2]))[:cap]]
    flow = defaultdict(float)
    outcomes = []
    for _,_,_,_,_,ts,net in selected:
        portion = 1
        locked = peak = previous = 0.
        for time,value in zip(ts,net):
            if trigger is not None and portion == 1 and value >= trigger:
                portion = .5
                locked = .5*value
                peak = value
            elif portion == .5:
                peak = max(peak,value)
                if value <= max(0,peak-trigger):
                    flow[time] += locked+portion*value-previous
                    break
            updated = locked+portion*value
            flow[time] += updated-previous
            previous = updated
        outcomes.append(locked+portion*value)
    equity = np.cumsum([0]+[flow[t] for t in sorted(flow)])
    high = np.maximum.accumulate(equity)
    return len(selected), equity[-1], (high-equity).max(), high[-1]-equity[-1], sum(x>0 for x in outcomes)

def main():
    lines = ['# Entry limits and profit protection replay', '',
             'All scenarios use $30 margin and 3x leverage, 0.7% slippage and 0.04% fee each side. Entry is the next observed price (next hourly open for annual). Positions have a common time exit. USD PnL and drawdown sum independent trades without a cash cap. Liquidation and intrabar execution are not simulated. Skip paths with price gaps over the stated data interval limit.', '',
             'Annual sample uses cached V3 estimates and ranks by directional exp4. Recent sample uses logged Binance verdicts and prepump/preshort scores. Both use new signals and no overlapping base trades in the same ticker. Caps group exact signal timestamps and side. Half closes at the chosen net profit threshold (percent of entry notional), the remainder trails by that amount with a zero net-PnL floor checked only at observed prices.']
    for label,load,annual in [('Annual',annual_data,True),('Recent',recent_data,False)]:
        p,start,end,split = load()
        for hours in (12,48):
            trades,missing=events(p,start,end,hours,annual)
            lines += ['',f'## {label} {hours}h | {start} to {end} exclusive | omitted {missing}', '',
                      '| period | cap | threshold | trades | net USD | max drawdown USD | peak giveback USD | wins |',
                      '|---|---:|---:|---:|---:|---:|---:|---:|']
            for name, subset in [('all',trades),('early',[t for t in trades if t[1]<pd.Timestamp(split)]),('late',[t for t in trades if t[1]>=pd.Timestamp(split)])]:
                for cap in (99999,2,3):
                    for percent in (None,1.5,2,2.5,3):
                        n,pnl,dd,giveback,wins=simulate(subset,cap,None if percent is None else 90*percent/100)
                        lines.append(f'| {name} | {"none" if cap==99999 else cap} | {percent if percent is not None else "off"} | {n} | {pnl:+.2f} | {dd:.2f} | {giveback:.2f} | {wins} |')
            print(label, hours, len(trades), missing, flush=True)
    (ROOT/'reports'/'entry_protection_validation_20261128.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

if __name__ == '__main__':
    main()
