# -*- coding: utf-8 -*-
"""
screener_prepump.py (v3)
-------------------------
data_collector.py가 쌓아둔 market_data_log CSV(예: market_data_log_v3_20260703_20260705.csv)를
오프라인으로 돌려서 "매집(prepump)" / "분산(preshort)" 신호를 스크리닝하는 독립 스크립트.

trading_server.py의 calculate_prepump_score / calculate_preshort_score (v3, 장기 매집사이클
탐지형)와 완전히 동일한 배점 로직을 벡터화(np.select)로 구현했다 — 여기서 임계값을 튜닝하면
그대로 trading_server.py의 함수에 옮겨 실시간 서버에도 반영할 수 있다.

필요 CSV 컬럼(data_collector.py v3 출력 기준):
  timestamp, ticker, price_usd, oi_change_pct, cvd_1h, ema20, ema60, ema120,
  atr_pct, vol_z, box_high, box_low, rsi, recent_pct, chg_30m_pct
컬럼명이 다르면 아래 COLUMN_MAP만 고치면 된다. box_high/box_low/recent_pct/ema120처럼
구버전 CSV엔 없는 컬럼은 자동으로 "중립값"으로 대체해서(해당 항목만 0점 처리) 죽지 않는다.
"""
import sys
import pandas as pd
import numpy as np

# ============================================================
# 설정 — 둘 다 켜둬도 되고(비교용), 필요 없는 쪽만 꺼도 된다.
# ============================================================
RUN_PREPUMP = True
RUN_PRESHORT = True
PREPUMP_CUTOFF = 80
PRESHORT_CUTOFF = 80

COLUMN_MAP = {
    "timestamp": "timestamp",
    "ticker": "ticker",
    "price": "price_usd",
    "oi_change_pct": "oi_change_pct",
    "cvd_1h": "cvd_1h",
    "ema20": "ema20",
    "ema60": "ema60",
    "ema120": "ema120",
    "atr_pct": "atr_pct",
    "vol_z": "vol_z",
    "box_high": "box_high",
    "box_low": "box_low",
    "rsi": "rsi",
    "recent_pct": "recent_pct",
    "chg_30m": "chg_30m_pct",
}


def _col_or_default(df: pd.DataFrame, name: str, default: float) -> pd.Series:
    """CSV에 없는 컬럼이면(구버전 로그 등) 기본값 Series로 대체해서 죽지 않게 한다."""
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce").fillna(default)
    return pd.Series(default, index=df.index)


# ── ① OI 지속 증가/감소 (25점) ──────────────────────────────
def score_oi_persistence(oi_change_pct: pd.Series, direction: str) -> pd.Series:
    if direction == "prepump":
        return np.select(
            [oi_change_pct >= 10, oi_change_pct >= 7, oi_change_pct >= 5,
             oi_change_pct >= 3, oi_change_pct >= 1, oi_change_pct >= 0],
            [25, 22, 18, 13, 8, 4],
            default=0,
        )
    return np.select(
        [oi_change_pct < 0, oi_change_pct < 1],
        [25, 12],
        default=0,
    )


# ── ② CVD 누적 증가/감소 (20점, 가격 보너스 포함) ────────────
def score_cvd_cumulative(cvd_1h: pd.Series, chg_30m: pd.Series, direction: str) -> pd.Series:
    if direction == "prepump":
        base = np.select(
            [cvd_1h >= 100000, cvd_1h >= 30000, cvd_1h >= 5000, cvd_1h > 0],
            [20, 17, 13, 7],
            default=0,
        )
        bonus = np.select(
            [(chg_30m >= -0.3) & (chg_30m <= 0.3), chg_30m < -0.3],
            [3, 5],
            default=0,
        )
        bonus = np.where(cvd_1h > 0, bonus, 0)
        return np.minimum(base + bonus, 20)
    return np.select(
        [cvd_1h <= -100000, cvd_1h <= -30000, cvd_1h <= -5000, cvd_1h < 0],
        [20, 17, 13, 7],
        default=0,
    )


# ── ③ EMA 압축도/이격도 (15점) ───────────────────────────────
def score_ema_compression(ema20: pd.Series, ema60: pd.Series, ema120: pd.Series, direction: str) -> pd.Series:
    valid = (ema20 > 0) & (ema60 > 0) & (ema120 > 0)
    spread = (np.maximum.reduce([ema20, ema60, ema120]) - np.minimum.reduce([ema20, ema60, ema120])) / ema60 * 100
    aligned_up = (ema20 > ema60) & (ema60 > ema120)
    aligned_down = (ema20 < ema60) & (ema60 < ema120)

    if direction == "prepump":
        result = np.select(
            [spread <= 0.3, aligned_up & (spread <= 1.0), aligned_up, aligned_down],
            [15, 12, 7, 0],
            default=2,
        )
    else:
        result = np.select(
            [spread >= 3.0, spread >= 1.5, spread <= 0.3],
            [15, 10, 2],
            default=5,
        )
    return np.where(valid, result, 0)


# ── ④ ATR (10점) ────────────────────────────────────────────
def score_atr_state(atr_pct: pd.Series, direction: str) -> pd.Series:
    if direction == "prepump":
        return np.select(
            [(atr_pct >= 1.0) & (atr_pct <= 2.0), (atr_pct >= 0.5) & (atr_pct < 1.0),
             (atr_pct > 2.0) & (atr_pct <= 3.0), atr_pct > 3.0],
            [10, 7, 5, 0],
            default=3,  # 0.5% 미만
        )
    return np.select([atr_pct >= 4.0, atr_pct >= 3.0], [10, 6], default=0)


