"""
debug_flat_scores.py — 모든 코인 점수가 몇 개 값으로만 뭉쳐 나오는 문제 진단.
trading_server.py랑 같은 폴더에서: python3 debug_flat_scores.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trading_server as srv

print("=" * 50)
print("1) 로지스틱 모델 상태")
print("=" * 50)
for ex in srv.EXCHANGES:
    for d in ('long', 'short'):
        m = srv.logistic_score_models.get(ex, {}).get(d)
        if m:
            print(f"  [{ex}/{d}] 모델 있음 — weights={[round(w,3) for w in m['weights']]}, bias={m['bias']:.3f}")
            print(f"           features={m.get('features')}")
        else:
            print(f"  [{ex}/{d}] 모델 없음 (가산식 fallback 사용 중)")

print()
print("=" * 50)
print("2) 아이소토닉 보정표 상태 — 항등변환(0..100 그대로)이 아니면 학습된 것")
print("=" * 50)
for ex in srv.EXCHANGES:
    for d in ('long', 'short'):
        tbl = srv.score_calibration.get(ex, {}).get(d, [])
        is_identity = tbl == list(range(101))
        unique_vals = sorted(set(tbl))
        print(f"  [{ex}/{d}] 항등변환 여부: {is_identity} | 서로 다른 출력값 개수: {len(unique_vals)}개")
        if not is_identity:
            print(f"           실제 나오는 값들: {unique_vals}")

print()
print("=" * 50)
print("3) 실제 score_cache에서 코인별 점수 분포 확인")
print("=" * 50)
for ex, cache in [('bithumb', srv.score_cache), ('upbit', srv.score_cache_upbit)]:
    if not cache:
        print(f"  [{ex}] score_cache 비어있음(서버 방금 켰으면 정상)")
        continue
    longs = [r.get('long_score') for r in cache.values() if r.get('long_score') is not None]
    shorts = [r.get('short_score') for r in cache.values() if r.get('short_score') is not None]
    print(f"  [{ex}] 코인 {len(cache)}개")
    print(f"    롱점수 고유값: {sorted(set(longs))}")
    print(f"    숏점수 고유값: {sorted(set(shorts))}")
