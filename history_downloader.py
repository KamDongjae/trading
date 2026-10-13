# -*- coding: utf-8 -*-
"""
history_downloader.py
---------------------
[2026-10-13 신설] 거래소 공개 API에서 과거 캔들(1시간봉 2년, 일봉 3년)을 받아서
history/ 폴더에 코인별 CSV로 쌓는다. 수집기(data_collector.py)가 5분 스냅샷으로 모은
3주치 데이터만으로는 상승/하락/횡보장을 여러 번 겪은 '긴' 검증이 안 돼서 만든 것.

- 거래소: 업비트 KRW 마켓 우선, 업비트에 없는 코인은 빗썸 v1 API(업비트와 같은 형식)로 시도.
- 대상 코인: 같은 폴더 market_data_log_v3_*.csv에 나온 티커 전부 + BTC/ETH.
- 이어받기: 이미 받은 파일이 있으면 최신 구간만 추가로 받고, 과거 쪽이 덜 받아졌으면
  (중간에 끊겼으면) 거기서부터 이어서 받는다. 상장일까지 다 받은 코인은 다시 안 받는다.
- 속도: 초당 약 6회 호출(거래소 제한보다 낮게). 처음 전체 다운로드는 10~20분 정도.
- 결과: history/{거래소}_{티커}_1h.csv, history/{거래소}_{티커}_1d.csv
         컬럼 = ts_kst, ts_utc, open, high, low, close, volume, value(거래대금 원)
         진행 상황은 history/_status.json, 로그는 history/_download_log.txt

사용법:
  python history_downloader.py          # 직접 실행
  (data_collector.py가 켜질 때 마지막 성공 후 20시간이 지났으면 백그라운드로 자동 실행)
"""
import os
import re
import csv
import sys
import json
import time
import glob
from datetime import datetime, timedelta, timezone

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HIST_DIR = os.path.join(SCRIPT_DIR, "history")
STATUS_FILE = os.path.join(HIST_DIR, "_status.json")
LOG_FILE = os.path.join(HIST_DIR, "_download_log.txt")

HOURLY_DAYS_BACK = 730      # 1시간봉: 2년
DAILY_DAYS_BACK = 1095      # 일봉: 3년
CALL_INTERVAL_SEC = 0.17    # 초당 약 6회
PAGE = 200                  # 한 번에 받는 캔들 수(API 최대)
REDOWNLOAD_AFTER_HOURS = 20

SOURCES = {
    "upbit": "https://api.upbit.com/v1/candles/{kind}",
    "bithumb": "https://api.bithumb.com/v1/candles/{kind}",
}
KINDS = {"1h": "minutes/60", "1d": "days"}
COLS = ["ts_kst", "ts_utc", "open", "high", "low", "close", "volume", "value"]

_last_call = [0.0]


def _log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print("📚 " + msg)
    try:
        os.makedirs(HIST_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _get(url, params, tries=5):
    for i in range(tries):
        wait = CALL_INTERVAL_SEC - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        try:
            r = requests.get(url, params=params, timeout=10, headers={"accept": "application/json"})
            if r.status_code == 429:
                time.sleep(1.0 * (i + 1))
                continue
            if r.status_code in (400, 404):
                return None  # 없는 마켓
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and data.get("error"):
                return None
            return data
        except Exception:
            time.sleep(1.0 * (i + 1))
    raise RuntimeError(f"요청 반복 실패: {url} {params}")


def _load_status():
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_status(st):
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATUS_FILE)


def _read_csv(path):
    rows = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[r["ts_utc"]] = r
    return rows


