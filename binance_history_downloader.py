# -*- coding: utf-8 -*-
"""
binance_history_downloader.py
-----------------------------
[2026-11-23 신설, 사용자 요청] 바이낸스 USDT 무기한 선물 공개 API(키 필요 없음)로
1시간봉과 펀딩비 기록을 받아 history_binance/ 에 코인별 CSV로 쌓는다.
바이낸스 탭 추가 전에 '실제로 주문하는 시장' 데이터로 1년 백테스트를 하기 위한 것.

- 대상: exchangeInfo 의 USDT 무기한(PERPETUAL, TRADING) 전 종목
- 기간: 1시간봉 DAYS_BACK 일(기본 400일) — 상장이 더 늦으면 상장 시점부터
- 이어받기: 이미 받은 파일이 있으면 마지막 시각 다음부터만 추가로 받는다
- 속도: 응답 헤더의 분당 사용량(X-MBX-USED-WEIGHT-1M)을 보고 한도 근처면 쉰다.
        펀딩비 기록은 5분에 500회 제한이라 0.7초 간격.
- 결과(history_binance/ — prob_model 학습 폴더(history/)와 분리, .gitignore):
    binance_{코인}_1h.csv      ts_kst, ts_utc, open, high, low, close, volume, value(USDT 거래대금),
                               trades, taker_buy_value(시장가 매수 거래대금 USDT)
    binance_{코인}_funding.csv ts_kst, ts_utc, funding_rate, mark_price
    _symbols.json (종목 목록·상장시각), _status.json (진행 상황), _download_log.txt (로그)
- 코인 이름: 1000PEPEUSDT 처럼 앞에 배수가 붙은 건 파일명도 1000PEPE 그대로(가격도 그 단위).

사용법: run_binance_history.bat 더블클릭 (또는 python binance_history_downloader.py)
"""
import os
import csv
import json
import time
from datetime import datetime, timedelta, timezone

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(SCRIPT_DIR, "history_binance")
STATUS_FILE = os.path.join(OUT_DIR, "_status.json")
LOG_FILE = os.path.join(OUT_DIR, "_download_log.txt")
SYMBOLS_FILE = os.path.join(OUT_DIR, "_symbols.json")

BASE = "https://fapi.binance.com"
DAYS_BACK = 400
KLINE_LIMIT = 1500
WEIGHT_SOFT_CAP = 1800          # 분당 2400 한도보다 낮게
FUNDING_INTERVAL_SEC = 0.7      # fundingRate 5분 500회 제한
KST = timezone(timedelta(hours=9))
HOUR_MS = 3600 * 1000

_session = requests.Session()


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get(path, params=None, tries=5):
    for i in range(tries):
        try:
            r = _session.get(BASE + path, params=params, timeout=20)
            used = int(r.headers.get("X-MBX-USED-WEIGHT-1M", "0") or 0)
            if r.status_code in (418, 429):
                wait = int(r.headers.get("Retry-After", "60") or 60)
                log(f"요청 제한({r.status_code}) — {wait}초 대기")
                time.sleep(wait + 1)
                continue
            r.raise_for_status()
            if used >= WEIGHT_SOFT_CAP:
                time.sleep(max(1, 61 - datetime.now().second))
            return r.json()
        except Exception as e:
            log(f"요청 실패 {path} {params} ({i + 1}/{tries}): {e}")
            time.sleep(3 * (i + 1))
    return None


def fmt_ts(ms):
    u = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return u.astimezone(KST).strftime("%Y-%m-%dT%H:%M:%S"), u.strftime("%Y-%m-%dT%H:%M:%S")


