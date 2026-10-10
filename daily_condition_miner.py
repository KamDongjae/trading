"""
daily_condition_miner.py
-------------------------
매일 쌓이는 market_data_raw_*.csv(원시 지표만, 점수 없음)를 넣으면:
  1) 1h/2h/4h 전방수익률을 직접 계산하고(±300초 오차범위)
  2) 지표 조합(최대 3개 AND)을 훑어서
  3) 4시간 승률 60% 이상인 것만 골라내고
  4) 겹치는(더 좁은 조건이 더 넓은 조건에 포함되는) 것들을 자동으로 정리한 뒤
  5) 롱/숏 각각 CSV + trading_client.py "조건식" 창에 바로 가져올 수 있는 txt 파일로 출력한다.

사용법:
    python3 daily_condition_miner.py market_data_raw_20260714_20260801.csv

매일 새 데이터가 쌓인 최신 raw CSV를 그대로 넣으면 되고, 실행할 때마다 그 시점까지의
전체 데이터로 새로 마이닝한다(과거 실행 결과에 안 얽매임 — 최신 데이터 기준으로 항상
다시 판단).

출력 파일(전부 실행한 폴더에 생성):
    daily_mined_all.csv          - 최소표본 넘긴 전체 조합(필터 전)
    daily_mined_long_60.csv      - 롱, 4h 승률 60%+ 만 (중복 정리 후)
    daily_mined_short_60.csv     - 숏, 4h 승률 60%+ 만 (중복 정리 후)
    daily_mined_long_import.txt  - 롱, 클라이언트 바로 가져오기용(OR로 묶은 조건 1개)
    daily_mined_short_import.txt - 숏, 클라이언트 바로 가져오기용(OR로 묶은 조건 1개)
"""
import sys
import itertools
import re
import pandas as pd
import numpy as np

# ============================================================
# 설정 — 필요하면 여기만 바꿔서 재실행
# ============================================================
MIN_WINRATE_4H = 0.60     # 이 미만이면 결과에서 제외(요청: 4h 승률 60% 이상만)
MIN_SAMPLES = 100         # 표본 100건 미만인 조합은 아예 안 봄(과적합 방지)
MAX_COMBO_SIZE = 3        # 최대 몇 개 지표까지 AND로 묶을지
TOP_N = 100                # 방향별로 최대 몇 개까지 저장할지(중복정리 후 그보다 적을 수 있음)
FWD_RETURN_TOLERANCE_SEC = 300  # 전방수익률 계산 시 허용 오차(초)

THRESHOLD_CANDIDATES = {
    "rsi":            [(20, "<"), (25, "<"), (30, "<"), (35, "<"), (40, "<"),
                        (60, ">"), (65, ">"), (70, ">"), (75, ">"), (80, ">")],
    "bb_percent":      [(0, "<"), (10, "<"), (20, "<"), (30, "<"),
                        (70, ">"), (80, ">"), (90, ">"), (100, ">")],
    "vol_z":           [(-1, "<"), (-0.5, "<"), (0, "<"),
                        (0.5, ">"), (1, ">"), (1.5, ">"), (2, ">"), (3, ">")],
    "rsi_delta":       [(-2, "<"), (-1, "<"), (0, "<"), (0, ">"), (1, ">"), (2, ">")],
    "chg_30m_pct":     [(-3, "<"), (-2, "<"), (-1, "<"), (1, ">"), (2, ">"), (3, ">")],
    "atr_pct":         [(0.3, ">"), (0.5, ">"), (1.0, ">"), (2.0, ">")],
    "oi_change_pct":   [(-5, "<"), (-3, "<"), (-1, "<"), (1, ">"), (3, ">"), (5, ">")],
    "ls_ratio":        [(0.7, "<"), (0.9, "<"), (1.1, ">"), (1.3, ">"), (1.5, ">")],
    "funding":         [(-0.01, "<"), (0, "<"), (0, ">"), (0.01, ">")],
    "recent_pct":      [(-10, "<"), (-5, "<"), (5, ">"), (10, ">")],
}
COL_TO_CLIENT_NAME = {"chg_30m_pct": "chg_30m", "price_krw": "price", "cvd_1h": "cvd"}
RETURN_COLS = {"1h": "ret_60m_pct", "2h": "ret_120m_pct", "4h": "ret_240m_pct"}


