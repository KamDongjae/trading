"""
debug_resolve.py — signal_outcomes.csv에서 제일 오래된 미해결 신호 하나를 골라서,
resolve_signal_outcomes()가 왜 못 넘기는지 단계별로 출력한다.
trading_server.py랑 같은 폴더에서 실행: python3 debug_resolve.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trading_server as srv
import pandas as pd
from datetime import datetime

df = srv._load_signal_log()
df['opened_ts'] = pd.to_datetime(df['opened_ts'])
now = datetime.now()
df['age_min'] = (now - df['opened_ts']).dt.total_seconds() / 60

pending = df[~df['resolved']].sort_values('opened_ts')
print(f"미해결 신호 총 {len(pending)}건")
if pending.empty:
    print("미해결 신호가 없음 — 전부 해결됐거나 신호 자체가 없음")
    sys.exit()

row = pending.iloc[0]
print(f"\n제일 오래된 미해결 신호:")
print(f"  {row['exchange']} / {row['ticker']} / {row['direction']} / opened_ts={row['opened_ts']} / 경과={row['age_min']:.1f}분")
print(f"  entry_price={row['entry_price']}")

if row['age_min'] < srv.RESOLVE_AFTER_MIN:
    print(f"\n→ 아직 {srv.RESOLVE_AFTER_MIN}분 안 지남({row['age_min']:.1f}분 경과) — 정상 대기 상태, 버그 아님")
    sys.exit()

print(f"\n→ {srv.RESOLVE_AFTER_MIN}분 지났는데 아직 미해결 — 캔들 조회 테스트:")
fetch_fn = srv.fetch_candlestick_upbit if row['exchange'] == 'upbit' else srv.fetch_candlestick
candle = fetch_fn(row['ticker'], chart_intervals="1h", timeout=8, retries=1)

if candle is None:
    print("  ❌ 캔들 조회 자체가 실패함(None 리턴) — 네트워크/API 문제일 가능성. 콘솔에 '캔들 조회 실패' 로그 확인해봐.")
    sys.exit()
print(f"  ✅ 캔들 {len(candle)}개 받음. 범위: {candle.index.min()} ~ {candle.index.max()}")

entry_time = row['opened_ts']
print(f"\n  entry_time = {entry_time}")
print(f"  entry_time - 30분 = {entry_time - pd.Timedelta(minutes=30)}")
print(f"  entry_time + 150분 = {entry_time + pd.Timedelta(minutes=150)}")

window = candle[(candle.index >= entry_time - pd.Timedelta(minutes=30)) &
                 (candle.index <= entry_time + pd.Timedelta(minutes=150))]
print(f"\n  이 구간에 걸리는 캔들 행 수: {len(window)}")
if len(window) < 2:
    print("  ❌ 여기서 막힘 — 캔들 시간대랑 entry_time이 안 겹침(타임존/범위 문제 의심).")
    print(f"  캔들 index 예시(앞 5개): {list(candle.index[:5])}")
    print(f"  캔들 index 예시(뒤 5개): {list(candle.index[-5:])}")
else:
    print("  ✅ 정상적으로 걸림 — 이 신호는 다음 주기에 해결될 것으로 보임")
