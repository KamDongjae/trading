# -*- coding: utf-8 -*-
"""
binance_history_backfill.py
---------------------------
[2026-11-23 신설, 사용자 요청] history_binance/ 의 1시간봉을 '과거 쪽으로' 더 받아서 앞에 붙인다.
binance_history_downloader.py 는 400일만 받고, 이어받기는 최신 쪽으로만 한다.
바이낸스 데이터로 판정 모델을 학습하려면 2년 이상이 필요해서(원화 모델과 같은 조건) 최대 DAYS_BACK 일까지 채운다.

- 대상: _symbols.json 의 종목(지금 거래 중인 USDT 무기한)
- 각 파일 첫 봉보다 앞 구간을 [max(상장시각, 지금-DAYS_BACK일), 첫 봉-1h] 로 받아서
  임시 파일에 (헤더 + 새 과거 봉 + 기존 내용) 을 쓰고 os.replace 로 교체 — 중간에 꺼져도 파일이 깨지지 않음
- 펀딩비는 손대지 않음
- 이미 다 받은 종목은 건너뜀(다시 돌려도 안전)

사용법: run_binance_longtest.bat 가 먼저 실행 (또는 python binance_history_backfill.py)
"""
import os
import json
import time
from datetime import datetime, timedelta, timezone

import binance_history_downloader as dl

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = dl.OUT_DIR
DAYS_BACK = 1100
HOUR_MS = 3600 * 1000
HEADER = "ts_kst,ts_utc,open,high,low,close,volume,value,trades,taker_buy_value"


def first_ts_ms(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            f.readline()
            line = f.readline()
        ts = line.split(",")[1]
        return int(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except Exception:
        return None


def fetch_range(sym, start_ms, end_ms):
    rows = []
    t = start_ms
    while t <= end_ms:
        data = dl.get("/fapi/v1/klines", {"symbol": sym, "interval": "1h", "startTime": t,
                                          "endTime": end_ms, "limit": dl.KLINE_LIMIT})
        if data is None:
            return None
        if not data:
            break
        for k in data:
            ot = int(k[0])
            if ot > end_ms:
                break
            kst, utc = dl.fmt_ts(ot)
            rows.append(",".join(str(x) for x in [kst, utc, k[1], k[2], k[3], k[4], k[5], k[7], k[8], k[10]]))
        t = int(data[-1][0]) + HOUR_MS
        if len(data) < dl.KLINE_LIMIT:
            break
    return rows


def main():
    with open(os.path.join(OUT_DIR, "_symbols.json"), encoding="utf-8") as f:
        meta = json.load(f)
    now_ms = int(time.time() * 1000)
    floor_ms = now_ms - DAYS_BACK * 24 * HOUR_MS
    dl.log(f"=== 과거 쪽 보충 시작 — {len(meta)}종목, 최대 {DAYS_BACK}일 ===")
    added = 0
    fails = []
    for i, (sym, m) in enumerate(sorted(meta.items()), 1):
        coin = m["coin"]
        path = os.path.join(OUT_DIR, f"binance_{coin}_1h.csv")
        first = first_ts_ms(path)
        if first is None:
            continue
        start = max(floor_ms, int(m.get("onboard") or 0))
        start = start - start % HOUR_MS
        end = first - HOUR_MS
        if end < start:
            continue
        rows = fetch_range(sym, start, end)
        if rows is None:
            fails.append(sym)
            continue
        if rows:
            tmp = path + ".tmp"
            with open(path, "r", encoding="utf-8") as src, open(tmp, "w", encoding="utf-8", newline="") as dst:
                src.readline()
                dst.write(HEADER + "\n")
                dst.write("\n".join(rows) + "\n")
                for line in src:
                    dst.write(line)
            os.replace(tmp, path)
            added += len(rows)
        if i % 20 == 0 or i == len(meta):
            dl.log(f"보충 {i}/{len(meta)} {sym} +{len(rows)} (누적 +{added})")
    dl.log(f"=== 과거 쪽 보충 끝 — +{added}줄, 실패 {len(fails)}종목 {fails[:10]} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