def add_forward_returns(df):
    """market_data_raw_*.csv(원시)에 1h/2h/4h 전방수익률 컬럼을 추가한다."""
    df = df.copy()
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    if 'exchange' not in df.columns:
        df['exchange'] = 'bithumb'
    group_cols = ['exchange', 'ticker']
    for minutes, label in [(60, '60m'), (120, '120m'), (240, '240m')]:
        out = []
        for _, g in df.groupby(group_cols):
            g = g.sort_values('timestamp').reset_index(drop=True)
            target = g['timestamp'] + pd.Timedelta(minutes=minutes)
            idx = np.searchsorted(g['timestamp'].values, target.values)
            idx_clip = np.clip(idx, 0, len(g) - 1)
            idx_prev = np.clip(idx - 1, 0, len(g) - 1)
            t_curr = g['timestamp'].values[idx_clip]
            t_prev = g['timestamp'].values[idx_prev]
            diff_curr = np.abs((pd.to_datetime(t_curr) - target).dt.total_seconds())
            diff_prev = np.abs((pd.to_datetime(t_prev) - target).dt.total_seconds())
            use_prev = diff_prev.values < diff_curr.values
            best_idx = np.where(use_prev, idx_prev, idx_clip)
            best_diff = np.where(use_prev, diff_prev.values, diff_curr.values)
            fwd_price = g['price_usd'].values[best_idx]
            ret = (fwd_price - g['price_usd'].values) / g['price_usd'].values * 100
            ret[best_diff > FWD_RETURN_TOLERANCE_SEC] = np.nan
            g[f'ret_{minutes}m_pct'] = ret
            out.append(g)
        merged = pd.concat(out, ignore_index=True)
        df = merged
    return df


def build_single_conditions():
    conds = []
    for ind, cands in THRESHOLD_CANDIDATES.items():
        for val, op in cands:
            conds.append((ind, op, val))
    return conds


def eval_mask(df, conds):
    mask = pd.Series(True, index=df.index)
    for col, op, val in conds:
        mask &= (df[col] > val) if op == ">" else (df[col] < val)
    return mask


def to_client_expr(conds):
    parts = []
    for col, op, val in conds:
        name = COL_TO_CLIENT_NAME.get(col, col)
        v = int(val) if float(val).is_integer() else val
        parts.append(f"{name} {op} {v}")
    return " && ".join(parts)


def evaluate_combo(df, conds, min_samples):
    mask = eval_mask(df, conds)
    n_total = int(mask.sum())
    if n_total < min_samples:
        return None
    result = {"condition": to_client_expr(conds), "n_지표수": len(conds), "n_total": n_total}
    ok_any = False
    for label, col in RETURN_COLS.items():
        sub = df.loc[mask, col].dropna()
        n = len(sub)
        result[f"n_{label}"] = n
        if n < min_samples // 2:
            result[f"winrate_long_{label}"] = np.nan
            result[f"winrate_short_{label}"] = np.nan
            result[f"meanret_long_{label}"] = np.nan
            result[f"meanret_short_{label}"] = np.nan
            continue
        ok_any = True
        result[f"winrate_long_{label}"] = round(float((sub > 0).mean()), 4)
        result[f"meanret_long_{label}"] = round(float(sub.mean()), 4)
        result[f"winrate_short_{label}"] = round(float((sub < 0).mean()), 4)
        result[f"meanret_short_{label}"] = round(float(-sub.mean()), 4)
    return result if ok_any else None


def mine(df, max_combo_size, min_samples):
    single_conds = build_single_conditions()
    print(f"단일 지표 후보: {len(single_conds)}개")
    all_results = []
    for c in single_conds:
        r = evaluate_combo(df, [c], min_samples)
        if r:
            all_results.append(r)
    print(f"  1개 조합: 통과 {len(all_results)}개")
    for size in range(2, max_combo_size + 1):
        before = len(all_results)
        checked = 0
        for combo in itertools.combinations(single_conds, size):
            cols = [c[0] for c in combo]
            if len(set(cols)) != len(cols):
                continue
            checked += 1
            r = evaluate_combo(df, list(combo), min_samples)
            if r:
                all_results.append(r)
        print(f"  {size}개 조합: 검토 {checked}개, 통과 +{len(all_results) - before}개")
    return pd.DataFrame(all_results)


