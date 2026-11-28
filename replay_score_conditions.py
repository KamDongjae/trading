from pathlib import Path
import numpy as np
import pandas as pd
from replay_entry_protection import simulate

root = Path.cwd()
features = ['rsi','rsi_delta','bb_percent','ls_ratio','funding','vol_z','recent_pct',
            'atr_pct','chg_30m','oi_change_pct','price_usd','max_lev','exp4',
            'prepump_score','preshort_score']
columns = ['chg_30m_pct' if column == 'chg_30m' else column for column in features]
columns += ['timestamp','ticker','liq_risk','verdict_reason']
test_rules = {
    'long_rebound': '(rsi < 40 or bb_percent < 30) and rsi_delta > 0 and chg_30m >= 0',
    'long_breakout': 'bb_percent > 70 and vol_z > 1 and chg_30m > 0',
    'short_turn': '(rsi >= 60 or recent_pct > 5) and rsi_delta < 0 and chg_30m < 0',
    'short_trend': 'rsi < 45 and bb_percent < 20 and rsi_delta < 0 and chg_30m < 0',
}

def load(files):
    frames = [pd.read_csv(path, usecols=columns, dtype=str) for path in files]
    data = pd.concat(frames, ignore_index=True).rename(columns={'timestamp':'ts','chg_30m_pct':'chg_30m'})
    for col in features:
        data[col] = pd.to_numeric(data[col], errors='coerce')
    data.ts = pd.to_datetime(data.ts, errors='coerce')
    return (data.dropna(subset=['ts','ticker','price_usd'])
            .loc[lambda d: (d.ts >= '2026-11-23') & (d.ts < '2026-11-29') & (d.price_usd > 0)]
            .sort_values(['ticker','ts']).drop_duplicates(['ticker','ts']).reset_index(drop=True))

def color(data, side):
    filename = f'daily_mined_{side}_import_curated.txt'
    rule = (root / filename).read_text(encoding='utf-8-sig').splitlines()[-1].split('\t')[-1]
    return data.eval(rule.replace('&&',' and ').replace('||',' or '), engine='python')

def build(data, hold_h):
    flags = pd.DataFrame({name:data.eval(rule,engine='python') for name,rule in test_rules.items()})
    no_futures = (data.liq_risk.fillna('').str.contains('NOFUT', regex=False) |
                  data.verdict_reason.fillna('').str.contains('선물 없음', regex=False))
    eligible = (data.max_lev >= 3) & ~no_futures
    trades = []
    omitted = 0
    for side, symbol in [('long',1),('short',-1)]:
        active = color(data,side)
        fresh = active & ~active.groupby(data.ticker).shift(fill_value=False)
        for ticker, block in data.groupby('ticker',sort=False):
            candidates = np.flatnonzero(fresh.loc[block.index].to_numpy())
            if not len(candidates):
                continue
            ts = block.ts.to_numpy(dtype='datetime64[s]').astype('int64')
            px = block.price_usd.to_numpy(dtype=float)
            available = 0
            for i in candidates:
                if ts[i] < available:
                    continue
                enter = i+1
                if enter == len(ts) or ts[enter]-ts[i] > 1200:
                    omitted += 1
                    continue
                exit_idx = np.searchsorted(ts,ts[enter]+hold_h*3600)
                if exit_idx == len(ts) or ts[exit_idx]-ts[enter]-hold_h*3600 > 1200 or np.max(np.diff(ts[enter:exit_idx+1]))>1800:
                    omitted += 1
                    continue
                available = ts[exit_idx]
                row = block.iloc[i]
                fill = px[enter]*(1+symbol*.007)
                net = np.maximum(-30,90*symbol*(px[enter:exit_idx+1]*(1-symbol*.007)/fill-1))-.072
                candidate = (ts[i],row.ts,ticker,symbol,
                             row.prepump_score if symbol==1 else row.preshort_score,
                             ts[enter:exit_idx+1],net)
                trades.append((candidate, bool(eligible.loc[row.name]), row.exp4*symbol,
                               row.atr_pct, flags.loc[row.name].to_dict()))
    return trades, omitted

cases = {
    'baseline': (False,'score',None,None),
    'risk': (True,'score',None,None),
    'expected': (True,'expected',None,None),
    'expected/ATR': (True,'adjusted',None,None),
    'long_rebound': (True,'score','long_rebound',None),
    'long_breakout': (True,'score','long_breakout',None),
    'short_turn': (True,'score',None,'short_turn'),
    'short_trend': (True,'score',None,'short_trend'),
    'combo_rebound_turn': (True,'adjusted','long_rebound','short_turn'),
    'combo_breakout_trend': (True,'adjusted','long_breakout','short_trend'),
}
out = ['# Score and condition replay', '',
       'Nov 23–28 KST, with Nov 27 onward held out for comparison. Archived curated colors are replayed from inputs, and may differ from colors active in the client. Conditional variants narrow the original color signals. All rows have the same cap of 3 per exact time and direction. Fixed USD 30, 3x, 0.7% slippage and 0.04% fee each side. Entry uses the next quote. No cash cap or intrabar liquidation. Risk gate requires logged max_lev ≥ 3 and checks explicit NOFUT fields only. Model rank falls back below any available model score, ties by ticker.']
for name, files in [('Binance',[root/'market_data_log_binance_202611.csv']),
                    ('Bithumb',sorted(root.glob('market_data_log_v3_2026112[36]_*.csv')))]:
    data = load(files)
    for hold in (12,48):
        trades, omitted = build(data,hold)
        out.extend(['',f'## {name} {hold}h, {len(trades)} usable, {omitted} incomplete paths','',
                    '| period | rule | trades | net USD | max drawdown USD | wins |',
                    '|---|---|---:|---:|---:|---:|'])
        print(name,hold,len(trades),omitted,flush=True)
        for period in ('all','early','late'):
            for label,(risk,rank,long_rule,short_rule) in cases.items():
                selected = []
                for trade, eligible, exp, atr, flags in trades:
                    late = trade[1] >= pd.Timestamp('2026-11-27')
                    rule = long_rule if trade[3]==1 else short_rule
                    if (period=='early' and late) or (period=='late' and not late) or (risk and not eligible) or (rule and not flags[rule]):
                        continue
                    value = trade[4] if rank=='score' else exp if rank=='expected' else exp/max(atr,.5)
                    value = value if np.isfinite(value) else -999
                    selected.append((*trade[:4],value,*trade[5:]))
                n,pnl,dd,_,wins = simulate(selected,3,None)
                out.append(f'| {period} | {label} | {n} | {pnl:+.2f} | {dd:.2f} | {wins} |')
(root/'reports'/'score_conditions_validation_20261128.md').write_text('\n'.join(out)+'\n',encoding='utf-8')
