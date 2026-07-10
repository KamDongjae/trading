# -*- coding: utf-8 -*-
"""
data_collector.py
------------------
trading_server.py와 같은 폴더에 놓고 별도로 돌리는 '데이터 수집 전용' 스크립트.
거래소 계좌/포지션은 전혀 안 건드리고, trading_server.py에 이미 있는 지표/점수 계산
함수(process_ticker)를 그대로 재사용해서 시세+지표+점수를 주기적으로 CSV에 append한다.

trading_server.py를 모듈로 import해서 쓰기 때문에, 서버가 실시간으로 계산하는 값과
100% 동일한 로직으로 로그가 쌓인다 — screener_prepump.py 같은 오프라인 분석 스크립트가
바로 읽을 수 있는 포맷(timestamp, ticker, price_krw, chg_30m_pct, vol_z, cvd_1h, rsi, ls_ratio)
을 기본으로 쓰되, long_score/short_score/prepump_score/preshort_score 등 나머지 지표도
같이 남겨서 나중에 배점 튜닝에 쓸 수 있게 한다.

사용법:
  python data_collector.py
  (Ctrl+C로 중지. trading_server.py를 이미 돌리고 있어도 상관없다 — 서버 상태를
   전혀 안 건드리고 계좌/포지션 파일도 안 만든다. 같은 기기에서 같이 돌려도 되고,
   기존 설계 의도대로 다른 기기에서 이 스크립트만 따로 돌려도 된다.)

파일 분리:
  ROTATE_DAYS(기본 3)일마다 새 CSV로 자동 분리된다. 파일명에 기간이 그대로 찍힌다
  (예: market_data_log_v3_20260703_20260705.csv). 스크립트를 중간에 껐다 켜도
  오늘 날짜가 진행 중이던 구간 안이면 그 파일에 이어서 쓰고, 구간이 지났으면
  새 파일로 넘어간다.

설정:
  COLLECT_INTERVAL_SEC - 몇 초마다 한 번씩 전 종목을 스캔해서 로그를 남길지 (기본 300초=5분)
  ROTATE_DAYS - 며칠마다 파일을 새로 분리할지 (기본 3일)
  CANDLE_INTERVAL_OVERRIDE - None이면 trading_server.py 기본값(1h) 그대로 사용.
                             "1h"/"2h"/"6h"/"12h" 중 하나로 고정하고 싶으면 지정.
"""
import os
import re
import sys
import csv
import time
import threading
from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import trading_server as srv  # noqa: E402  (trading_server.py를 같은 폴더에 둬야 함)

COLLECT_INTERVAL_SEC = 300
CANDLE_INTERVAL_OVERRIDE = None  # 예: "1h" / "2h" / "6h" / "12h"
ROTATE_DAYS = 3  # 이 일수마다 새 CSV 파일로 분리 (파일명에 기간이 그대로 찍힘)

FILE_PATTERN = re.compile(r"^market_data_log_v3_(\d{8})_(\d{8})\.csv$")

CSV_HEADER = [
    "timestamp", "ticker", "price_krw", "price_usd",
    "chg_30m_pct", "vol_z", "cvd_1h", "rsi", "rsi_delta", "bb_percent",
    "ls_ratio", "atr_pct", "oi_change_pct", "funding", "vol_24h_m",
    "ema20", "ema60", "ema120", "box_high", "box_low", "recent_pct",
    "long_score", "short_score", "prepump_score", "preshort_score", "interval",
]

# 현재 로그 구간 상태 (get_output_path가 채워줌)
_current_period_start = None  # type: date
_current_output_csv = None


def _path_for_period(period_start: date) -> str:
    period_end = period_start + timedelta(days=ROTATE_DAYS - 1)
    return os.path.join(SCRIPT_DIR, f"market_data_log_v3_{period_start:%Y%m%d}_{period_end:%Y%m%d}.csv")


def _find_resumable_period():
    """재시작 시 기존에 쌓다 만 구간이 있으면(오늘 날짜가 그 구간 안이면) 새로 쪼개지 말고 이어붙인다."""
    best_start = None
    for name in os.listdir(SCRIPT_DIR):
        m = FILE_PATTERN.match(name)
        if not m:
            continue
        try:
            start = datetime.strptime(m.group(1), "%Y%m%d").date()
            end = datetime.strptime(m.group(2), "%Y%m%d").date()
        except ValueError:
            continue
        if start <= date.today() <= end:
            if best_start is None or start > best_start:
                best_start = start
    return best_start