def _parse_clauses(expr):
    out = []
    for c in expr.split("&&"):
        c = c.strip()
        m = re.match(r"(\w+)\s*(<|>)\s*(-?\d+\.?\d*)", c)
        out.append((m.group(1), m.group(2), float(m.group(3))))
    return out


def _a_dominates_b(a_clauses, b_clauses):
    """a가 b보다 넓은(느슨한) 조건이면 True — b는 중복이라 제거 대상.
    두 가지 경우를 다 잡는다:
      1) a의 절 개수가 b보다 적고(지표 하나를 덜 씀), a의 모든 절이 b에도 동일 지표+
         방향으로 있으면서 b쪽이 같거나 더 빡빡함 (기존 로직)
      2) a와 b가 지표+방향 구성이 완전히 같은데(절 개수도 같음), 값만 a가 전부 같거나
         더 느슨한 경우 — 예: "ls_ratio>1.1"이 "ls_ratio>1.5"보다 넓음. 이번에 실제로
         빠뜨렸던 케이스(같은 3개 지표, 임계값만 다른 여러 줄이 하나로 안 걸러졌음).
    완전히 동일한 조건(값까지 같음)은 어느 한쪽만 남기기 위해 리스트 순서로 타이브레이크.
    """
    a_dict = {}
    for ind, op, val in a_clauses:
        a_dict.setdefault((ind, op), []).append(val)
    b_dict = {}
    for ind, op, val in b_clauses:
        b_dict.setdefault((ind, op), []).append(val)

    if len(a_clauses) == len(b_clauses):
        if set(a_dict.keys()) != set(b_dict.keys()):
            return False
        strictly_looser_somewhere = False
        for key, a_vals in a_dict.items():
            ind, op = key
            b_vals = b_dict[key]
            a_val, b_val = a_vals[0], b_vals[0]
            if op == "<":
                if a_val < b_val:
                    return False  # a가 더 빡빡함 -> 지배 아님
                if a_val > b_val:
                    strictly_looser_somewhere = True
            else:
                if a_val > b_val:
                    return False
                if a_val < b_val:
                    strictly_looser_somewhere = True
        return strictly_looser_somewhere  # 완전히 동일하면(전부 같음) False로 두 줄 다 유지 안 하고 아래 별도 처리

    if len(a_clauses) >= len(b_clauses):
        return False
    for key, a_vals in a_dict.items():
        if key not in b_dict:
            return False
        ind, op = key
        a_val = a_vals[0]
        if op == "<":
            if not any(bv <= a_val for bv in b_dict[key]):
                return False
        else:
            if not any(bv >= a_val for bv in b_dict[key]):
                return False
    return True


def _dedup_exact_duplicates(conditions):
    """값까지 완전히 동일한 조건은 첫 번째만 남긴다(_a_dominates_b는 완전동일은 False라
    안 걸러지므로 별도 처리)."""
    seen = set()
    out = []
    for c in conditions:
        key = frozenset(_parse_clauses(c))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def dedup(conditions):
    """더 좁은(빡빡한) 조건이 이미 있는 더 넓은 조건에 포함되면 제거.
    완전히 동일한 조건(값까지 같음)도 먼저 하나로 정리한다."""
    conditions = _dedup_exact_duplicates(conditions)
    parsed = [(c, _parse_clauses(c)) for c in conditions]
    redundant = set()
    for i, (li, ci) in enumerate(parsed):
        for j, (lj, cj) in enumerate(parsed):
            if i == j:
                continue
            if _a_dominates_b(ci, cj):
                redundant.add(lj)
    return [c for c, _ in parsed if c not in redundant]


