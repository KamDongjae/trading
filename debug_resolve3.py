"""
debug_resolve3.py — resolve_signal_outcomes() 내부 로직을 그대로 복사하되, 매 단계마다
print를 심어서 실제로 어디서 continue 당하는지 정확히 추적한다.
trading_server.py랑 같은 폴더에서 실행: python3 debug_resolve3.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trading_server as srv
import pandas as pd
from datetime import datetime

df = srv._load_signal_log()
print(f"전체 로드: {len(df)}행")
print(f"resolved 컬럼 dtype: {df['resolved'].dtype}, 고유값: {df['resolved'].unique()}")

df['opened_ts'] = pd.to_datetime(df['opened_ts'])
now = datetime.now()
pending_mask = (~df['resolved']) & ((now - df['opened_ts']).dt.total_seconds() >= srv.RESOLVE_AFTER_MIN * 60)
print(f"pending_mask True 개수: {pending_mask.sum()}")

if not pending_mask.any():
    print("pending_mask가 전부 False — 여기서 이미 0건으로 끝남(진짜 원인일 수 있음)")
    sys.exit()

pairs = df.loc[pending_mask, ['exchange', 'ticker']].drop_duplicates()
print(f"고유 (거래소,티커) 쌍: {len(pairs)}개")
print(pairs.head(10).to_string())

newly_resolved = 0
checked = 0
for exch, ticker in pairs.itertuples(index=False):
    checked += 1
    if checked > 8:
        print("... (처음 8개만 상세 추적, 이후 생략) ...")
        break
    print(f"\n--- [{checked}] {exch}/{ticker} ---")
    fetch_fn = srv.fetch_candlestick_upbit if exch == 'upbit' else srv.fetch_candlestick
    try:
        candle = fetch_fn(ticker, chart_intervals="1h", timeout=8, retries=1)
    except Exception as e:
        print(f"  ❌ fetch_fn 자체에서 예외 발생: {e}")
        continue
    if candle is None or len(candle) < 3:
        print(f"  ❌ candle이 None이거나 3개 미만(candle={None if candle is None else len(candle)}) — continue")
        continue
    print(f"  ✅ 캔들 {len(candle)}개")

    idxs = df.index[pending_mask & (df['ticker'] == ticker) & (df['exchange'] == exch)]
    print(f"  이 쌍에 해당하는 미해결 신호 인덱스: {len(idxs)}개")

    for idx in idxs[:3]:  # 티커당 최대 3개만 상세 추적
        try:
            row = df.loc[idx]
            entry_time = row['opened_ts']
            entry_price = float(row['entry_price'])
            print(f"    idx={idx} entry_time={entry_time} entry_price={entry_price}")
            if entry_price <= 0:
                print(f"    ❌ entry_price<=0 — continue")
                continue
            window = candle[(candle.index >= entry_time - pd.Timedelta(minutes=30)) &
                             (candle.index <= entry_time + pd.Timedelta(minutes=150))]
            if len(window) < 2:
                print(f"    ❌ window 행수={len(window)} (<2) — continue")
                continue
            t60 = window[window.index >= entry_time + pd.Timedelta(minutes=45)]
            t120 = window[window.index >= entry_time + pd.Timedelta(minutes=105)]
            ret60 = (t60.iloc[0]['close'] - entry_price) / entry_price * 100 if len(t60) else None
            ret120 = (t120.iloc[0]['close'] - entry_price) / entry_price * 100 if len(t120) else None
            if ret60 is None and ret120 is None:
                print(f"    ❌ ret60/ret120 둘 다 None — continue")
                continue
            print(f"    ✅ ret60={ret60}, ret120={ret120} — 여기까지 왔으면 resolved=True 처리됨")
            newly_resolved += 1
        except Exception as e:
            print(f"    ❌ 내부 try/except에서 예외 발생(조용히 삼켜지는 부분): {e!r}")
            continue

print(f"\n최종 집계(추적된 것만): newly_resolved={newly_resolved}")