def get_output_path() -> str:
    """오늘 날짜 기준으로 현재 몇 번째 3일 구간인지 계산해서, 구간이 바뀌었으면
    새 파일 경로로 갱신한다(헤더도 새로 씀). 재시작해도 기존 진행 중이던 구간이
    있으면 그 파일에 이어서 쓴다."""
    global _current_period_start, _current_output_csv
    today = date.today()
    if _current_period_start is None or today > _current_period_start + timedelta(days=ROTATE_DAYS - 1):
        resumable = _find_resumable_period()
        _current_period_start = resumable if resumable else today
        _current_output_csv = _path_for_period(_current_period_start)
        _ensure_header(_current_output_csv)
        print(f"📂 로그 파일: {_current_output_csv} "
              f"({_current_period_start:%Y-%m-%d} ~ {(_current_period_start + timedelta(days=ROTATE_DAYS-1)):%Y-%m-%d})")
    return _current_output_csv


def _ensure_header(path):
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(CSV_HEADER)


def _row_from_result(ts, r):
    return [
        ts, r.get("ticker", ""), r.get("price", ""), r.get("price_usd", ""),
        r.get("chg_30m", ""), r.get("vol_z", ""), r.get("cvd_diff", ""),
        r.get("rsi", ""), r.get("rsi_delta", ""), r.get("bb_percent", ""),
        r.get("ls_ratio", ""), r.get("atr_pct", ""), r.get("oi_change_pct", ""),
        r.get("funding", ""), r.get("vol_24h_m", ""),
        r.get("ema20", ""), r.get("ema60", ""), r.get("ema120", ""),
        r.get("box_high", ""), r.get("box_low", ""), r.get("recent_pct", ""),
        r.get("long_score", ""), r.get("short_score", ""),
        r.get("prepump_score", ""), r.get("preshort_score", ""),
        srv.CANDLE_INTERVAL,
    ]


def load_tickers():
    tickers_ref = [[]]
    try:
        price_data = srv.Bithumb.get_current_price("ALL")
        tickers_ref[0] = [k for k in list(price_data.keys()) if k != "date"][: srv.TOP_COIN_COUNT]
        print(f"✅ 티커 {len(tickers_ref[0])}개 로드")
    except Exception as e:
        tickers_ref[0] = ["BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "TRX", "AVAX", "LINK", "BNB"]
        print(f"⚠️ 티커 로드 실패, 기본 목록 사용: {e}")
    return tickers_ref


def collect_once(tickers):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    with ThreadPoolExecutor(max_workers=srv.MAX_WORKERS) as pool:
        futures = {pool.submit(srv.process_ticker, t): t for t in tickers}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                rows.append(_row_from_result(ts, r))
    out_path = get_output_path()  # 구간이 바뀌었으면 여기서 자동으로 새 파일로 전환됨
    if rows:
        with open(out_path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
    print(f"[{ts}] {len(rows)}개 티커 로그 저장 → {out_path}")


def main():
    if CANDLE_INTERVAL_OVERRIDE:
        srv.CANDLE_INTERVAL = CANDLE_INTERVAL_OVERRIDE
    print("=" * 50)
    print("데이터 수집기 시작 (계좌/포지션 없음 — 시세+지표+점수만 CSV로 로깅)")
    print(f"{ROTATE_DAYS}일마다 파일 자동 분리 | 출력 파일: {get_output_path()}")
    print(f"수집 주기: {COLLECT_INTERVAL_SEC}초 | 기준봉: {srv.CANDLE_INTERVAL}")
    print("중지: Ctrl+C")
    print("=" * 50)

    tickers_ref = load_tickers()

    # 시세 캐시(latest_prices/latest_prices_usd)를 채워주는 백그라운드 스레드.
    # trading_server.py의 것을 그대로 재사용 — process_ticker가 여기 의존한다.
    threading.Thread(target=srv.ticker_updater, args=(tickers_ref,), daemon=True).start()
    threading.Thread(target=srv.price_updater, args=(tickers_ref,), daemon=True).start()

    print("시세 캐시 준비 중... (5초 대기)")
    time.sleep(5)

    try:
        get_funding = getattr(srv, "get_all_funding_rates", None)
        if get_funding:
            get_funding()
    except Exception:
        pass

    while True:
        try:
            tickers = tickers_ref[0]
            if not tickers:
                time.sleep(2)
                continue
            collect_once(tickers)
        except Exception as e:
            print(f"⚠️ 수집 실패: {e}")
        time.sleep(COLLECT_INTERVAL_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n데이터 수집기 종료")