def filter_and_rank(results_df, direction, min_winrate_4h, top_n):
    wr4_col = f"winrate_{direction}_4h"
    df = results_df.dropna(subset=[wr4_col]).copy()
    df = df[df[wr4_col] >= min_winrate_4h]
    if df.empty:
        return df
    df = df.sort_values(wr4_col, ascending=False)
    kept_conditions = dedup(df["condition"].tolist())
    df = df[df["condition"].isin(kept_conditions)]
    df = df.sort_values(wr4_col, ascending=False).head(top_n).reset_index(drop=True)
    return df


def write_import_txt(conditions, color, path):
    if not conditions:
        with open(path, "w", encoding="utf-8") as f:
            f.write("# 조건식 없음(기준 통과한 것이 없음)\n")
        return
    combined = " || ".join(f"({c})" for c in conditions)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# 커스텀 조건식 내보내기\n")
        f.write("# 형식: 색상\\t사용여부(1/0)\\t알림여부(1/0)\\t조건식\n")
        f.write(f"{color}\t1\t0\t{combined}\n")


def main():
    if len(sys.argv) < 2:
        print("사용법: python3 daily_condition_miner.py <market_data_raw_*.csv>")
        sys.exit(1)
    path = sys.argv[1]
    print(f"데이터 로드: {path}")
    raw = pd.read_csv(path)
    print(f"  {len(raw)}행")
    missing = [c for c in THRESHOLD_CANDIDATES if c not in raw.columns]
    for m in missing:
        print(f"⚠️ 컬럼 없음, 탐색에서 제외: {m}")
        THRESHOLD_CANDIDATES.pop(m, None)

    print("전방수익률(1h/2h/4h) 계산 중...")
    df = add_forward_returns(raw)
    for label, col in RETURN_COLS.items():
        print(f"  {label}: {df[col].notna().sum()}/{len(df)}")

    print(f"\n조건 마이닝 시작 (최소표본 {MIN_SAMPLES}, 최대조합 {MAX_COMBO_SIZE}개)...")
    results = mine(df, MAX_COMBO_SIZE, MIN_SAMPLES)
    results.to_csv("daily_mined_all.csv", index=False, encoding="utf-8-sig")
    print(f"\n전체 결과: {len(results)}개 조합 -> daily_mined_all.csv")

    long_df = filter_and_rank(results, "long", MIN_WINRATE_4H, TOP_N)
    short_df = filter_and_rank(results, "short", MIN_WINRATE_4H, TOP_N)

    long_df.to_csv("daily_mined_long_60.csv", index=False, encoding="utf-8-sig")
    short_df.to_csv("daily_mined_short_60.csv", index=False, encoding="utf-8-sig")
    print(f"\n[롱] 4h 승률 {MIN_WINRATE_4H*100:.0f}%+ (중복정리 후): {len(long_df)}개 -> daily_mined_long_60.csv")
    print(f"[숏] 4h 승률 {MIN_WINRATE_4H*100:.0f}%+ (중복정리 후): {len(short_df)}개 -> daily_mined_short_60.csv")

    write_import_txt(long_df["condition"].tolist() if not long_df.empty else [],
                      "#2eaa4a", "daily_mined_long_import.txt")
    write_import_txt(short_df["condition"].tolist() if not short_df.empty else [],
                      "#d13a3a", "daily_mined_short_import.txt")
    print("\n가져오기용 txt 저장 완료: daily_mined_long_import.txt / daily_mined_short_import.txt")

    show_cols = ["condition", "n_total", "winrate_long_4h"]
    if not long_df.empty:
        print("\n=== 롱 상위 10개 ===")
        print(long_df[show_cols].head(10).to_string(index=False))
    show_cols_s = ["condition", "n_total", "winrate_short_4h"]
    if not short_df.empty:
        print("\n=== 숏 상위 10개 ===")
        print(short_df[show_cols_s].head(10).to_string(index=False))

    print(f"\n※ 표본 수(n_total)가 작은 조건은 다음 데이터 갱신 때 재현 안 될 위험이 "
          f"있습니다 — 이 프로젝트에서 표본 30~60건짜리 콤보가 실제로 재현 안 된 전례가 "
          f"여러 번 있었으니, n_total이 큰 것부터 우선 신뢰하세요.")


if __name__ == "__main__":
    main()