def _write_csv(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for k in sorted(rows):
            w.writerow(rows[k])
    os.replace(tmp, path)


def _to_row(c):
    return {
        "ts_kst": c.get("candle_date_time_kst", ""),
        "ts_utc": c.get("candle_date_time_utc", ""),
        "open": c.get("opening_price", ""),
        "high": c.get("high_price", ""),
        "low": c.get("low_price", ""),
        "close": c.get("trade_price", ""),
        "volume": c.get("candle_acc_trade_volume", ""),
        "value": c.get("candle_acc_trade_price", ""),
    }


def _fetch_range(source, kind, market, to_utc, stop_utc):
    """to_utc(포함 안 함)부터 과거로 stop_utc까지 페이지를 넘기며 받는다.
    반환: (캔들 dict 목록, 상장일까지 다 받았는지)"""
    url = SOURCES[source].format(kind=KINDS[kind])
    out = []
    cursor = to_utc
    while True:
        params = {"market": market, "count": PAGE}
        if cursor:
            params["to"] = cursor.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        data = _get(url, params)
        if data is None:
            return None, False
        if not data:
            return out, True
        out.extend(data)
        oldest = min(datetime.strptime(c["candle_date_time_utc"], "%Y-%m-%dT%H:%M:%S") for c in data)
        if oldest <= stop_utc:
            return out, False
        if len(data) < PAGE:
            return out, True
        cursor = oldest


def _download_one(source, ticker, kind, status):
    key = f"{source}_{ticker}_{kind}"
    path = os.path.join(HIST_DIR, f"{key}.csv")
    st = status.get(key, {})
    if st.get("missing"):
        return "missing"
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    days_back = HOURLY_DAYS_BACK if kind == "1h" else DAILY_DAYS_BACK
    target_start = now_utc - timedelta(days=days_back)
    market = f"KRW-{ticker}"
    rows = _read_csv(path)
    added = 0
    if rows:
        newest = datetime.strptime(max(rows), "%Y-%m-%dT%H:%M:%S")
        oldest = datetime.strptime(min(rows), "%Y-%m-%dT%H:%M:%S")
        # 1) 최신 쪽 채우기
        got, _ = _fetch_range(source, kind, market, None, newest)
        if got is None:
            return "missing"
        for c in got:
            k = c["candle_date_time_utc"]
            if k not in rows:
                added += 1
            rows[k] = _to_row(c)
        # 2) 과거 쪽(중간에 끊겼으면 이어받기)
        if not st.get("complete_back") and oldest > target_start:
            got, done = _fetch_range(source, kind, market, oldest, target_start)
            for c in got or []:
                k = c["candle_date_time_utc"]
                if k not in rows:
                    added += 1
                rows[k] = _to_row(c)
            st["complete_back"] = bool(done) or (min(rows) <= target_start.strftime("%Y-%m-%dT%H:%M:%S"))
    else:
        got, done = _fetch_range(source, kind, market, None, target_start)
        if got is None:
            status[key] = {"missing": True}
            return "missing"
        for c in got:
            rows[c["candle_date_time_utc"]] = _to_row(c)
        added = len(rows)
        st["complete_back"] = bool(done) or (rows and min(rows) <= target_start.strftime("%Y-%m-%dT%H:%M:%S"))
    if rows:
        _write_csv(path, rows)
    st["rows"] = len(rows)
    st["first"] = min(rows) if rows else ""
    st["last"] = max(rows) if rows else ""
    st["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status[key] = st
    return f"{len(rows)}개(+{added})"


def _tickers():
    tks = set()
    files = sorted(glob.glob(os.path.join(SCRIPT_DIR, "market_data_log_v3_*.csv")))[-3:]
    for fp in files:
        try:
            with open(fp, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    t = (r.get("ticker") or "").strip().upper()
                    if re.fullmatch(r"[A-Z0-9]{1,15}", t):
                        tks.add(t)
        except Exception:
            pass
    tks.update(["BTC", "ETH"])
    return sorted(tks)


def run():
    os.makedirs(HIST_DIR, exist_ok=True)
    status = _load_status()
    tickers = _tickers()
    _log(f"과거 캔들 다운로드 시작 — {len(tickers)}개 코인, 1시간봉 {HOURLY_DAYS_BACK}일 / 일봉 {DAILY_DAYS_BACK}일")
    t0 = time.time()
    for i, t in enumerate(tickers, 1):
        res = {}
        for kind in ("1d", "1h"):
            got = None
            for source in ("upbit", "bithumb"):
                try:
                    r = _download_one(source, t, kind, status)
                except Exception as e:
                    r = f"오류({e})"
                if r != "missing":
                    got = f"{source} {r}"
                    break
            res[kind] = got or "없음"
            _save_status(status)
        _log(f"({i}/{len(tickers)}) {t}: 일봉 {res['1d']} | 1시간봉 {res['1h']}")
    status["_last_run"] = {"finished": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                           "tickers": len(tickers), "seconds": round(time.time() - t0)}
    _save_status(status)
    _log(f"완료 — {round((time.time() - t0) / 60, 1)}분")


def due():
    """마지막 완료 후 REDOWNLOAD_AFTER_HOURS 시간이 지났으면 True."""
    st = _load_status().get("_last_run", {})
    try:
        last = datetime.strptime(st["finished"], "%Y-%m-%d %H:%M:%S")
        return datetime.now() - last > timedelta(hours=REDOWNLOAD_AFTER_HOURS)
    except Exception:
        return True


if __name__ == "__main__":
    run()