def last_ts_ms(path):
    """CSV 마지막 줄의 ts_utc를 ms로. 파일이 없거나 비면 None."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4096))
            tail = f.read().decode("utf-8", "ignore").strip().splitlines()
        if len(tail) < 2 and size < 4096:
            return None
        ts = tail[-1].split(",")[1]
        if ts == "ts_utc":
            return None
        return int(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except Exception:
        return None


def append_rows(path, header, rows):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerows(rows)


def download_klines(sym, coin, onboard_ms, start_ms, now_ms):
    path = os.path.join(OUT_DIR, f"binance_{coin}_1h.csv")
    last = last_ts_ms(path)
    t = (last + HOUR_MS) if last else max(start_ms, onboard_ms or 0)
    end_ms = now_ms - HOUR_MS          # 아직 안 끝난 봉은 안 받음
    n = 0
    while t <= end_ms:
        data = get("/fapi/v1/klines", {"symbol": sym, "interval": "1h", "startTime": t, "limit": KLINE_LIMIT})
        if data is None:
            return n, "fail"
        rows = []
        for k in data:
            ot = int(k[0])
            if ot > end_ms:
                break
            kst, utc = fmt_ts(ot)
            rows.append([kst, utc, k[1], k[2], k[3], k[4], k[5], k[7], k[8], k[10]])
        if not rows:
            break
        append_rows(path, ["ts_kst", "ts_utc", "open", "high", "low", "close", "volume", "value",
                           "trades", "taker_buy_value"], rows)
        n += len(rows)
        t = int(data[len(rows) - 1][0]) + HOUR_MS
        if len(data) < KLINE_LIMIT:
            break
    return n, "ok"


def download_funding(sym, coin, start_ms, now_ms):
    path = os.path.join(OUT_DIR, f"binance_{coin}_funding.csv")
    last = last_ts_ms(path)
    t = (last + 1) if last else start_ms
    n = 0
    while t < now_ms:
        time.sleep(FUNDING_INTERVAL_SEC)
        data = get("/fapi/v1/fundingRate", {"symbol": sym, "startTime": t, "limit": 1000})
        if data is None:
            return n, "fail"
        if not data:
            break
        rows = []
        for d in data:
            kst, utc = fmt_ts(int(d["fundingTime"]))
            rows.append([kst, utc, d.get("fundingRate"), d.get("markPrice", "")])
        append_rows(path, ["ts_kst", "ts_utc", "funding_rate", "mark_price"], rows)
        n += len(rows)
        t = int(data[-1]["fundingTime"]) + 1
        if len(data) < 1000:
            break
    return n, "ok"


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    log("=== 바이낸스 선물 과거 데이터 받기 시작 ===")
    info = get("/fapi/v1/exchangeInfo")
    if not info:
        log("exchangeInfo 실패 — 인터넷/바이낸스 접속 확인")
        return 1
    syms = [s for s in info.get("symbols", [])
            if s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"]
    meta = {s["symbol"]: {"coin": s["baseAsset"], "onboard": s.get("onboardDate")} for s in syms}
    with open(SYMBOLS_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - DAYS_BACK * 24 * HOUR_MS
    log(f"대상 {len(meta)}종목, 기간 {DAYS_BACK}일")
    status = {"started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "total": len(meta), "done": 0,
              "fail": [], "rows_1h": 0, "rows_funding": 0}
    for i, (sym, m) in enumerate(sorted(meta.items()), 1):
        coin = m["coin"]
        try:
            n1, s1 = download_klines(sym, coin, m.get("onboard"), start_ms, now_ms)
            n2, s2 = download_funding(sym, coin, start_ms, now_ms)
        except Exception as e:
            n1 = n2 = 0
            s1 = s2 = f"error {e}"
        status["rows_1h"] += n1
        status["rows_funding"] += n2
        if s1 != "ok" or s2 != "ok":
            status["fail"].append(sym)
        status["done"] = i
        status["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if i % 10 == 0 or i == len(meta):
            log(f"{i}/{len(meta)} {sym} 1h +{n1} 펀딩 +{n2}")
            with open(STATUS_FILE, "w", encoding="utf-8") as f:
                json.dump(status, f, ensure_ascii=False, indent=1)
    status["finished"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=1)
    log(f"=== 끝 — 1h {status['rows_1h']}줄, 펀딩 {status['rows_funding']}줄, 실패 {len(status['fail'])}종목 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