# ── ⑤ VolZ (10점) ───────────────────────────────────────────
def score_volz_state(vol_z: pd.Series, direction: str) -> pd.Series:
    if direction == "prepump":
        return np.select(
            [(vol_z >= 0.5) & (vol_z <= 1.2), (vol_z > 1.2) & (vol_z <= 2.0),
             (vol_z >= 0.2) & (vol_z < 0.5), (vol_z > 2.0) & (vol_z <= 3.0), vol_z > 3.0],
            [10, 7, 6, 3, 0],
            default=2,  # 0.2 미만
        )
    return np.select([vol_z >= 3.0, vol_z >= 2.0], [10, 5], default=0)


# ── ⑥ 가격 위치 (박스, 10점) ─────────────────────────────────
def score_box_position(price: pd.Series, box_high: pd.Series, box_low: pd.Series, direction: str) -> pd.Series:
    valid = box_high > box_low
    denom = (box_high - box_low).replace(0, np.nan)
    pos_pct = (price - box_low) / denom * 100
    if direction == "prepump":
        result = np.select([pos_pct <= 25, pos_pct <= 50, pos_pct <= 80], [10, 8, 5], default=0)
    else:
        result = np.select([pos_pct >= 80, pos_pct >= 60], [10, 5], default=0)
    return np.where(valid, np.nan_to_num(result), 0)


# ── ⑦ RSI (5점) ─────────────────────────────────────────────
def score_rsi_box(rsi: pd.Series, direction: str) -> pd.Series:
    if direction == "prepump":
        return np.select(
            [(rsi >= 45) & (rsi <= 60), (rsi >= 40) & (rsi < 45), (rsi > 60) & (rsi <= 70), (rsi >= 30) & (rsi < 40)],
            [5, 4, 3, 2],
            default=0,
        )
    return np.select([rsi >= 70, rsi >= 60], [5, 3], default=0)


# ── ⑧ 최근 상승률 패널티/보너스 (5점) ─────────────────────────
def score_recent_move(recent_pct: pd.Series, direction: str) -> pd.Series:
    if direction == "prepump":
        return np.select(
            [recent_pct <= 3, recent_pct <= 7, recent_pct <= 10, recent_pct <= 15],
            [5, 3, 2, 1],
            default=0,
        )
    return np.select([recent_pct >= 15, recent_pct >= 10], [5, 3], default=0)


def compute_scores(df: pd.DataFrame) -> pd.DataFrame:
    cm = COLUMN_MAP
    price = _col_or_default(df, cm["price"], 0.0)
    oi = _col_or_default(df, cm["oi_change_pct"], 0.0)
    cvd = _col_or_default(df, cm["cvd_1h"], 0.0)
    ema20 = _col_or_default(df, cm["ema20"], 0.0)
    ema60 = _col_or_default(df, cm["ema60"], 0.0)
    ema120 = _col_or_default(df, cm["ema120"], 0.0)
    atr = _col_or_default(df, cm["atr_pct"], 0.0)
    vz = _col_or_default(df, cm["vol_z"], 0.0)
    box_high = _col_or_default(df, cm["box_high"], 0.0)
    box_low = _col_or_default(df, cm["box_low"], 0.0)
    rsi = _col_or_default(df, cm["rsi"], 50.0)
    recent = _col_or_default(df, cm["recent_pct"], 0.0)
    chg30 = _col_or_default(df, cm["chg_30m"], 0.0)

    for direction, run, out_col in (("prepump", RUN_PREPUMP, "prepump_score"),
                                     ("preshort", RUN_PRESHORT, "preshort_score")):
        if not run:
            continue
        df[out_col] = (
            score_oi_persistence(oi, direction)
            + score_cvd_cumulative(cvd, chg30, direction)
            + score_ema_compression(ema20, ema60, ema120, direction)
            + score_atr_state(atr, direction)
            + score_volz_state(vz, direction)
            + score_box_position(price, box_high, box_low, direction)
            + score_rsi_box(rsi, direction)
            + score_recent_move(recent, direction)
        )
        df[out_col] = df[out_col].clip(0, 100)
    return df


def main(csv_path: str):
    df = pd.read_csv(csv_path)
    df = compute_scores(df)
    cm = COLUMN_MAP

    if RUN_PREPUMP:
        hits = df[df["prepump_score"] >= PREPUMP_CUTOFF].sort_values(cm["timestamp"])
        print(f"\n=== 매집(Pre-Pump) 신호 ({PREPUMP_CUTOFF}점 이상, {len(hits)}건) ===")
        cols = [cm["timestamp"], cm["ticker"], cm["price"], cm["rsi"], cm["oi_change_pct"], "prepump_score"]
        print(hits[[c for c in cols if c in hits.columns]].to_string(index=False))

    if RUN_PRESHORT:
        hits = df[df["preshort_score"] >= PRESHORT_CUTOFF].sort_values(cm["timestamp"])
        print(f"\n=== 분산(Pre-Short) 신호 ({PRESHORT_CUTOFF}점 이상, {len(hits)}건) ===")
        cols = [cm["timestamp"], cm["ticker"], cm["price"], cm["rsi"], cm["oi_change_pct"], "preshort_score"]
        print(hits[[c for c in cols if c in hits.columns]].to_string(index=False))

    out_path = csv_path.rsplit(".", 1)[0] + "_prepump_scored.csv"
    df.to_csv(out_path, index=False)
    print(f"\n전체 스코어 포함 CSV 저장: {out_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python screener_prepump.py market_data_log_v3_20260703_20260705.csv")
        sys.exit(1)
    main(sys.argv[1])
