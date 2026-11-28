import heapq
import math
from pathlib import Path
import pandas as pd
import binance_backtest2 as b2
import binance_backtest3 as b3
import binance_backtest14 as b14

cache = Path('../trading_cache/binance_v3_preds_1-300.pkl')
pred = pd.read_pickle(cache)
end = pred.ts.max()
start = end - pd.DateOffset(years=1)
split = start + pd.DateOffset(months=6)
print(f'Forecast cache: {len(pred):,}, {start} to {end}', flush=True)
frames = b2.load_dir(b2.BIN_DIR, 'binance_*_1h.csv')
pred = pred[pred.ts >= start - pd.Timedelta(days=7)].copy()
ref = frames['BTC']['close'].pct_change(24)
pred['mkt_r24'] = pred.ts.map(ref)
b14.AGE_MIN = 600
b14.ATR_MAX = 3.0
b3.EXP4_MAX = -0.3
events = []
for side in ('short', 'long'):
    chosen = b14.events(pred, side, bear_proxy=(side == 'long'), guard_mode='any')
    for event in chosen:
        events.append(event)
events.sort(key=lambda event: (event[0], -abs(event[3]), event[1]))
print(f'{len(events):,} entry signals, {len(frames)} markets', flush=True)
terminal = end - pd.Timedelta(hours=48)

def simulate(hours, leverage, sides):
    cash = 1000.0
    positions = {}
    exits = []
    closed = []
    blocked_until = {}
    last_shown = {}
    skipped_cash = 0
    cutoff = 1 / leverage - 0.02
    cost = leverage * 0.014
    sequence = 0

    def settle(ts):
        nonlocal cash
        while exits and exits[0][0] <= ts:
            closed_at, _, ticker, trade = heapq.heappop(exits)
            del positions[ticker]
            cash += trade['amount'] + trade['pnl']
            closed.append(trade)
            if trade['reason'] == 'liq':
                blocked_until[ticker, trade['side']] = closed_at + pd.Timedelta(days=7)

    for ts, ticker, side, exp4, *_ in events:
        if side not in sides or ts > terminal:
            continue
        key = (ticker, side)
        last = last_shown.get(key)
        last_shown[key] = ts
        if ts < start or (last is not None and ts - last < pd.Timedelta(hours=6)):
            continue
        settle(ts)
        if ticker in positions or (key in blocked_until and ts < blocked_until[key]):
            continue
        d = frames.get(ticker)
        if d is None or ts not in d.index:
            continue
        i = d.index.get_loc(ts)
        entry = float(d.close.iloc[i])
        future = d.iloc[i+1:i+1+hours]
        if len(future) != hours or future.index[-1] != ts + pd.Timedelta(hours=hours):
            continue
        equity = cash
        for tk, trade in positions.items():
            prices = frames[tk].close
            current = float(prices.asof(ts))
            equity += trade['amount'] * (1 + leverage * trade['direction'] * (current / trade['entry'] - 1))
        amount = math.floor(max(1000., equity) * 0.03) * (2 if side == 'short' and exp4 <= -1 else 1)
        if cash < amount:
            skipped_cash += 1
            continue
        direction = 1 if side == 'long' else -1
        best = 0.0
        for bar in future.itertuples():
            adverse = 1 - bar.low / entry if direction == 1 else bar.high / entry - 1
            gain = direction * (bar.close / entry - 1)
            if adverse >= cutoff:
                roi, reason = -0.9, 'liq'
                break
            best = max(best, gain)
            if best >= 0.15 and best - gain >= 0.03:
                roi, reason = leverage * gain - cost, 'trail'
                break
        else:
            roi, reason = leverage * gain - cost, 'time'
        cash -= amount
        trade = dict(ts=ts, exit_ts=bar.Index, ticker=ticker, side=side, amount=amount,
                     entry=entry, direction=direction, pnl=round(amount * roi, 2), reason=reason)
        positions[ticker] = trade
        sequence += 1
        heapq.heappush(exits, (bar.Index, sequence, ticker, trade))
    settle(end)
    trades = pd.DataFrame(closed)
    pnl = trades.pnl.sum()
    realized = pd.concat([pd.Series([0.0]), trades.pnl.cumsum()], ignore_index=True)
    return dict(hold_h=hours, leverage=leverage, scope='+'.join(sides), trades=len(trades),
                pnl=round(pnl, 2), equity=round(1000 + pnl, 2), win_pct=round(100 * (trades.pnl > 0).mean(), 2),
                liquidations=int((trades.reason == 'liq').sum()), trails=int((trades.reason == 'trail').sum()),
                realized_drawdown=round((realized - realized.cummax()).min(), 2), cash_skips=skipped_cash,
                first_half=round(trades.loc[trades.ts < split, 'pnl'].sum(), 2),
                second_half=round(trades.loc[trades.ts >= split, 'pnl'].sum(), 2),
                long_pnl=round(trades.loc[trades.side == 'long', 'pnl'].sum(), 2),
                short_pnl=round(trades.loc[trades.side == 'short', 'pnl'].sum(), 2))

for sides in [('long', 'short'), ('short',)]:
    for hours, leverage in [(48, 3), (9, 5)]:
        print(simulate(hours, leverage, sides), flush=True)
print(f'Last eligible entry: {terminal}', flush=True)
