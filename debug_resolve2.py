"""
debug_resolve2.py — 실제 서버 함수 resolve_signal_outcomes()를 직접 호출해서
몇 건이 해결됐는지, 안 됐으면 왜 안 됐는지(t60/t120 단계까지) 정확히 찍는다.
trading_server.py랑 같은 폴더에서 실행: python3 debug_resolve2.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trading_server as srv
import pandas as pd
from datetime import datetime

print("=" * 50)
print("1단계: 실제 resolve_signal_outcomes() 직접 호출")
print("=" * 50)
try:
    n = srv.resolve_signal_outcomes()
    print(f"반환값(새로 해결된 건수): {n}")
except Exception as e:
    import traceback
    print(f"❌ 예외 발생: {e}")
    traceback.print_exc()

print()
print("=" * 50)
print("2단계: 호출 후 실제 CSV 상태 재확인")
print("=" * 50)
df = srv._load_signal_log()
resolved_count = int(df['resolved'].sum())
print(f"전체 {len(df)}건 중 해결 {resolved_count}건")

print()
print("=" * 50)
print("3단계: 제일 오래된 미해결 신호를 t60/t120까지 수동으로 끝까지 추적")
print("=" * 50)
df['opened_ts'] = pd.to_datetime(df['opened_ts'])
pending = df[~df['resolved']].sort_values('opened_ts')
if pending.empty:
    print("미해결 신호 없음")
    sys.exit()

row = pending.iloc[0]
print(f"대상: {row['exchange']} / {row['ticker']} / {row['direction']} / opened_ts={row['opened_ts']}")
entry_time = row['opened_ts']
entry_price = float(row['entry_price'])
print(f"entry_price={entry_price}")

fetch_fn = srv.fetch_candlestick_upbit if row['exchange'] == 'upbit' else srv.fetch_candlestick
candle = fetch_fn(row['ticker'], chart_intervals="1h", timeout=8, retries=1)
if candle is None:
    print("❌ 캔들 조회 실패")
    sys.exit()
print(f"캔들 {len(candle)}개, 범위 {candle.index.min()} ~ {candle.index.max()}")

window = candle[(candle.index >= entry_time - pd.Timedelta(minutes=30)) &
                 (candle.index <= entry_time + pd.Timedelta(minutes=150))]
print(f"\nwindow 행 수: {len(window)}")
print(window)

t60 = window[window.index >= entry_time + pd.Timedelta(minutes=45)]
t120 = window[window.index >= entry_time + pd.Timedelta(minutes=105)]
print(f"\nt60 (index >= {entry_time + pd.Timedelta(minutes=45)}) 행 수: {len(t60)}")
print(t60)
print(f"\nt120 (index >= {entry_time + pd.Timedelta(minutes=105)}) 행 수: {len(t120)}")
print(t120)

if entry_price <= 0:
    print("\n❌ entry_price가 0 이하 — 여기서 continue 당함(이게 원인일 수 있음)")

ret60 = (t60.iloc[0]['close'] - entry_price) / entry_price * 100 if len(t60) else None
ret120 = (t120.iloc[0]['close'] - entry_price) / entry_price * 100 if len(t120) else None
print(f"\nret60={ret60}, ret120={ret120}")
if ret60 is None and ret120 is None:
    print("❌ 여기서 최종적으로 막힘 — ret60/ret120 둘 다 None이라 continue 당함")
else:
    print("✅ 여기까진 정상 — 이 신호는 resolved=True로 바뀌어야 정상")
