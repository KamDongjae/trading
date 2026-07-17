import threading
import time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pybithumb import Bithumb
from concurrent.futures import ThreadPoolExecutor, as_completed
import queue
import os
import csv
import socket
import dns.resolver
import ssl
import requests
from collections import deque, defaultdict

# ====================== DNS + SSL 우회 ======================
try:
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = ['8.8.8.8', '8.8.4.4', '1.1.1.1']
    dns.resolver.default_resolver = resolver
    socket.setdefaulttimeout(15)
    print("✅ DNS 우회 설정 완료")
except Exception as e:
    print(f"DNS 우회 설정 실패: {e}")
requests.packages.urllib3.disable_warnings()
ssl._create_default_https_context = ssl._create_unverified_context

# ============================================================
# 설정
# ============================================================
TOP_COIN_COUNT = 40
MIN_SCORE = 75  # 기본 진입 컷("일반장 진입"). 개편안 v2 권장 기준: 65 관심 / 70 추세장 진입 / 75 일반장 진입 / 80 횡보장(보수적 진입)
                 # 시장 상태(평균 ATR%)에 따라 70(추세장)~80(횡보장)로 자동 조정됨
WATCH_MIN_SCORE = 65  # "관심" 참고용 하한선. 실제 진입 필터(current_min_score)에는 안 쓰고 클라이언트 표시용으로만 내려준다.
# DISCORD_WEBHOOK_URL은 SCRIPT_DIR이 정해진 뒤(안드로이드 공용 저장소 경로 판별 후)
# DISCORD_WEBHOOK.txt에서 읽어온다 — 아래쪽 "SCRIPT_DIR 확정" 블록 바로 다음 참고.
INITIAL_BALANCE = 10000  # USD (달러 기준 계좌)
DEFAULT_LEVERAGE = 10
FEE_RATE = 0.0004
SLIPPAGE_RATE = 0.003
# 크로스 마진 모드: 바이낸스 크로스 마진처럼, 개별 포지션 손실이 그 포지션에 배정한
# 증거금(amount)의 100%를 넘어도(-100% 초과) 계좌 전체 잔고가 버텨주는 한 그 포지션 하나만
# 따로 강제청산하지 않는다. 계좌 총자산(현금+미실현손익 합)이 바닥날 때만 청산한다.
# False로 두면 기존처럼 포지션별 격리마진(90% 손실 시 그 포지션만 강제청산) 방식으로 동작한다.
CROSS_MARGIN_MODE = True
CROSS_MARGIN_MAINTENANCE_RATIO = 0.005  # 유지증거금 비율(0.5%). 열려있는 모든 포지션 명목가치(증거금×레버리지) 합의
                                          # 이 비율만큼을 총자산이 못 채우면 청산 시작. 실거래소 유지증거금율(대략 0.4~1%)을 참고한 값.
                                          # (예전엔 고정 $0이라 총자산이 살짝만 마이너스여도 바로 청산되는 문제가 있었음 — 버퍼를 둬서 완화)
# 안드로이드(Pydroid 등)에서는 os.path.expanduser("~")가 일반 파일탐색기로는
# 안 보이는 앱 전용 내부 저장소를 가리켜서 CSV를 찾기 어렵다. 대신 이 파이썬
# 스크립트 파일이 실제로 위치한 폴더에 저장해서 어디서든 쉽게 찾을 수 있게 한다.
try:
    _fallback_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    # 일부 환경(인터랙티브 실행 등)에서는 __file__이 없을 수 있어 현재
    # 작업 디렉터리(cwd)로 안전하게 폴백한다.
    _fallback_dir = os.getcwd()

# Termux 같은 환경의 SCRIPT_DIR(예: /data/data/com.termux/files/home)은
# 경로 계산 자체는 정확하지만, 그 폴더가 앱 전용 샌드박스라 일반
# 파일관리자/갤러리 앱으로는 접근이 안 돼서 "안 보인다"고 느껴진다.
# 그래서 CSV는 안드로이드 공용 저장소(Documents 폴더)에 고정 저장해서
# 어떤 파일관리자로든, PC에 USB로 연결해서든 바로 찾을 수 있게 한다.
# (termux-setup-storage로 저장소 권한을 먼저 허용해야 함)
_ANDROID_PUBLIC_DIR = "/storage/emulated/0/Documents"
try:
    os.makedirs(_ANDROID_PUBLIC_DIR, exist_ok=True)
    # 실제로 쓰기 가능한지 테스트 (권한 없으면 예외 발생)
    _test_path = os.path.join(_ANDROID_PUBLIC_DIR, ".write_test")
    with open(_test_path, "w") as _f:
        _f.write("ok")
    os.remove(_test_path)
    SCRIPT_DIR = _ANDROID_PUBLIC_DIR
    print(f"✅ 데이터 저장 위치: {SCRIPT_DIR} (공용 저장소)")
except Exception:
    # 권한이 없거나 안드로이드가 아닌 환경(Windows/Mac/일반 Linux)이면
    # 기존 방식(스크립트 폴더 또는 cwd)으로 그대로 폴백한다.
    SCRIPT_DIR = _fallback_dir
    print(f"⚠️ 공용 저장소 접근 불가, 대체 경로 사용: {SCRIPT_DIR}")

def _load_discord_webhook():
    """안드로이드 공용 문서함(또는 SCRIPT_DIR)의 DISCORD_WEBHOOK.txt에서 웹후크 URL을
    읽어온다(파일 안엔 URL만 한 줄로). 파일이 없거나 비어있으면 빈 문자열 -> 알림 기능 꺼짐."""
    for candidate_dir in (_ANDROID_PUBLIC_DIR, SCRIPT_DIR):
        path = os.path.join(candidate_dir, "DISCORD_WEBHOOK.txt")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    url = f.read().strip()
                if url:
                    print(f"✅ 디스코드 웹후크 로드됨: {path}")
                    return url
            except Exception as e:
                print(f"⚠️ DISCORD_WEBHOOK.txt 읽기 실패({path}): {e}")
    print("⚠️ DISCORD_WEBHOOK.txt를 못 찾음 — 디스코드 알림 기능 꺼짐")
    return ""

DISCORD_WEBHOOK_URL = _load_discord_webhook()
DATA_FILE = os.path.join(SCRIPT_DIR, "simulation_data_usd.csv")
PRICE_INTERVAL = 2
SCORE_INTERVAL = 10
MAX_WORKERS = 8
ALLOWED_INTERVALS = ["1h", "2h", "6h", "12h"]   # 클라이언트 버튼으로 전환 가능한 계산 기준 캔들
CANDLE_INTERVAL = "1h"   # 점수 계산에 쓰는 기준 캔들. srv_set_interval()로 실행 중에도 전환 가능
                          # (빗썸이 2h를 직접 지원하지 않아 2h는 1h 캔들 2개를 합쳐 재구성한다)
CVD_WINDOW_CANDLES = 2   # CANDLE_INTERVAL 기준 캔들 개수(예: 1h면 최근 2시간, 6h면 최근 12시간)
ATR_PERIOD = 14          # ATR 계산에 쓸 캔들 개수 (CANDLE_INTERVAL 기준. 인터벌이 바뀌어도 캔들 개수는 고정)
MIN_ATR_PCT = 0.0        # ATR%가 이 값 미만이면 "죽어있는 코인"으로 보고 목록에서 제외 (0이면 필터 끔)
OI_CACHE_TTL = 60        # 미체결약정(OI) 히스토리 캐시 유지시간(초). 심볼별 개별 API 호출이라 너무 짧게 두지 않는다.
ENABLE_PREPUMP_SCORE = True    # "출발 전 매집 구간" 탐지용 prepump_score/preshort_score 계산 on/off.
                                # False면 계산을 건너뛰고 스냅샷 컬럼은 0으로 채워진다(꺼도 CSV 포맷은 그대로 유지).
PREPUMP_MIN_SCORE = 80          # 클라이언트가 참고할 기본 컷 (고정값, MIN_SCORE처럼 동적 조정하지 않음)
# (마켓 데이터 CSV 로깅은 별도 기기의 data_collector.py 가 담당 — 이 서버에서는 제거됨)

# ============================================================
# 전역 변수
# ============================================================
class _NullQueue:
    """GUI가 없어 큐 소비자가 없으므로, put을 버려서 메모리 누수를 막는 더미 큐."""
    def put(self, item): pass
    def get_nowait(self): raise queue.Empty
    def empty(self): return True
data_queue = _NullQueue()
price_queue = _NullQueue()
positions = {}
balance = INITIAL_BALANCE
# 외부 통장(거래소 바깥의 '내 진짜 지갑') — 거래소 잔고(balance)와 완전히 분리된 별도의 돈.
#   외부통장 입금 : 새 돈이 생김(월급 등). 어디서 오는지는 안 따짐.
#   외부통장 출금 : 돈이 통장 밖으로 나가서 그냥 사라짐(실생활 지출 등). 어디로도 안 감.
#   거래소 충전   : 외부통장 → 거래소로 이체 (외부통장 잔액이 있어야 됨)
#   거래소 출금   : 거래소 → 외부통장으로 이체 (전에는 그냥 사라졌지만 이제 외부통장으로 들어옴)
bank_balance = 0.0
bank_total_deposit = 0.0   # 누적 외부통장 입금액 (순수익 계산용)
bank_total_spent = 0.0     # 누적 외부통장 출금액 = 실생활로 빠져나가 사라진 돈 (순수익 계산용)
trade_history = []   # 각 항목: dict {type, ticker, direction, amount, leverage, entry_price, exit_price, pnl, pnl_rate, entry_time, exit_time}
running = True
latest_prices = {}
latest_prices_usd = {}   # 바이낸스 선물 체결가(USD). 거래/손익/청산 전부 이 가격 기준.
data_lock = threading.Lock()
score_lock = threading.Lock()
score_cache = {}

# 최근 가격 히스토리 (30분/1시간 등 단기 변동률 계산용). price_updater가 PRICE_INTERVAL(2초)마다
# 채워준다. 약 70분치를 보관해두면 30분/1시간 변동률을 캔들 fetch 없이 바로 구할 수 있다.
PRICE_HISTORY_MAXLEN = int(70 * 60 / PRICE_INTERVAL)
price_history = defaultdict(lambda: deque(maxlen=PRICE_HISTORY_MAXLEN))
price_history_lock = threading.Lock()

# 펀딩레이트 캐시
_funding_cache = {}
_funding_cache_time = 0
_funding_lock = threading.Lock()

# ============================================================
# 펀딩레이트 - 바이낸스 REST API 직접 호출 (ccxt 불필요)
# ============================================================
BINANCE_FUNDING_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
BINANCE_TICKER_URL = "https://fapi.binance.com/fapi/v1/ticker/price"  # 선물 최종 체결가(last price)

def get_all_funding_rates():
    """
    바이낸스 선물 premiumIndex를 한 번에 받아 심볼별 {'funding': 펀딩레이트(%), 'mark_price': USD가격}
    딕셔너리로 캐싱한다. 가격은 markPrice가 아니라 ticker/price의 최종 체결가(last price)로
    덮어써서 바이낸스 앱 화면에 표시되는 가격과 동일하게 맞춘다. 캐시는 10초.
    """
    global _funding_cache, _funding_cache_time
    now = time.time()
    with _funding_lock:
        if now - _funding_cache_time < 10 and _funding_cache:
            return _funding_cache
        try:
            resp = requests.get(BINANCE_FUNDING_URL, timeout=10, verify=False)
            resp.raise_for_status()
            items = resp.json()
            cache = {}
            for item in items:
                sym = item.get('symbol', '')
                if sym.endswith('USDT') and 'DOWN' not in sym and 'UP' not in sym:
                    base = sym[:-4]
                    entry = {}
                    fr = item.get('lastFundingRate')
                    if fr is not None:
                        try:
                            entry['funding'] = round(float(fr) * 100, 4)
                        except:
                            entry['funding'] = 0.0
                    else:
                        entry['funding'] = 0.0
                    mp = item.get('markPrice')
                    if mp is not None:
                        try:
                            entry['mark_price'] = float(mp)
                        except:
                            entry['mark_price'] = None
                    else:
                        entry['mark_price'] = None
                    cache[base] = entry
            # 체결가(last price)로 덮어쓰기 — 바이낸스 앱 표시가와 동일하게.
            # markPrice(지수 산출 마크가격)는 화면 체결가와 미세하게 달라서,
            # ticker/price의 최종 체결가를 받아 mark_price 자리에 덮어쓴다.
            try:
                resp2 = requests.get(BINANCE_TICKER_URL, timeout=10, verify=False)
                resp2.raise_for_status()
                for item in resp2.json():
                    sym2 = item.get('symbol', '')
                    if sym2.endswith('USDT'):
                        base2 = sym2[:-4]
                        if base2 in cache:
                            try:
                                cache[base2]['mark_price'] = float(item['price'])
                            except:
                                pass
            except Exception as e:
                print(f"체결가 갱신 실패(markPrice 사용): {e}")
            _funding_cache = cache
            _funding_cache_time = now
            print(f"✅ 펀딩레이트/가격 {len(cache)}개 갱신")
            return cache
        except Exception as e:
            print(f"펀딩레이트 갱신 실패: {e}")
            return _funding_cache

# ============================================================
# 비트코인 공포&탐욕 지수 (Alternative.me, 무료/무인증 API)
# 하루에 한 번만 갱신되는 지표라 캐시를 넉넉히(1시간) 둔다.
# ============================================================
FNG_URL = "https://api.alternative.me/fng/?limit=1"
_fng_cache = {"value": None, "classification": ""}
_fng_cache_time = 0
_fng_lock = threading.Lock()

def get_fear_greed_index():
    global _fng_cache, _fng_cache_time
    now = time.time()
    with _fng_lock:
        if now - _fng_cache_time < 3600 and _fng_cache["value"] is not None:
            return _fng_cache
        try:
            resp = requests.get(FNG_URL, timeout=10, verify=False)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if data:
                item = data[0]
                _fng_cache = {
                    "value": int(item.get("value", 0)),
                    "classification": item.get("value_classification", ""),
                }
                _fng_cache_time = now
                print(f"✅ 공포탐욕지수 갱신: {_fng_cache['value']} ({_fng_cache['classification']})")
            return _fng_cache
        except Exception as e:
            print(f"공포탐욕지수 갱신 실패: {e}")
            return _fng_cache

# ============================================================
# 디스코드 알림 — 롱/숏 진입컷을 새로 넘은 코인이 생기면 웹후크로 알림.
# 매 사이클 넘는 코인마다 보내면 스팸이라, "컷 아래로 떨어졌다가 다시 넘을 때만"
# 재알림하도록 상태를 기억한다(_discord_alerted에 지금 넘어있는 티커+방향을 계속 담아둠).
# ============================================================
_discord_alerted = set()  # {(ticker, 'long'|'short'), ...} — 지금 컷을 넘어있는 것들
_discord_last_alert_time = {}  # (ticker, direction) -> 마지막 알림 보낸 시각
_DISCORD_ALERT_COOLDOWN = 300  # 같은 코인+방향은 이 시간(초) 안에는 재알림 안 함(경계 플래핑 스팸 방지)

def send_discord_alert(message):
    """디스코드 웹후크 호출은 느리거나(네트워크 지연) 실패할 수 있는데, score_updater의
    메인 루프 안에서 동기(블로킹)로 호출하면 그 사이클 전체가 지연되고, 컷 경계에서
    점수가 왔다갔다하는 코인이 있으면 매 사이클 알림을 재시도하면서 서버 전체가
    느려지는 문제가 있었다(클라이언트 정렬/색칠이 같이 느려지는 걸로 나타남) —
    그래서 백그라운드 스레드에서 쏘고 바로 리턴한다(결과를 기다리지 않음)."""
    if not DISCORD_WEBHOOK_URL:
        return
    def _fire():
        try:
            requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=8)
        except Exception as e:
            print(f"디스코드 알림 실패: {e}")
    threading.Thread(target=_fire, daemon=True).start()

def check_discord_alerts(results, min_score):
    """score_updater 한 사이클이 끝날 때마다 호출. 이번에 새로 컷을 넘은 코인만 알림 보낸다."""
    if not DISCORD_WEBHOOK_URL:
        return
    now_over = set()
    for r in results:
        t = r.get('ticker', '')
        if r.get('long_score', 0) >= min_score:
            now_over.add((t, 'long'))
        if r.get('short_score', 0) >= min_score:
            now_over.add((t, 'short'))

    newly_over = now_over - _discord_alerted
    now = time.time()
    for t, direction in newly_over:
        key = (t, direction)
        last = _discord_last_alert_time.get(key, 0)
        if now - last < _DISCORD_ALERT_COOLDOWN:
            continue  # 컷 경계에서 방금 왔다갔다한 것 — 스팸 방지로 건너뜀
        r = next((x for x in results if x['ticker'] == t), None)
        if not r:
            continue
        score = r.get('long_score' if direction == 'long' else 'short_score', 0)
        emoji = "🟢" if direction == 'long' else "🔴"
        label = "롱" if direction == 'long' else "숏"
        send_discord_alert(f"{emoji} **{t}** {label} 진입컷 돌파 ({score}점, 컷 {min_score}점)")
        _discord_last_alert_time[key] = now

    _discord_alerted.clear()
    _discord_alerted.update(now_over)

# ============================================================
# 미체결약정(Open Interest) - 바이낸스 선물 OI 히스토리
# ============================================================
# 바이낸스는 전 심볼 OI를 한 번에 주는 엔드포인트가 없어(premiumIndex처럼) 심볼별로
# 개별 호출해야 한다. 매 사이클(SCORE_INTERVAL)마다 호출하면 부하가 크므로,
# 심볼별 캐시(OI_CACHE_TTL)를 둬서 호출 빈도를 제한한다.
BINANCE_OI_HIST_URL = "https://fapi.binance.com/futures/data/openInterestHist"
_oi_cache = {}        # base -> (oi_change_pct, oi_value)
_oi_cache_time = {}   # base -> 마지막 조회 시각
_oi_lock = threading.Lock()

def get_oi_change_1h(base):
    """
    바이낸스 선물 OI 히스토리(5분 간격)에서 약 1시간 전 대비 변동률(%)을 구한다.
    period=5m, limit=13으로 최근 1시간(+여유 1구간)치를 받아 가장 오래된 값과
    가장 최신 값을 비교한다. 반환값은 (oi_change_pct, 최신 OI값).
    """
    global _oi_cache, _oi_cache_time
    now = time.time()
    with _oi_lock:
        cached = _oi_cache.get(base)
        cached_time = _oi_cache_time.get(base, 0)
        if cached is not None and now - cached_time < OI_CACHE_TTL:
            return cached
    try:
        symbol = f"{base}USDT"
        resp = requests.get(
            BINANCE_OI_HIST_URL,
            params={"symbol": symbol, "period": "5m", "limit": 13},
            timeout=8, verify=False
        )
        resp.raise_for_status()
        items = resp.json()
        if not items or len(items) < 2:
            result = (0.0, None)
        else:
            oldest = float(items[0]['sumOpenInterest'])
            newest = float(items[-1]['sumOpenInterest'])
            change_pct = ((newest - oldest) / oldest * 100) if oldest > 0 else 0.0
            result = (round(change_pct, 2), newest)
        with _oi_lock:
            _oi_cache[base] = result
            _oi_cache_time[base] = now
        return result
    except Exception:
        # 실패 시 직전 캐시값이라도 재사용 (없으면 0/None)
        with _oi_lock:
            cached = _oi_cache.get(base)
        return cached if cached is not None else (0.0, None)

# ============================================================
# 롱/숏 계정 비율 - 바이낸스 globalLongShortAccountRatio
# ============================================================
# "현재 화면의 롱 수 / 숏 수 비율"에 대응하는 지표로, 바이낸스가 집계하는
# 심볼별 전체 계정 롱/숏 비율(longAccount/shortAccount)을 사용한다.
# OI와 마찬가지로 심볼별 개별 호출이라 캐시(OI_CACHE_TTL 재사용)로 빈도를 제한한다.
BINANCE_LSR_URL = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
_lsr_cache = {}        # base -> longShortRatio(float, longAccount/shortAccount)
_lsr_cache_time = {}
_lsr_lock = threading.Lock()

def get_long_short_ratio(base):
    """
    심볼의 전체 계정 롱/숏 비율(longAccount/shortAccount)을 반환한다.
    실패하거나 데이터가 없으면 None.
    """
    global _lsr_cache, _lsr_cache_time
    now = time.time()
    with _lsr_lock:
        cached = _lsr_cache.get(base)
        cached_time = _lsr_cache_time.get(base, 0)
        if cached is not None and now - cached_time < OI_CACHE_TTL:
            return cached
    try:
        symbol = f"{base}USDT"
        resp = requests.get(
            BINANCE_LSR_URL,
            params={"symbol": symbol, "period": "5m", "limit": 1},
            timeout=8, verify=False
        )
        resp.raise_for_status()
        items = resp.json()
        if not items:
            result = None
        else:
            result = round(float(items[-1]['longShortRatio']), 3)
        with _lsr_lock:
            _lsr_cache[base] = result
            _lsr_cache_time[base] = now
        return result
    except Exception:
        with _lsr_lock:
            cached = _lsr_cache.get(base)
        return cached

# ============================================================
# 단기(30분/1시간) 가격 변동률 - 자체 수집한 가격 히스토리 기반
# ============================================================
def record_price_history(updates, ts=None):
    """price_updater가 매 폴링마다 호출해서 (시각, 가격)을 ticker별로 누적한다."""
    ts = ts or time.time()
    with price_history_lock:
        for ticker, price in updates.items():
            if price and price > 0:
                price_history[ticker].append((ts, price))

def get_recent_pct_change(ticker, minutes=30):
    """
    자체 수집한 price_history에서 약 `minutes`분 전 대비 현재가 변동률(%)을 구한다.
    캔들 fetch 없이 price_updater가 이미 모아둔 데이터를 재사용하므로 API 호출이 추가로 들지 않는다.
    앱을 막 시작해서 히스토리가 부족하면 0.0을 반환한다.
    """
    with price_history_lock:
        hist = price_history.get(ticker)
        if not hist or len(hist) < 2:
            return 0.0
        now_ts, now_price = hist[-1]
        target_ts = now_ts - minutes * 60
        old_price = None
        for ts, p in hist:
            if ts <= target_ts:
                old_price = p
            else:
                break
        if old_price is None:
            old_price = hist[0][1]
    if not old_price or old_price <= 0:
        return 0.0
    return round((now_price - old_price) / old_price * 100, 3)

# ============================================================
# CSV
# ============================================================
HISTORY_FILE = os.path.join(SCRIPT_DIR, "trade_history_usd.csv")

def save_to_csv():
    try:
        with open(DATA_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['balance', str(round(balance, 2))])
            writer.writerow(['bank_balance', str(round(bank_balance, 2))])
            writer.writerow(['bank_total_deposit', str(round(bank_total_deposit, 2))])
            writer.writerow(['bank_total_spent', str(round(bank_total_spent, 2))])
            writer.writerow(['positions'])
            for ticker, pos in positions.items():
                writer.writerow([ticker, pos.get('entry_price', 0), pos.get('amount', 0),
                                 pos.get('leverage', 5), pos.get('position_type', 'long'),
                                 pos.get('entry_fee', 0),
                                 pos.get('entry_time', datetime.now()).strftime('%Y-%m-%d %H:%M:%S'),
                                 pos.get('entry_score', 0)])
        print(f"✅ 저장 완료: {DATA_FILE}")
    except Exception as e:
        print(f"저장 실패: {e}")

def append_history_csv(record):
    """거래 완료 시 history CSV에 한 줄 추가"""
    try:
        file_exists = os.path.exists(HISTORY_FILE)
        with open(HISTORY_FILE, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['type','ticker','direction','amount','leverage',
                                 'entry_price','exit_price','pnl','pnl_rate_pct','entry_time','exit_time'])
            writer.writerow([
                record.get('type',''),
                record.get('ticker',''),
                record.get('direction',''),
                record.get('amount', 0),
                record.get('leverage', 0),
                record.get('entry_price', 0),
                record.get('exit_price', 0),
                record.get('pnl', 0),
                record.get('pnl_rate_pct', 0),
                record.get('entry_time', ''),
                record.get('exit_time', ''),
            ])
    except Exception as e:
        print(f"history CSV 저장 실패: {e}")

def load_history_csv():
    """앱 시작 시 history CSV 로드"""
    global trade_history
    if not os.path.exists(HISTORY_FILE):
        return
    try:
        with open(HISTORY_FILE, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                trade_history.append({
                    'type': row.get('type',''),
                    'ticker': row.get('ticker',''),
                    'direction': row.get('direction',''),
                    'amount': float(row.get('amount', 0)),
                    'leverage': int(row.get('leverage', 0)),
                    'entry_price': float(row.get('entry_price', 0)),
                    'exit_price': float(row.get('exit_price', 0)),
                    'pnl': float(row.get('pnl', 0)),
                    'pnl_rate_pct': float(row.get('pnl_rate_pct', 0)),
                    'entry_time': row.get('entry_time', ''),
                    'exit_time': row.get('exit_time', ''),
                })
        print(f"✅ 거래기록 {len(trade_history)}건 로드")
    except Exception as e:
        print(f"history 로드 실패: {e}")

def load_from_csv():
    global balance, bank_balance, bank_total_deposit, bank_total_spent
    if not os.path.exists(DATA_FILE):
        print("새 시뮬레이션 시작")
        return False
    try:
        positions.clear()
        with open(DATA_FILE, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            mode = None
            for row in reader:
                if not row: continue
                if row[0] == 'balance':
                    balance = float(row[1])
                elif row[0] == 'bank_balance':
                    bank_balance = float(row[1])
                elif row[0] == 'bank_total_deposit':
                    bank_total_deposit = float(row[1])
                elif row[0] == 'bank_total_spent':
                    bank_total_spent = float(row[1])
                elif row[0] == 'positions':
                    mode = 'positions'; continue
                if mode == 'positions' and len(row) >= 7:
                    try:
                        entry_time = datetime.strptime(row[6], '%Y-%m-%d %H:%M:%S') if row[6] and row[6].strip() else datetime.now()
                        positions[row[0]] = {
                            "entry_price": float(row[1]),
                            "amount": float(row[2]),
                            "leverage": int(row[3]),
                            "position_type": row[4],
                            "entry_fee": float(row[5]),
                            "entry_time": entry_time,
                            "entry_score": float(row[7]) if len(row) >= 8 and row[7] != '' else 0,
                        }
                    except: pass
        print(f"✅ 로드 완료 | 포지션 {len(positions)}개")
        return True
    except Exception as e:
        print(f"로드 실패: {e}")
        return False

# ============================================================
# 지표 함수
# ============================================================
def calculate_rsi(data, period=14):
    try:
        delta = data['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))
    except:
        return pd.Series([50.0] * len(data))

def calculate_macd(df):
    try:
        ema12 = df['close'].ewm(span=12, adjust=False).mean()
        ema26 = df['close'].ewm(span=26, adjust=False).mean()
        signal = (ema12 - ema26).ewm(span=9, adjust=False).mean()
        return (ema12 - ema26) - signal
    except:
        return pd.Series([0.0] * len(df))

def calculate_vol_zscore(df):
    try:
        if len(df) < 20: return 0.0
        vol_ma = df['volume'].rolling(20).mean().iloc[-1]
        vol_std = df['volume'].rolling(20).std().iloc[-1]
        return round((df['volume'].iloc[-1] - vol_ma) / vol_std, 2) if vol_std > 0 else 0.0
    except:
        return 0.0

def calculate_cvd(df, window=CVD_WINDOW_CANDLES):
    """
    OHLCV만으로 추정하는 볼륨 델타(CVD), 거래량(코인 개수) 기준.
    실제 체결 tick 데이터가 없으므로 Chaikin 방식의
    Money Flow Multiplier로 캔들 내 매수/매도 비중을 추정해
    거래량(volume)에 곱한 뒤, 전체 누적이 아니라 최근 `window`개
    캔들(기준 캔들 = CANDLE_INTERVAL)만 합산하는 롤링 방식으로 계산한다.
    MFM = ((close-low) - (high-close)) / (high-low)
    CVD = rolling_sum(MFM * volume, window)
    """
    try:
        high = df['high']
        low = df['low']
        close = df['close']
        volume = df['volume']
        rng = (high - low)
        rng = rng.where(rng != 0, np.nan)
        mfm = ((close - low) - (high - close)) / rng
        mfm = mfm.fillna(0)
        mfv = mfm * volume
        return mfv.rolling(window=window, min_periods=1).sum()
    except:
        return pd.Series([0.0] * len(df))

def calculate_extension_pct(df, periods=10):
    """
    최근 `periods` 캔들 동안 가격이 이미 얼마나 움직였는지(%)를 계산한다.
    이미 많이 오르거나 내린 코인을 "뒤늦게 쫓아가며" 높은 점수를 주는 문제를
    완화하기 위한 보조 지표로, 점수 계산 시 모멘텀 보너스를 깎거나
    과열 페널티를 주는 데 사용한다.
    """
    try:
        if len(df) < periods + 1:
            return 0.0
        base = float(df['close'].iloc[-(periods + 1)])
        now = float(df['close'].iloc[-1])
        if base <= 0:
            return 0.0
        return (now - base) / base * 100
    except:
        return 0.0

def calculate_atr_1h(df, period=ATR_PERIOD):
    """
    1시간 기준 ATR(Average True Range)을 현재가 대비 %로 계산한다.
    기준 캔들(CANDLE_INTERVAL)이 이미 1시간봉이므로 리샘플링 없이 바로 계산한다.
    VolZ가 '거래량'의 폭발을 보여준다면, 이 지표는 '가격' 자체의 움직임 크기를
    보여준다 — 움직임이 너무 죽어있는 코인을 거르는 필터로 쓰기 좋다.
    """
    try:
        h1 = df[['open', 'high', 'low', 'close', 'volume']].dropna()
        if len(h1) < period + 1:
            return 0.0
        prev_close = h1['close'].shift(1)
        tr = pd.concat([
            h1['high'] - h1['low'],
            (h1['high'] - prev_close).abs(),
            (h1['low'] - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        last_close = float(h1['close'].iloc[-1])
        if last_close <= 0 or pd.isna(atr):
            return 0.0
        return round(float(atr) / last_close * 100, 2)
    except Exception:
        return 0.0

def calculate_bb_percent(df, latest):
    try:
        upper = float(latest.get('BB_UPPER', np.nan))
        lower = float(latest.get('BB_LOWER', np.nan))
        if np.isnan(upper) or np.isnan(lower) or upper == lower:
            return 50.0
        return (float(latest['close']) - lower) / (upper - lower) * 100
    except:
        return 50.0

def check_bullish_rsi_divergence(df, lookback=20):
    """
    가격은 더 낮은 저점(Lower Low)을 만들지만 RSI는 더 높은 저점(Higher Low)을
    만드는 상승 다이버전스를 탐지한다. 직전 5개 캔들만 보는 대신, lookback
    구간을 전반부/후반부로 나눠 각 구간의 저점을 비교함으로써 노이즈에
    덜 취약하게 만든다.
    """
    try:
        if len(df) < lookback: return False
        recent_price = df['close'].iloc[-lookback:].reset_index(drop=True)
        recent_rsi = df['RSI'].iloc[-lookback:].reset_index(drop=True)
        half = lookback // 2
        i1 = recent_price.iloc[:half].idxmin()
        i2 = recent_price.iloc[half:].idxmin()
        if i2 <= i1:
            return False
        price1, price2 = recent_price[i1], recent_price[i2]
        rsi1, rsi2 = recent_rsi[i1], recent_rsi[i2]
        return bool(price2 <= price1 * 1.005 and rsi2 > rsi1 * 1.02)
    except:
        return False

def check_bearish_rsi_divergence(df, lookback=20):
    """
    가격은 더 높은 고점(Higher High)을 만들지만 RSI는 더 낮은 고점(Lower High)을
    만드는 하락 다이버전스를 탐지한다. check_bullish_rsi_divergence와 대칭.
    """
    try:
        if len(df) < lookback: return False
        recent_price = df['close'].iloc[-lookback:].reset_index(drop=True)
        recent_rsi = df['RSI'].iloc[-lookback:].reset_index(drop=True)
        half = lookback // 2
        i1 = recent_price.iloc[:half].idxmax()
        i2 = recent_price.iloc[half:].idxmax()
        if i2 <= i1:
            return False
        price1, price2 = recent_price[i1], recent_price[i2]
        rsi1, rsi2 = recent_rsi[i1], recent_rsi[i2]
        return bool(price2 >= price1 * 0.995 and rsi2 < rsi1 * 0.98)
    except:
        return False

def calculate_oi_synergy(price_chg, oi_change_pct):
    """
    가격 변화(price_chg)와 OI 변동률(oi_change_pct, 1시간 기준)을 조합해
    '진짜 돈이 들어와서 미는지'를 판별한다.
      가격↑ + OI↑ : 신규 롱 유입(진성 상승, 롱 스퀴즈 가능성) → 롱 가점
      가격↑ + OI↓ : 숏 커버링에 의한 단기 반등(지속성 약함) → 숏 가점(반전 기대)
      가격↓ + OI↑ : 신규 숏 유입(진성 하락) → 숏 가점
      가격↓ + OI↓ : 롱 청산/포지션 정리(저점 다지기 가능성) → 롱 가점(반전 기대)
    반환값: (long_bonus, short_bonus), 둘 다 0~10 범위.
    (참고용 보조 지표. 메인 점수 체계의 OI 배점은 score_oi_long/short 가 담당한다.)
    """
    long_bonus = 0.0
    short_bonus = 0.0
    try:
        if price_chg > 0 and oi_change_pct > 0:
            long_bonus = min(10.0, oi_change_pct * 1.5)
        elif price_chg > 0 and oi_change_pct < 0:
            short_bonus = min(10.0, abs(oi_change_pct) * 1.0)
        elif price_chg < 0 and oi_change_pct > 0:
            short_bonus = min(10.0, oi_change_pct * 1.5)
        elif price_chg < 0 and oi_change_pct < 0:
            long_bonus = min(10.0, abs(oi_change_pct) * 1.0)
    except Exception:
        pass
    return round(long_bonus, 1), round(short_bonus, 1)

# ============================================================
# 105점 만점 점수 체계 (추세추종형 개편안 v3, 최종 100점 환산)
#   ① EMA 추세 (EMA20/60/120 삼중 정배열)   20점
#   ② 가격위치 (RSI+BB% 결합조건)            20점
#   ③ CVD 추세                              15점
#   ④ OI 미체결약정                         15점
#   ⑤ VolZ 거래량                           15점
#   ⑥ 30분 모멘텀                           15점
#   ⑦ ATR/거래대금 최소유동성 필터(통과시만)  5점
#   합계 105점 → final = round(raw합 / 105 × 100), 0~100 클램프
#   (L/S 역발상, Funding, RSI Delta는 "추세추종 진입 시 노이즈 유발"로 v3에서 완전 삭제)
#
# [원안 대비 구현 메모 — 문서에 정확한 수치/구간이 없어 임의로 채운 부분]
#   - 가격위치: 문서에 나온 3개 구간(20/10/5) 외의 조합은 0점으로 처리
#   - OI: 0%≤ΔOI<1% 구간이 문서에 없어 0점(하위 구간)으로 편입
#   - VolZ: 1.0≤VolZ<1.2 구간이 문서에 없어 <1.2 전체를 3점 구간으로 편입
#   - 30분 모멘텀: 문서에 없는 중간 구간(과열 직전)은 10점으로 보간
#   - ATR/거래대금 필터: "최소 유동성 하한선" 구체 수치가 없어 ATR≥0.3% & 24h거래대금
#     ≥1천만원을 기준으로 임의 설정 (통과 5점 / 미통과 0점, 등급 없는 필터형)
# ============================================================
TOTAL_SCORE_WEIGHT = 105  # 위 7개 항목 배점의 합. 항목을 추가/변경하면 같이 맞춰야 함.
                           # 롱 점수는 이 중 OI(15점)를 안 받지만, 분모는 그대로 105를 쓴다 —
                           # 분모를 90으로 줄이면 남은 점수들이 상대적으로 부풀어서 진입컷을
                           # 넘는 코인이 오히려 127개->345개로 늘어나는 부작용이 실측으로
                           # 확인됐다(과열 캡의 효과가 재정규화로 상쇄돼버림). 분모를 그대로
                           # 두면 OI를 안 받는 만큼 롱 점수 상한이 자연스럽게 ~86점으로
                           # 낮아지면서 진입컷 비교가 원래 기준과 계속 맞는다.

def score_ema_trend(price, ema20, ema60, ema120, direction):
    """
    EMA 삼중(20/60/120) 정배열 점수(최대 20점).
      롱: EMA20>EMA60>EMA120 이고 가격>EMA20 → 20 (완벽한 정배열)
          정배열이나 가격이 EMA20~EMA60 사이(눌림목) → 10
          역배열이거나 EMA60 하향이탈 → 0
      숏: 대칭.
    """
    try:
        if price is None or ema20 is None or ema60 is None or ema120 is None:
            return 0
        if direction == 'long':
            if ema20 > ema60 > ema120 and price > ema20:
                return 20
            elif ema20 > ema60 and ema60 <= price <= ema20:
                return 10
            elif price < ema60:
                return 0
            return 0
        else:
            if ema20 < ema60 < ema120 and price < ema20:
                return 20
            elif ema20 < ema60 and ema20 <= price <= ema60:
                return 10
            elif price > ema60:
                return 0
            return 0
    except Exception:
        return 0

def score_price_position_long(rsi, bb_percent):
    """
    가격위치 결합조건 점수(최대 20점). bb_percent는 0~100 스케일(%B*100)이라
    문서의 %B(0~1) 기준값에 100을 곱해 맞췄다.
      20점: %B≥80 & 55≤RSI≤70 (과열 초입 강한 분출)
      10점: %B≥70 & RSI>80 (단기 과열권 진입 부담)
       5점: %B<50 또는 RSI<45 (추세 상실)
       0점: 위 어느 조건에도 안 맞음
    """
    try:
        if bb_percent >= 80 and 55 <= rsi <= 70:
            return 20
        elif bb_percent >= 70 and rsi > 80:
            return 10
        elif bb_percent < 50 or rsi < 45:
            return 5
        return 0
    except Exception:
        return 0

def score_price_position_short(rsi, bb_percent):
    """가격위치 결합조건 숏 점수(최대 20점, 롱과 대칭)."""
    try:
        if bb_percent <= 20 and 30 <= rsi <= 45:
            return 20
        elif bb_percent <= 30 and rsi < 20:
            return 10
        elif bb_percent > 50 or rsi > 55:
            return 5
        return 0
    except Exception:
        return 0

def cvd_direction(cvd_diff, vol_window_sum):
    """CVD 추세 방향 판정. 최근 거래량 대비 2%를 기준선(eps)으로 +1/0/-1 반환."""
    eps = 0.02 * vol_window_sum if vol_window_sum > 0 else 0.0
    if cvd_diff > eps: return 1
    elif cvd_diff < -eps: return -1
    else: return 0

def score_cvd_trend(cvd_diff, vol_window_sum, direction):
    """
    CVD 추세 점수(최대 15점). 일치 15 / 보합 5 / 반대 0 — 진성매수세(가격+CVD 동행)
    확인용. 문서의 "전고점 돌파" 여부까지는 별도 이력 추적이 필요해 방향 일치
    여부로 단순화했다.
    """
    want = 1 if direction == 'long' else -1
    d = cvd_direction(cvd_diff, vol_window_sum)
    if d == want: return 15
    elif d == 0: return 5
    return 0

def score_oi_v3(oi_change_pct):
    """OI 미체결약정 점수(최대 15점, 롱/숏 공통 — ΔOI 자체의 증가 강도만 본다)."""
    try:
        if oi_change_pct >= 3: return 15
        elif oi_change_pct >= 1: return 7
        return 0
    except Exception:
        return 0

def score_volz_v3(vol_z):
    """VolZ 거래량 점수(최대 15점, 롱/숏 공통)."""
    try:
        if vol_z >= 2.0: return 15
        elif vol_z >= 1.2: return 10
        return 3
    except Exception:
        return 3

def score_chg30m_long(chg_30m):
    """
    최근 30분 모멘텀 롱 점수(최대 15점).
      0.5%~2.5%: 15 (안정적 양봉) | 4.0%초과: 5 (과열, 꼬리물림 위험)
      2.5%초과~4.0%: 10 (과열 직전, 문서에 없는 구간 보간)
      그 외(0.5% 미만/음수): 0 (모멘텀 부족)
    """
    if 0.5 <= chg_30m <= 2.5: return 15
    elif chg_30m > 4.0: return 5
    elif 2.5 < chg_30m <= 4.0: return 10
    return 0

def score_chg30m_short(chg_30m):
    """최근 30분 모멘텀 숏 점수(최대 15점, 롱과 대칭)."""
    if -2.5 <= chg_30m <= -0.5: return 15
    elif chg_30m < -4.0: return 5
    elif -4.0 <= chg_30m < -2.5: return 10
    return 0

def score_liquidity_filter(atr_pct, vol_24h_m):
    """
    ATR/거래대금 '최소 유동성 하한선' 필터(최대 5점, 등급 없이 통과/미통과만).
    죽어있는 코인(변동성·거래대금 둘 다 바닥)을 걸러내는 용도로, 문서 취지대로
    점수제가 아니라 필터형으로 구현했다. 임계값(ATR 0.3%, 24h 거래대금 1천만원)은
    문서에 구체 수치가 없어 임의로 설정 — 데이터 보면서 조정 필요.
    """
    try:
        if atr_pct >= 0.3 and vol_24h_m >= 10:
            return 5
        return 0
    except Exception:
        return 0

def score_overextension_penalty_cap(final_score, ema_pts, pp_pts, cvd_pts, oi_pts, m30_pts, volz_pts, liq_pts):
    """
    과열 상한 캡. v3 롱/숏 세부 7개 항목은 개별로 보면 forward-return과의 상관관계가
    거의 0(실측: -0.08~+0.05)이라 무해해 보이는데, 59시간치 실측 데이터로 확인해보니
    "7개 중 5개 이상이 동시에 거의 만점(90%+)"인 경우에만 60분 후 시장대비 초과수익률이
    뚜렷하게 마이너스(-0.52%)로 뒤집혔다(0~4개 겹칠 땐 오히려 점수가 높을수록 좋아지는
    정상적인 패턴이었음). "모든 지표가 동시에 GO"인 상태 = 이미 다 오른 뒤 반전 직전인
    경우가 많다는 뜻으로 해석한다.
    처음엔 고정 감점(-15)으로 시도했는데, 85점짜리가 71점이 되는 식으로 여전히 진입컷을
    넘는 경우가 많아서 실효성이 없었다(실측으로 확인함) — 그래서 감점이 아니라 진입컷보다
    확실히 낮은 값으로 상한을 씌우는 방식으로 바꿨다.
    """
    maxes = [(ema_pts, 20), (pp_pts, 20), (cvd_pts, 15), (oi_pts, 15),
             (m30_pts, 15), (volz_pts, 15), (liq_pts, 5)]
    n_maxed = sum(1 for v, mx in maxes if v >= mx * 0.9)
    if n_maxed >= 5:
        return min(final_score, 45)
    return final_score

def calculate_long_score(rsi, bb_percent, cvd_diff, vol_window_sum, ls_ratio, oi_change_pct, chg_30m,
                          price_chg, extension_pct, vol_z=0.0, rsi_delta=0.0,
                          atr_pct=0.0, ema20=None, ema60=None, oi_notional_usd=None,
                          funding_rate=0.0, trade_value_usd=None,
                          price=None, ema120=None, vol_24h_m=0):
    """
    실질 최대 90점(105점 만점 배점 중 EMA삼중20+가격위치20+CVD15+VolZ15+30분모멘텀15+유동성5)
    롱 점수. OI(oi_sc, 15점)는 일부러 뺐다 — 59시간 실측으로 OI 급증이 롱보다 오히려 하락과
    상관관계가 있는 걸 확인해서(숏 점수엔 그대로 유지, calculate_short_score 참고).
    분모는 그대로 TOTAL_SCORE_WEIGHT(105)를 써서 /105×100 환산한다 — 분모를 90으로 줄이면
    남은 항목들 점수가 상대적으로 부풀어서 진입컷을 넘는 코인이 오히려 늘어나는 부작용이
    있었다(실측으로 확인, 127개→345개). 그래서 롱 점수 실질 상한은 자연히 ~86점이 된다.
    """
    raw = 0
    try:
        p_ema = score_ema_trend(price, ema20, ema60, ema120, 'long')
        p_pp = score_price_position_long(rsi, bb_percent)
        p_cvd = score_cvd_trend(cvd_diff, vol_window_sum, 'long')
        p_volz = score_volz_v3(vol_z)
        p_m30 = score_chg30m_long(chg_30m)
        p_liq = score_liquidity_filter(atr_pct, vol_24h_m)
        raw = p_ema + p_pp + p_cvd + p_volz + p_m30 + p_liq
    except Exception:
        final = round(raw / TOTAL_SCORE_WEIGHT * 100)
        return max(0, min(final, 100))
    final = round(raw / TOTAL_SCORE_WEIGHT * 100)
    # oi_sc는 롱 총점에서 뺀 것과 동일하게, 과열 캡 판정(몇 개 항목이 동시에 만점인지)에서도
    # 제외한다 — 백테스트 때 검증한 조건과 정확히 똑같이 맞추기 위함(0을 넘겨서 항상 미달 처리).
    final = score_overextension_penalty_cap(final, p_ema, p_pp, p_cvd, 0, p_m30, p_volz, p_liq)
    return max(0, min(final, 100))

def calculate_short_score(rsi, bb_percent, cvd_diff, vol_window_sum, ls_ratio, oi_change_pct, chg_30m,
                           price_chg, extension_pct, vol_z=0.0, rsi_delta=0.0,
                           atr_pct=0.0, ema20=None, ema60=None, oi_notional_usd=None,
                           funding_rate=0.0, trade_value_usd=None,
                           price=None, ema120=None, vol_24h_m=0):
    """105점 만점 숏 점수 (롱과 대칭, 과열 상한 캡도 동일 적용). 최종 /105×100 환산.
    ema_s(EMA 완전 역배열, 20점)는 59시간 실측으로 120~240분 후 오히려 가격이 반등하는
    경향이 확인돼서(완전히 다 떨어진 뒤라는 뜻으로 해석) 10점으로 다운그레이드한다 —
    "부분 역배열"과 "완전 역배열"을 더 이상 구분해서 보너스 주지 않는다."""
    raw = 0
    try:
        p_ema = score_ema_trend(price, ema20, ema60, ema120, 'short')
        if p_ema >= 20:
            p_ema = 10
        p_pp = score_price_position_short(rsi, bb_percent)
        p_cvd = score_cvd_trend(cvd_diff, vol_window_sum, 'short')
        p_oi = score_oi_v3(oi_change_pct)
        p_volz = score_volz_v3(vol_z)
        p_m30 = score_chg30m_short(chg_30m)
        p_liq = score_liquidity_filter(atr_pct, vol_24h_m)
        raw = p_ema + p_pp + p_cvd + p_oi + p_volz + p_m30 + p_liq
    except Exception:
        final = round(raw / TOTAL_SCORE_WEIGHT * 100)
        return max(0, min(final, 100))
    final = round(raw / TOTAL_SCORE_WEIGHT * 100)
    final = score_overextension_penalty_cap(final, p_ema, p_pp, p_cvd, p_oi, p_m30, p_volz, p_liq)
    return max(0, min(final, 100))


# ============================================================
# Pre-Pump / Pre-Short 점수 체계 (100점 만점, 매집형 개편안 v3) — "출발 전 매집 구간" 탐지
#   장기 매집 사이클(며칠~몇 주) 탐지용으로 완전히 새로 설계. 기존 v1/v2는 "롱/숏
#   진입"(추세추종) 로직을 그대로 갖다 써서 EMA 정배열·RSI·30분모멘텀 비중이 과도했고,
#   그 결과 "이미 많이 오른 종목"이 오히려 고득점하는 모순이 있었다는 지적을 반영해
#   OI 지속성·CVD 누적·EMA 압축·박스권 위치 중심으로 재설계했다.
#
#   매집(prepump) 100점 = OI지속증가(25) + CVD누적증가(20) + EMA압축도(15)
#                        + ATR(10) + VolZ(10) + 가격위치(20일박스, 10) + RSI(5) + 최근3일상승패널티(5)
#   분산(preshort) 100점 = OI감소·불일치(25) + CVD지속감소(20) + EMA과이격(15)
#                         + ATR급증(10) + VolZ폭발(10) + 신고가부근(10) + RSI70+(5) + 최근급등(5)
#   ENABLE_PREPUMP_SCORE=False면 process_ticker에서 아예 호출하지 않고 0을 채운다.
#
# [원안 대비 구현 메모 — 서버가 갖고 있는 데이터 한계로 근사한 부분]
#   - OI 지속성은 "1시간 변화율"(oi_change_pct)을 그대로 쓴다. 원안처럼 진짜 며칠짜리
#     누적 OI 추이를 보려면 별도의 장기 OI 히스토리 캐시가 필요한데, 그건 이번 개편
#     범위 밖이라 1h 변화율로 근사했다 — 절대적 지속성이 아니라 "지금 이 순간의 유입
#     강도"로 봐야 함.
#   - 가격위치(20일박스)/최근3일상승률은 fetch_candlestick이 이미 받아온 캔들(df)에서
#     최근 N개를 뽑아 계산한다. N은 "일수"가 아니라 "캔들 개수"로 고정했다(예: 20일
#     박스 → 최근 20캔들, 3일 상승률 → 기준봉이 1h면 최근 72캔들). 기준봉을 바꾸면
#     같이 바뀐다 — 1h가 아니면 원안의 "일" 단위와 정확히 안 맞을 수 있다.
#   - CVD 누적증가의 "가격 횡보+CVD↑ → +3, 가격 하락+CVD↑ → +5" 보너스는 가격 지표로
#     30분 변동률(chg_30m)을 재사용했다.
# ============================================================

def _ema_spread_pct(ema20, ema60, ema120):
    """EMA20/60/120 세 값이 서로 얼마나 벌어져 있는지(%, ema60 기준)."""
    try:
        if not ema20 or not ema60 or not ema120 or ema60 <= 0:
            return None
        return (max(ema20, ema60, ema120) - min(ema20, ema60, ema120)) / ema60 * 100
    except Exception:
        return None

def score_oi_persistence(oi_change_pct, direction):
    """
    OI 지속 증가 점수(매집 25점 / 분산 25점). 매집은 OI가 꾸준히 늘어야 만점,
    분산은 OI가 줄거나(청산/차익실현) 가격 대비 안 따라오면 만점.
    """
    try:
        if direction == 'prepump':
            if oi_change_pct >= 10: return 25
            elif oi_change_pct >= 7: return 22
            elif oi_change_pct >= 5: return 18
            elif oi_change_pct >= 3: return 13
            elif oi_change_pct >= 1: return 8
            elif oi_change_pct >= 0: return 4
            return 0
        else:
            if oi_change_pct < 0: return 25
            elif oi_change_pct < 1: return 12
            return 0
    except Exception:
        return 0

def score_cvd_cumulative(cvd_1h, direction, chg_30m_pct=0.0):
    """
    CVD 누적 증가 점수(매집 20점 / 분산 20점). 매집은 가격보다 CVD 증가율이 훨씬
    중요하다는 원안을 반영해, "가격은 안 가는데 CVD만 오른다" 상황에 보너스를 준다.
    (cvd_1h는 별도 API가 없어 CVD_WINDOW_CANDLES 구간 변화량을 근사치로 쓴다.)
    """
    try:
        if direction == 'prepump':
            if cvd_1h > 0:
                if cvd_1h >= 100000: base = 20
                elif cvd_1h >= 30000: base = 17
                elif cvd_1h >= 5000: base = 13
                else: base = 7
                if -0.3 <= chg_30m_pct <= 0.3:
                    base += 3   # 가격 횡보 + CVD 증가
                elif chg_30m_pct < -0.3:
                    base += 5   # 가격 하락 + CVD 증가 (더 강한 매집 신호)
                return min(base, 20)
            return 0
        else:
            if cvd_1h < 0:
                if cvd_1h <= -100000: return 20
                elif cvd_1h <= -30000: return 17
                elif cvd_1h <= -5000: return 13
                return 7
            return 0
    except Exception:
        return 0

def score_ema_compression(ema20, ema60, ema120, direction):
    """
    EMA 압축도 점수(매집 15점 / 분산 15점). 매집은 세 EMA가 거의 겹쳐있는 상태를
    최고점으로 본다 — "정배열 완성"은 이미 매집이 끝나고 추세가 시작된 신호라 오히려
    감점한다(원안의 핵심 지적사항). 분산은 반대로 이격이 클수록(추세 과열) 만점.
    """
    spread = _ema_spread_pct(ema20, ema60, ema120)
    if spread is None:
        return 0
    aligned_up = ema20 > ema60 > ema120
    aligned_down = ema20 < ema60 < ema120
    if direction == 'prepump':
        if spread <= 0.3: return 15          # 거의 겹침 — 매집 최적 구간
        elif aligned_up and spread <= 1.0: return 12   # 약한 정배열 시작
        elif aligned_up: return 7             # 완전 정배열 — 이미 매집 끝난 상태
        elif aligned_down: return 0           # 역배열
        return 2                              # 과도한 이격(방향 불명)
    else:
        if spread >= 3.0: return 15           # 과도한 이격 — 분산/과열
        elif spread >= 1.5: return 10
        elif spread <= 0.3: return 2          # 압축 상태는 분산 신호로는 약함
        return 5

def score_atr_state(atr_pct, direction):
    """ATR(변동성) 점수(매집 10점 / 분산 10점). 매집은 적당히 낮은 변동성, 분산은 급증이 좋음."""
    try:
        if direction == 'prepump':
            if 1.0 <= atr_pct <= 2.0: return 10
            elif 0.5 <= atr_pct < 1.0: return 7
            elif 2.0 < atr_pct <= 3.0: return 5
            elif atr_pct > 3.0: return 0
            return 3   # 0.5% 미만 — 거의 죽어있음, 낮은 점수
        else:
            if atr_pct >= 4.0: return 10
            elif atr_pct >= 3.0: return 6
            return 0

    except Exception:
        return 0

def score_volz_state(vol_z, direction):
    """
    거래량(VolZ) 점수(매집 10점 / 분산 10점). 매집은 '적당한 증가'가 최고점 —
    너무 없으면 관심 밖, 너무 많으면 이미 분산 중이라는 원안을 반영.
    """
    try:
        if direction == 'prepump':
            if 0.5 <= vol_z <= 1.2: return 10
            elif 1.2 < vol_z <= 2.0: return 7
            elif 0.2 <= vol_z < 0.5: return 6
            elif 2.0 < vol_z <= 3.0: return 3
            elif vol_z > 3.0: return 0
            return 2   # 0.2 미만 — 관심도 자체가 없음
        else:
            if vol_z >= 3.0: return 10
            elif vol_z >= 2.0: return 5
            return 0
    except Exception:
        return 0

def score_box_position(current_price, box_high, box_low, direction):
    """
    가격 위치 점수(매집 10점 / 분산 10점). 최근 N캔들(기본 20개, '20일 박스' 근사)
    범위 안에서 지금 가격이 바닥권인지 상단권인지. 매집은 바닥권, 분산은 신고가 근처가 만점.
    """
    try:
        if box_high is None or box_low is None or box_high <= box_low:
            return 0
        pos_pct = (current_price - box_low) / (box_high - box_low) * 100
        if direction == 'prepump':
            if pos_pct <= 25: return 10
            elif pos_pct <= 50: return 8
            elif pos_pct <= 80: return 5
            return 0
        else:
            if pos_pct >= 80: return 10
            elif pos_pct >= 60: return 5
            return 0
    except Exception:
        return 0

def score_rsi_box(rsi, direction):
    """RSI 점수(매집 5점 / 분산 5점). 매집은 박스권 중립(45~60), 분산은 과매수(70+)가 만점."""
    try:
        if direction == 'prepump':
            if 45 <= rsi <= 60: return 5
            elif 40 <= rsi < 45: return 4
            elif 60 < rsi <= 70: return 3
            elif 30 <= rsi < 40: return 2
            return 0
        else:
            if rsi >= 70: return 5
            elif rsi >= 60: return 3
            return 0
    except Exception:
        return 0

def score_recent_move(recent_pct, direction):
    """
    최근 상승률 점수(매집: 급등 패널티 5점 / 분산: 급등 보너스 5점). recent_pct는
    최근 N캔들 전 대비 현재가 변화율(%, '최근 3일' 근사 — 자세한 건 함수 docstring 참고).
    매집은 이미 급등한 종목엔 감점(매집이 끝났을 가능성), 분산은 반대로 급등에 가점.
    """
    try:
        if direction == 'prepump':
            if recent_pct <= 3: return 5
            elif recent_pct <= 7: return 3
            elif recent_pct <= 10: return 2
            elif recent_pct <= 15: return 1
            return 0
        else:
            if recent_pct >= 15: return 5
            elif recent_pct >= 10: return 3
            return 0
    except Exception:
        return 0

def calculate_prepump_score(oi_change_pct, cvd_1h, ema20, ema60, ema120, atr_pct, vol_z,
                             current_price, box_high, box_low, rsi, recent_pct, chg_30m_pct=0.0):
    """매집 총점(0~100) = OI지속(25)+CVD누적(20)+EMA압축(15)+ATR(10)+VolZ(10)+가격위치(10)+RSI(5)+최근상승패널티(5)."""
    if not ENABLE_PREPUMP_SCORE:
        return 0
    try:
        score = (score_oi_persistence(oi_change_pct, 'prepump')
                 + score_cvd_cumulative(cvd_1h, 'prepump', chg_30m_pct)
                 + score_ema_compression(ema20, ema60, ema120, 'prepump')
                 + score_atr_state(atr_pct, 'prepump')
                 + score_volz_state(vol_z, 'prepump')
                 + score_box_position(current_price, box_high, box_low, 'prepump')
                 + score_rsi_box(rsi, 'prepump')
                 + score_recent_move(recent_pct, 'prepump'))
        return max(0, min(round(score), 100))
    except Exception:
        return 0

def calculate_preshort_score(oi_change_pct, cvd_1h, ema20, ema60, ema120, atr_pct, vol_z,
                              current_price, box_high, box_low, rsi, recent_pct, chg_30m_pct=0.0):
    """분산 총점(0~100) = OI감소·불일치(25)+CVD지속감소(20)+EMA과이격(15)+ATR급증(10)+VolZ폭발(10)+신고가부근(10)+RSI70+(5)+최근급등(5)."""
    if not ENABLE_PREPUMP_SCORE:
        return 0
    try:
        score = (score_oi_persistence(oi_change_pct, 'preshort')
                 + score_cvd_cumulative(cvd_1h, 'preshort', chg_30m_pct)
                 + score_ema_compression(ema20, ema60, ema120, 'preshort')
                 + score_atr_state(atr_pct, 'preshort')
                 + score_volz_state(vol_z, 'preshort')
                 + score_box_position(current_price, box_high, box_low, 'preshort')
                 + score_rsi_box(rsi, 'preshort')
                 + score_recent_move(recent_pct, 'preshort'))
        return max(0, min(round(score), 100))
    except Exception:
        return 0


# ── 동적 진입 컷오프 ─────────────────────────────────────────
# 점수 컷을 75로 고정하지 않고, 시장 상태(전 코인 평균 ATR%)에 따라 조정한다.
#   횡보장(평균 ATR% 낮음)  → 컷 상향 (신호 남발 방지)
#   추세장(평균 ATR% 높음)  → 컷 하향 (기회 포착)
current_min_score = MIN_SCORE  # score_updater가 매 사이클 갱신, GUI가 읽어서 색칠 기준으로 사용

def compute_dynamic_min_score(results):
    """결과 리스트의 평균 ATR%로 시장 상태를 추정해 진입 컷을 반환한다.
    (개편안 v2 권장 기준: 65 관심 / 70 추세장 진입 / 75 일반장 진입 / 80 횡보장)"""
    try:
        atrs = [r.get('atr_pct', 0) for r in results if r.get('atr_pct', 0) > 0]
        if not atrs:
            return MIN_SCORE
        avg = sum(atrs) / len(atrs)
        if avg >= 1.2:   # 강한 추세/변동장
            return 70
        elif avg <= 0.6:  # 횡보장
            return 80
        else:             # 보통
            return MIN_SCORE
    except Exception:
        return MIN_SCORE



# ============================================================
# 단일 코인 처리
# ============================================================
BITHUMB_CANDLESTICK_URL = "https://api.bithumb.com/public/candlestick/{}_{}/{}"
BITHUMB_NATIVE_INTERVALS = {"1h", "6h", "12h"}  # 빗썸 캔들스틱 API가 직접 지원하는 간격

def _fetch_candlestick_raw(order_currency, payment_currency="KRW", chart_intervals="1h", timeout=10, retries=2):
    """
    pybithumb.Bithumb.get_candlestick()는 내부 HTTP 타임아웃이 3초로 고정돼있고,
    실패 시 예외를 그냥 삼켜버려서(None 리턴) 원인 파악이 안 된다. 특히
    ThreadPoolExecutor로 여러 코인을 동시에 요청할 때(Termux 등 환경에서)
    TLS 핸드셰이크가 몰리면서 3초를 넘기는 간헐적 타임아웃이 자주 발생한다.
    그래서 같은 빗썸 캔들스틱 엔드포인트를 더 넉넉한 타임아웃(기본 10초)과
    재시도 로직으로 직접 호출하고, pybithumb과 동일한 형식의 DataFrame으로
    파싱해서 돌려준다. (빗썸이 직접 지원하는 간격: 1m/3m/5m/10m/30m/1h/6h/12h/24h)
    """
    url = BITHUMB_CANDLESTICK_URL.format(order_currency, payment_currency, chart_intervals)
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, timeout=timeout, verify=False)
            data = resp.json()
            if data.get('status') == '0000':
                rows = data.get('data')
                df = pd.DataFrame(rows, columns=['time', 'open', 'close', 'high', 'low', 'volume'])
                df = df.set_index('time')
                df = df[['open', 'high', 'low', 'close', 'volume']]
                df.index = pd.to_datetime(df.index, unit='ms', utc=True)
                # Termux 등 일부 환경엔 IANA tzdata가 없어 tz_convert('Asia/Seoul')가
                # 실패할 수 있다. 한국은 서머타임이 없는 고정 UTC+9이므로 이름 기반
                # 변환 대신 직접 9시간을 더해서 tzdata 의존성을 없앤다.
                df.index = df.index + pd.Timedelta(hours=9)
                df.index = df.index.tz_localize(None)
                return df.astype(float)
            return None
        except Exception as e:
            if attempt == retries:
                print(f"[{order_currency}] 캔들 조회 실패(재시도 {retries}회 소진): {e}")
                return None
            time.sleep(0.5)
    return None

def fetch_candlestick(order_currency, payment_currency="KRW", chart_intervals=None, timeout=10, retries=2):
    """
    계산 기준 캔들 간격을 감싸는 래퍼. 빗썸이 직접 지원하지 않는 "2h"는
    1시간봉을 2개씩 묶어(OHLCV 표준 리샘플) 합성한다. chart_intervals를
    안 주면 그 시점의 전역 CANDLE_INTERVAL(클라이언트 버튼으로 전환 가능)을 쓴다.
    """
    interval = chart_intervals or CANDLE_INTERVAL
    if interval in BITHUMB_NATIVE_INTERVALS or interval == "24h":
        return _fetch_candlestick_raw(order_currency, payment_currency, interval, timeout, retries)
    if interval == "2h":
        df = _fetch_candlestick_raw(order_currency, payment_currency, "1h", timeout, retries)
        if df is None or len(df) < 2:
            return df
        try:
            resampled = df.resample("2h").agg({
                "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
            })
            return resampled.dropna(how="any")
        except Exception as e:
            print(f"[{order_currency}] 2h 리샘플 실패, 1h로 폴백: {e}")
            return df
    # 알 수 없는 간격은 안전하게 1h로 폴백
    return _fetch_candlestick_raw(order_currency, payment_currency, "1h", timeout, retries)

def process_ticker(ticker):
    try:
        df = fetch_candlestick(ticker, chart_intervals=CANDLE_INTERVAL)
        if df is None or len(df) < 30:
            return None
        df = df.astype(float)
        df['RSI'] = calculate_rsi(df)
        df['BB_MIDDLE'] = df['close'].rolling(20).mean()
        df['BB_STD'] = df['close'].rolling(20).std()
        df['BB_UPPER'] = df['BB_MIDDLE'] + df['BB_STD'] * 2
        df['BB_LOWER'] = df['BB_MIDDLE'] - df['BB_STD'] * 2
        df['CVD'] = calculate_cvd(df)

        latest = df.iloc[-1]
        current_price = float(latest['close'])
        vz = calculate_vol_zscore(df)
        cvd_value = float(df['CVD'].iloc[-1])
        # 최근 거래량(윈도우 합) 대비 CVD 변화폭의 기준선 산정용
        vol_window_sum = float(df['volume'].iloc[-CVD_WINDOW_CANDLES:].sum())
        # CVD 추세: 직전 캔들 대비 CVD 변화량 (기준 캔들 = CANDLE_INTERVAL)
        cvd_diff = cvd_value - float(df['CVD'].iloc[-2]) if len(df) >= 2 else 0.0
        bb_percent = calculate_bb_percent(df, latest)
        extension_pct = calculate_extension_pct(df)
        atr_pct = calculate_atr_1h(df)
        # ATR%가 너무 낮으면(움직임이 죽어있는 코인) 목록에서 제외한다 (MIN_ATR_PCT=0이면 필터 끔)
        if MIN_ATR_PCT > 0 and atr_pct > 0 and atr_pct < MIN_ATR_PCT:
            return None
        # 단기(30분) 가격 변동률: 캔들 fetch 없이 price_updater가 모아둔 자체 히스토리 사용
        chg_30m = get_recent_pct_change(ticker, minutes=30)
        # OI 유형 판별용 방향(부호만 사용하므로 단위는 무관)
        price_chg = float(df['close'].iloc[-1]) - float(df['close'].iloc[-5])

        with data_lock:
            latest_prices[ticker] = current_price

        # 바이낸스 캐시에서 펀딩레이트 및 USD 가격(markPrice) 조회 (점수에는 더 이상 사용하지 않고 표시용)
        funding_rate = 0.0
        price_usd = None
        base = ticker.replace("KRW-", "").strip().upper()
        try:
            cache = get_all_funding_rates()
            entry = cache.get(base)
            if entry:
                funding_rate = entry.get('funding', 0.0) or 0.0
                price_usd = entry.get('mark_price')
        except:
            pass

        # 미체결약정(OI) 1시간 변동률 및 롱/숏 계정 비율 조회 (심볼별 개별 호출, 내부 캐시로 빈도 제한)
        oi_change_pct, oi_value = get_oi_change_1h(base)
        ls_ratio = get_long_short_ratio(base)
        # OI 절대 규모(명목가치 USD) = OI 수량 × USD 가격. 규모가 작으면 OI 점수 감액에 사용.
        oi_notional_usd = (oi_value * price_usd) if (oi_value and price_usd) else None

        # EMA 추세 (1시간봉 기준 EMA20/60/120 삼중 정배열)
        try:
            ema20 = float(df['close'].ewm(span=20, adjust=False).mean().iloc[-1])
            ema60 = float(df['close'].ewm(span=60, adjust=False).mean().iloc[-1])
            ema120 = float(df['close'].ewm(span=120, adjust=False).mean().iloc[-1]) if len(df) >= 30 else None
        except Exception:
            ema20 = ema60 = ema120 = None

        rsi_val = float(latest['RSI'])
        rsi_delta = rsi_val - float(df['RSI'].iloc[-5])

        # 24h 거래대금(원화, 백만원 단위) — 새 점수체계의 '거래대금 보너스'에 쓰이므로
        # 점수 계산 전에 먼저 구해둔다.
        vol_million = 0
        try:
            all_ticker = Bithumb.get_current_price("ALL")
            info = all_ticker.get(ticker)
            if isinstance(info, dict):
                acc = info.get('acc_trade_value_24H') or info.get('acc_trade_price_24h') or 0
                vol_million = round(float(acc) / 1_000_000)
        except:
            pass
        # 위 24h 거래대금은 원화 기준이라, 달러 점수 구간과 비교하려면 환산이 필요하다.
        # 정식 원/달러 환율 API를 새로 붙이는 대신, 이미 갖고 있는 같은 코인의
        # 원화가(price)와 달러가(price_usd) 비율을 원/달러 환율의 근사치로 사용한다.
        trade_value_usd = None
        try:
            if price_usd and price_usd > 0 and current_price > 0:
                fx_rate = current_price / price_usd  # 원/달러 근사
                trade_value_usd = (vol_million * 1_000_000) / fx_rate
        except Exception:
            trade_value_usd = None

        long_score = calculate_long_score(
            rsi_val, bb_percent, cvd_diff, vol_window_sum, ls_ratio, oi_change_pct, chg_30m,
            price_chg, extension_pct, vz, rsi_delta, atr_pct, ema20, ema60, oi_notional_usd,
            funding_rate, trade_value_usd, current_price, ema120, vol_million
        )
        short_score = calculate_short_score(
            rsi_val, bb_percent, cvd_diff, vol_window_sum, ls_ratio, oi_change_pct, chg_30m,
            price_chg, extension_pct, vz, rsi_delta, atr_pct, ema20, ema60, oi_notional_usd,
            funding_rate, trade_value_usd, current_price, ema120, vol_million
        )
        # Pre-Pump/Pre-Short (매집/분산 v3 — 장기 매집 사이클 탐지). cvd_1h는 별도 API가
        # 없어 위에서 이미 구한 cvd_diff(최근 CVD_WINDOW_CANDLES 캔들 변화량)를 근사치로
        # 재사용한다.
        try:
            box_lookback = df.iloc[-20:] if len(df) >= 20 else df
            box_high = float(box_lookback['high'].max())
            box_low = float(box_lookback['low'].min())
        except Exception:
            box_high = box_low = None
        try:
            # "최근 3일" 근사 — 기준봉(CANDLE_INTERVAL)에 따라 캔들 개수를 다르게 잡는다.
            candles_per_3d = {"1h": 72, "2h": 36, "6h": 12, "12h": 6}.get(CANDLE_INTERVAL, 72)
            ref_idx = -min(candles_per_3d, len(df) - 1) if len(df) > 1 else -1
            ref_price = float(df['close'].iloc[ref_idx])
            recent_pct = (current_price - ref_price) / ref_price * 100 if ref_price > 0 else 0.0
        except Exception:
            recent_pct = 0.0
        prepump_score = calculate_prepump_score(oi_change_pct, cvd_diff, ema20, ema60, ema120,
                                                 atr_pct, vz, current_price, box_high, box_low,
                                                 rsi_val, recent_pct, chg_30m)
        preshort_score = calculate_preshort_score(oi_change_pct, cvd_diff, ema20, ema60, ema120,
                                                   atr_pct, vz, current_price, box_high, box_low,
                                                   rsi_val, recent_pct, chg_30m)
        # 항목별 세부점수 (로그 분석/배점 튜닝용 — 총점과 동일한 함수로 계산, 105점 원점수 기준)
        components = {
            "ema_l": score_ema_trend(current_price, ema20, ema60, ema120, 'long'),
            "ema_s": score_ema_trend(current_price, ema20, ema60, ema120, 'short'),
            "pp_l": score_price_position_long(rsi_val, bb_percent),
            "pp_s": score_price_position_short(rsi_val, bb_percent),
            "cvd_l": score_cvd_trend(cvd_diff, vol_window_sum, 'long'),
            "cvd_s": score_cvd_trend(cvd_diff, vol_window_sum, 'short'),
            "oi_sc": score_oi_v3(oi_change_pct),
            "m30_l": score_chg30m_long(chg_30m),
            "m30_s": score_chg30m_short(chg_30m),
            "volz_sc": score_volz_v3(vz),
            "liquidity_sc": score_liquidity_filter(atr_pct, vol_million),
        }

        return {
            "ticker": ticker,
            "price": current_price,
            "price_usd": price_usd,
            "long_score": int(long_score),
            "short_score": int(short_score),
            "prepump_score": int(prepump_score),
            "preshort_score": int(preshort_score),
            "rsi": round(float(latest['RSI']), 1),
            "rsi_delta": round(rsi_delta, 1),
            "vol_z": round(vz, 1),
            "bb_percent": round(bb_percent, 1),
            "cvd": round(cvd_value, 2),
            "cvd_diff": round(cvd_diff, 2),  # prepump/preshort 스코어링에 쓰는 cvd_1h 근사치 (누적값 아니라 변화량)
            "funding": funding_rate,
            "vol_24h_m": vol_million,
            "trade_value_usd": trade_value_usd,
            "atr_pct": atr_pct,
            "oi_change_pct": oi_change_pct,
            "oi_value": oi_value,
            "ls_ratio": ls_ratio,
            "chg_30m": chg_30m,
            "ema20": round(ema20, 8) if ema20 is not None else None,
            "ema60": round(ema60, 8) if ema60 is not None else None,
            "ema120": round(ema120, 8) if ema120 is not None else None,
            "box_high": round(box_high, 8) if box_high is not None else None,
            "box_low": round(box_low, 8) if box_low is not None else None,
            "recent_pct": round(recent_pct, 3),
            "extension_pct": round(extension_pct, 3) if extension_pct is not None else None,
            "components": components,
        }
    except Exception as e:
        print(f"[{ticker}] 데이터 수집 실패: {e}")
        return None


# ============================================================
# 업데이터
# ============================================================
def price_updater(tickers_ref):
    global score_cache
    while running:
        try:
            price_data = Bithumb.get_current_price("ALL")
            updates = {}
            for ticker, info in price_data.items():
                if ticker == "date": continue
                try:
                    if isinstance(info, dict):
                        updates[ticker] = float(info.get('closing_price') or info.get('trade_price') or 0)
                    else:
                        updates[ticker] = float(info)
                except:
                    updates[ticker] = 0.0
            with data_lock:
                latest_prices.update(updates)
            # 바이낸스 USD 체결가 갱신 (거래는 전부 USD 기준)
            try:
                cache = get_all_funding_rates()
                usd_updates = {}
                for ticker in updates:
                    base = ticker.replace("KRW-", "").strip().upper()
                    entry = cache.get(base)
                    if entry and entry.get('mark_price'):
                        usd_updates[ticker] = float(entry['mark_price'])
                with data_lock:
                    latest_prices_usd.update(usd_updates)
            except Exception:
                pass
            record_price_history(updates)
            with score_lock:
                if score_cache:
                    merged = [dict(row, price=latest_prices.get(t, row.get('price', 0))) for t, row in score_cache.items()]
                    merged.sort(key=lambda x: x.get('long_score', 0) + x.get('short_score', 0), reverse=True)
                    price_queue.put(merged)
        except Exception as e:
            print(f"가격 업데이트 실패: {e}")
        time.sleep(PRICE_INTERVAL)

def score_updater(tickers_ref):
    global score_cache
    # 첫 실행 전 펀딩레이트 미리 로드
    try:
        get_all_funding_rates()
    except:
        pass
    while running:
        tickers = tickers_ref[0]
        if not tickers:
            time.sleep(2)
            continue
        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(process_ticker, t): t for t in tickers}
            for future in as_completed(futures):
                if not running: break
                res = future.result()
                if res:
                    results.append(res)
        if results:
            global current_min_score
            current_min_score = compute_dynamic_min_score(results)
            results.sort(key=lambda x: x['long_score'] + x['short_score'], reverse=True)
            with score_lock:
                score_cache.clear()
                for r in results:
                    score_cache[r['ticker']] = r
            data_queue.put(results)
            try:
                check_discord_alerts(results, current_min_score)
            except Exception as e:
                print(f"디스코드 알림 체크 실패: {e}")
        time.sleep(SCORE_INTERVAL)

def ticker_updater(tickers_ref):
    while running:
        try:
            price_data = Bithumb.get_current_price("ALL")
            tickers = [k for k in list(price_data.keys()) if k != "date"][:TOP_COIN_COUNT]
            tickers_ref[0] = tickers
        except:
            if not tickers_ref[0]:
                tickers_ref[0] = ["BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "TRX", "AVAX", "LINK", "BNB"]
        time.sleep(60)

# ============================================================
# 서버 <-> 주문용(GUI) CSV 인터페이스
#   server_market.csv   : 코인별 최신 지표/점수 스냅샷 (서버가 2초마다 원자적 갱신)
#   server_account.csv  : 잔고/포지션/실시간 손익 스냅샷 (서버가 1초마다 갱신)
#   server_cmds/        : 주문용이 명령 파일(cmd_*.csv)을 떨어뜨리는 폴더
#   server_results.csv  : 명령 처리 결과 (cmd_id, status, message) 누적
# 계좌(잔고/포지션/강제청산)는 전부 서버가 관리한다. 주문용은 명령만 보낸다.
# ============================================================
MARKET_SNAPSHOT = os.path.join(SCRIPT_DIR, "server_market.csv")
ACCOUNT_SNAPSHOT = os.path.join(SCRIPT_DIR, "server_account.csv")
CMD_DIR = os.path.join(SCRIPT_DIR, "server_cmds")
RESULTS_FILE = os.path.join(SCRIPT_DIR, "server_results.csv")
REPORT_SAVE_DIR = SCRIPT_DIR  # PDF 리포트 저장 경로. 기본값은 SCRIPT_DIR(안드로이드 공용 문서함).
                               # srv_set_report_dir()로 실행 중에도 바꿀 수 있다.
os.makedirs(CMD_DIR, exist_ok=True)

def _atomic_write_csv(path, rows):
    """쓰다 만 파일을 클라이언트가 읽는 사고를 막기 위해 tmp에 쓰고 교체한다."""
    tmp = path + ".tmp"
    with open(tmp, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow(r)
    os.replace(tmp, path)

MARKET_COLS = ['ticker', 'price', 'price_usd', 'long_score', 'short_score',
               'prepump_score', 'preshort_score',
               'rsi', 'rsi_delta', 'vol_z', 'bb_percent', 'cvd', 'cvd_diff', 'funding',
               'vol_24h_m', 'atr_pct', 'oi_change_pct', 'chg_30m', 'ls_ratio',
               'ema20', 'ema60',
               'min_cut', 'watch_cut', 'pp_min_cut', 'interval', 'score_time', 'price_time']

_last_score_time = [""]

def write_market_snapshot():
    with score_lock:
        snap = {t: dict(r) for t, r in score_cache.items()}
    if not snap:
        return
    with data_lock:
        prices = dict(latest_prices)
    pt = datetime.now().strftime('%H:%M:%S')
    rows = [MARKET_COLS]
    for t, r in snap.items():
        rows.append([
            t, prices.get(t, r.get('price', 0)),
            r.get('price_usd', '') if r.get('price_usd') is not None else '',
            r.get('long_score', 0), r.get('short_score', 0),
            r.get('prepump_score', 0), r.get('preshort_score', 0),
            r.get('rsi', 0), r.get('rsi_delta', 0), r.get('vol_z', 0),
            r.get('bb_percent', 0), r.get('cvd', 0), r.get('cvd_diff', 0), r.get('funding', 0),
            r.get('vol_24h_m', 0), r.get('atr_pct', 0), r.get('oi_change_pct', 0),
            r.get('chg_30m', 0),
            r.get('ls_ratio', '') if r.get('ls_ratio') is not None else '',
            r.get('ema20', '') if r.get('ema20') is not None else '',
            r.get('ema60', '') if r.get('ema60') is not None else '',
            current_min_score, WATCH_MIN_SCORE, PREPUMP_MIN_SCORE, CANDLE_INTERVAL, _last_score_time[0], pt,
        ])
    try:
        _atomic_write_csv(MARKET_SNAPSHOT, rows)
    except Exception as e:
        print(f"마켓 스냅샷 저장 실패: {e}")

def write_account_snapshot():
    with data_lock:
        pos_snap = {t: dict(p) for t, p in positions.items()}
        prices = dict(latest_prices_usd)
    fng = get_fear_greed_index()
    rows = [['balance', round(balance, 2)],
            ['ts', datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
            ['margin_mode', 'cross' if CROSS_MARGIN_MODE else 'isolated'],
            ['bank_balance', round(bank_balance, 2)],
            ['bank_total_deposit', round(bank_total_deposit, 2)],
            ['bank_total_spent', round(bank_total_spent, 2)],
            ['fng_value', fng.get('value') if fng.get('value') is not None else ''],
            ['fng_class', fng.get('classification', '')],
            ['positions', 'entry_price', 'amount', 'leverage', 'type',
             'entry_fee', 'entry_time', 'current_price', 'pnl', 'pnl_rate_pct', 'entry_score']]
    for t, pos in pos_snap.items():
        entry = pos.get('entry_price', 0)
        cur = prices.get(t, entry)
        ptype = pos.get('position_type', 'long')
        amt = pos.get('amount', 0); lev = pos.get('leverage', 1)
        if entry <= 0: continue
        rate = (cur - entry) / entry if ptype == 'long' else (entry - cur) / entry
        pnl = rate * amt * lev
        et = pos.get('entry_time', '')
        et_str = et.strftime('%Y-%m-%d %H:%M:%S') if isinstance(et, datetime) else str(et)
        rows.append([t, entry, amt, lev, ptype, pos.get('entry_fee', 0), et_str,
                     cur, round(pnl, 2), round(rate * lev * 100, 2), pos.get('entry_score', 0)])
    try:
        _atomic_write_csv(ACCOUNT_SNAPSHOT, rows)
    except Exception as e:
        print(f"계좌 스냅샷 저장 실패: {e}")

def append_result(cmd_id, status, message):
    try:
        exists = os.path.exists(RESULTS_FILE)
        with open(RESULTS_FILE, 'a', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(['cmd_id', 'status', 'message', 'ts'])
            w.writerow([cmd_id, status, message, datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
    except Exception as e:
        print(f"결과 기록 실패: {e}")

# ── 서버측 계좌 조작 (GUI에 있던 로직 이관) ──────────────────
def srv_charge(amount):
    """거래소 충전 = 외부통장 → 거래소로 이체. 외부통장 잔액이 있어야 한다."""
    global balance, bank_balance
    if amount <= 0:
        return False, "금액이 올바르지 않습니다"
    if amount > bank_balance:
        return False, f"외부통장 잔액이 부족합니다 (현재 외부통장 ${bank_balance:,.2f}, 먼저 외부통장 입금 필요)"
    bank_balance -= amount
    balance += amount
    save_to_csv()
    return True, f"거래소로 ${amount:,.2f} 충전 완료 (거래소 잔고 ${balance:,.2f} / 외부통장 ${bank_balance:,.2f})"

def srv_withdraw(amount):
    """
    거래소 출금 = 거래소 → 외부통장으로 이체. balance는 이미 포지션에 배정된 증거금과
    완전히 분리된 '남는 현금'이라, 포지션이나 그 포지션의 미실현손익은 전혀 건드리지 않는다.
    (전에는 그냥 사라졌지만, 이제는 사라지지 않고 외부통장으로 들어간다.)
    """
    global balance, bank_balance
    if amount <= 0:
        return False, "금액이 올바르지 않습니다"
    if amount > balance:
        return False, f"출금 가능 금액을 초과했습니다 (현재 여유 현금 ${balance:,.2f})"
    balance -= amount
    bank_balance += amount
    save_to_csv()
    return True, f"외부통장으로 ${amount:,.2f} 출금 완료 (거래소 잔고 ${balance:,.2f} / 외부통장 ${bank_balance:,.2f})"

def srv_bank_deposit(amount):
    """외부통장 입금 — 시스템 밖에서 새로 들어오는 돈(월급 등). 출처는 안 따진다."""
    global bank_balance, bank_total_deposit
    if amount <= 0:
        return False, "금액이 올바르지 않습니다"
    bank_balance += amount
    bank_total_deposit += amount
    save_to_csv()
    return True, f"외부통장에 ${amount:,.2f} 입금 완료 (외부통장 잔액 ${bank_balance:,.2f})"

def srv_bank_withdraw(amount):
    """외부통장 출금 — 실생활 지출 등으로 시스템 밖으로 빠져나가는 돈. 그냥 사라진다(어디로도 안 감)."""
    global bank_balance, bank_total_spent
    if amount <= 0:
        return False, "금액이 올바르지 않습니다"
    if amount > bank_balance:
        return False, f"외부통장 잔액을 초과했습니다 (현재 외부통장 ${bank_balance:,.2f})"
    bank_balance -= amount
    bank_total_spent += amount
    save_to_csv()
    return True, f"외부통장에서 ${amount:,.2f} 출금 완료 (외부통장 잔액 ${bank_balance:,.2f})"

def srv_reset_balance():
    """거래소 잔고뿐 아니라 외부통장(잔액+누적입금+누적출금)까지 전부 $0으로 리셋한다.
    거래소만 리셋하고 외부통장 누적입금은 안 지우면, '이미 거래소로 넘긴 돈'이
    허공으로 사라진 것처럼 순수익이 끝없이 마이너스로 튀는 문제가 있었다."""
    global balance, bank_balance, bank_total_deposit, bank_total_spent
    if positions:
        return False, f"보유 포지션이 있어 리셋할 수 없습니다 ({len(positions)}개 청산 후 시도)"
    balance = 0
    bank_balance = 0
    bank_total_deposit = 0
    bank_total_spent = 0
    save_to_csv()
    return True, "거래소 잔고와 외부통장을 모두 $0 으로 리셋했습니다"

def srv_set_interval(interval):
    """
    계산 기준 캔들 간격을 전환한다 (1h/2h/6h/12h). 클라이언트의 타임프레임
    버튼이 여기로 명령을 보낸다. 즉시 반영되며, 다음 score_updater 사이클
    (SCORE_INTERVAL 초 이내)부터 새 간격으로 계산된 지표/점수가 나온다.
    """
    global CANDLE_INTERVAL
    interval = (interval or "").strip().lower()
    if interval not in ALLOWED_INTERVALS:
        return False, f"지원하지 않는 간격: {interval} (허용: {', '.join(ALLOWED_INTERVALS)})"
    if interval == CANDLE_INTERVAL:
        return True, f"이미 {interval} 기준입니다"
    CANDLE_INTERVAL = interval
    return True, f"계산 기준을 {interval}로 전환했습니다 (다음 갱신부터 반영)"

def srv_set_margin_mode(mode):
    """
    마진 모드를 전환한다 (cross/isolated). 클라이언트 버튼이 여기로 명령을 보낸다.
      cross(크로스)   : 포지션 손실이 -100%를 넘어도 계좌 총자산이 버티면 청산 안 함
      isolated(격리)  : 포지션별로 배정 증거금의 90% 손실 시 그 포지션만 즉시 강제청산
    보유 포지션이 있어도 전환은 즉시 되고, 다음 강제청산 감시 사이클(1초)부터 새 방식 적용.
    """
    global CROSS_MARGIN_MODE
    mode = (mode or "").strip().lower()
    if mode not in ("cross", "isolated"):
        return False, f"지원하지 않는 마진 모드: {mode} (허용: cross, isolated)"
    new_val = (mode == "cross")
    if new_val == CROSS_MARGIN_MODE:
        return True, f"이미 {mode} 모드입니다"
    CROSS_MARGIN_MODE = new_val
    label = "크로스 마진" if new_val else "격리 마진"
    return True, f"{label} 모드로 전환했습니다"

# ============================================================
# 지표 리포트 PDF — 현재 score_cache 스냅샷 기준 Long/Short Top10 + 전체 지표 설명.
# fpdf2 필요 (pip install fpdf2). 영문/Helvetica만 사용 — fpdf2 기본 폰트는 한글을
# 지원 안 해서, 한글을 쓰려면 별도 TTF를 add_font로 넣어야 하는데 그러면 서버
# 기기마다 폰트 파일을 챙겨야 하는 번거로움이 생겨서 이번에도 영문으로 유지했다.
# ============================================================

def _report_fmt_price(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "N/A"
    if v == 0:
        return "N/A"
    return f"{v:,.4f}" if abs(v) < 1 else f"{v:,.2f}"

def _report_fmt_num(v, decimals=1, signed=True):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "N/A"
    fmt = f"{{:+,.{decimals}f}}" if signed else f"{{:,.{decimals}f}}"
    return fmt.format(v)

_REPORT_INDICATORS = [
    ("Price(USD)", lambda r: _report_fmt_price(r.get('price_usd'))),
    ("RSI", lambda r: _report_fmt_num(r.get('rsi', 0), 1, False)),
    ("RSI Delta", lambda r: _report_fmt_num(r.get('rsi_delta', 0), 1)),
    ("VolZ", lambda r: _report_fmt_num(r.get('vol_z', 0), 1)),
    ("BB%", lambda r: _report_fmt_num(r.get('bb_percent', 0), 0, False)),
    ("CVD(level)", lambda r: _report_fmt_num(r.get('cvd', 0), 2)),
    ("CVD Diff", lambda r: _report_fmt_num(r.get('cvd_diff', 0), 2)),
    ("ATR%", lambda r: _report_fmt_num(r.get('atr_pct', 0), 2, False)),
    ("OI Delta%", lambda r: _report_fmt_num(r.get('oi_change_pct', 0), 2)),
    ("30m Delta%", lambda r: _report_fmt_num(r.get('chg_30m', 0), 2)),
    ("L/S", lambda r: f"{r['ls_ratio']:.2f}" if r.get('ls_ratio') else "N/A"),
    ("Ext%", lambda r: _report_fmt_num(r.get('extension_pct', 0), 2)),
    ("Funding%", lambda r: _report_fmt_num(r.get('funding', 0), 3)),
    ("24h Volume(M)", lambda r: f"{r.get('vol_24h_m', 0):,.0f}"),
    ("EMA20", lambda r: _report_fmt_price(r.get('ema20'))),
    ("EMA60", lambda r: _report_fmt_price(r.get('ema60'))),
    ("EMA120", lambda r: _report_fmt_price(r.get('ema120'))),
    ("ema_l", lambda r: str(r.get('components', {}).get('ema_l', 0))),
    ("ema_s", lambda r: str(r.get('components', {}).get('ema_s', 0))),
    ("pp_l", lambda r: str(r.get('components', {}).get('pp_l', 0))),
    ("pp_s", lambda r: str(r.get('components', {}).get('pp_s', 0))),
    ("cvd_l", lambda r: str(r.get('components', {}).get('cvd_l', 0))),
    ("cvd_s", lambda r: str(r.get('components', {}).get('cvd_s', 0))),
    ("oi_sc", lambda r: str(r.get('components', {}).get('oi_sc', 0))),
    ("m30_l", lambda r: str(r.get('components', {}).get('m30_l', 0))),
    ("m30_s", lambda r: str(r.get('components', {}).get('m30_s', 0))),
    ("volz_sc", lambda r: str(r.get('components', {}).get('volz_sc', 0))),
    ("liquidity_sc", lambda r: str(r.get('components', {}).get('liquidity_sc', 0))),
]

_REPORT_DESCRIPTIONS = [
    ("Price(USD)", "Binance futures last traded price (USD). N/A if not listed on Binance."),
    ("RSI", "Relative Strength Index (0-100). 70+ overbought, 30- oversold."),
    ("RSI Delta", "RSI change vs. 5 candles ago. A large value means a sharp move just happened."),
    ("VolZ", "Volume Z-score vs. the last 20-candle average. Higher means volume has already spiked."),
    ("BB%", "Price position within Bollinger Bands (0-100%). 0=lower band, 100=upper band."),
    ("CVD(level)", "Cumulative Volume Delta, running total (OHLCV-estimated). + means net buy pressure so far."),
    ("CVD Diff", "CVD change over the last CVD_WINDOW_CANDLES candles. Used as the 'cvd_1h' proxy in scoring."),
    ("ATR%", "Average True Range on the current base candle (% of price). Feeds the liquidity filter."),
    ("OI Delta%", "Open Interest change rate over the last base candle. Used raw (no direction gating) in v3."),
    ("30m Delta%", "Price change over the last 30 minutes, independent of the base candle interval."),
    ("L/S", "Binance-wide account long/short ratio. Display only in Long/Short Score v3 (used in the accumulation/distribution scoring instead)."),
    ("Ext%", "How far price has already moved over the last 10 candles (%). Not used directly in v3 scoring, shown for reference."),
    ("Funding%", "Funding rate (%). Display only - not used in any score as of v3."),
    ("24h Volume(M)", "24h cumulative trading value (millions KRW). Feeds the liquidity filter."),
    ("EMA20/60/120", "Exponential moving averages on the base candle. EMA20>60>120 = uptrend alignment."),
    ("ema_l / ema_s", "Long/Short Score component (max 20): EMA20/60/120 triple-alignment check. "
                       "Full marks = perfect alignment + price above EMA20, partial = pullback zone, 0 = reverse alignment."),
    ("pp_l / pp_s", "Long/Short Score component (max 20): combined RSI+BB% price-position condition."),
    ("cvd_l / cvd_s", "Long/Short Score component (max 15): CVD direction match (full/partial/none)."),
    ("oi_sc", "Long/Short Score component (max 15). Raw OI 1h change-rate tier, direction-agnostic in v3 - "
              "the same value is added to both the long and short totals (no price-direction gating)."),
    ("m30_l / m30_s", "Long/Short Score component (max 15): 30-minute momentum tier (healthy range scores highest, overheated range is penalized)."),
    ("volz_sc", "Long/Short Score component (max 15, shared by long/short): VolZ tier."),
    ("liquidity_sc", "Long/Short Score component (max 5, shared): ATR + 24h volume liquidity pass/fail filter."),
]

def _wrap_text_to_lines(text, max_chars):
    """
    직접 줄바꿈 계산. multi_cell()의 자동 줄바꿈이 일부 fpdf2 버전/환경에서
    글자 몇 개만 그리고 멈추는 버그가 확인돼서, 그 기능 자체를 안 쓰고
    파이썬에서 미리 줄을 나눈 뒤 cell()로 한 줄씩 그린다.
    """
    words = text.split()
    lines = []
    cur = ""
    for w in words:
        candidate = (cur + " " + w).strip()
        if len(candidate) <= max_chars:
            cur = candidate
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]

def _report_draw_table(pdf, rows, title):
    full_w = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.set_font('Helvetica', 'B', 14)
    pdf.cell(full_w, 8, title, ln=1)
    pdf.ln(1)
    if not rows:
        pdf.set_font('Helvetica', '', 10)
        pdf.cell(full_w, 6, "(no data)", ln=1)
        return
    n_cols = len(rows)
    label_w = 32
    col_w = max((full_w - label_w) / n_cols, 18)
    row_h = 5

    pdf.set_font('Helvetica', 'B', 8)
    pdf.set_x(pdf.l_margin)
    pdf.cell(label_w, row_h, "Indicator", border=1)
    for r in rows:
        txt = str(r.get('ticker', ''))[:10]
        pdf.cell(col_w, row_h, txt, border=1, align='C')
    pdf.ln(row_h)

    pdf.set_font('Helvetica', '', 7)
    for label, fn in _REPORT_INDICATORS:
        pdf.set_x(pdf.l_margin)
        pdf.cell(label_w, row_h, label, border=1)
        for r in rows:
            try:
                val = str(fn(r))
            except Exception:
                val = "N/A"
            pdf.cell(col_w, row_h, val[:16], border=1, align='C')
        pdf.ln(row_h)

def srv_generate_report(save_dir=None):
    """
    서버가 들고 있는 현재 스코어 스냅샷으로 지표 리포트(PDF)를 만든다.
    롱/숏 컷으로 Top10씩 뽑던 예전 방식 대신, 바이낸스에 상장된 코인 전부를 티커 이름순으로
    정렬해서 10개씩 나눠 페이지를 채운다(사전 랭킹/필터링 없음 — Grok 등 후속 분석이
    점수에 의해 이미 걸러지지 않은 원본을 보게 하려는 목적).
    save_dir을 안 주면 REPORT_SAVE_DIR(기본: 안드로이드 공용 문서함)에 저장한다.
    각 섹션(표 페이지들/설명)을 개별 try/except로 감싸서, 한 섹션에서 에러가 나도 나머지는
    최대한 정상적으로 만들어 저장한다(전부 날리지 않음).
    """
    try:
        from fpdf import FPDF
    except ImportError:
        return False, "fpdf2가 설치되어 있지 않습니다 (pip install fpdf2)"
    with score_lock:
        snap = {t: dict(r) for t, r in score_cache.items()}
    if not snap:
        return False, "아직 점수 데이터가 없습니다 (서버가 막 켜졌으면 잠시 후 다시 시도)"

    # 바이낸스 상장된 코인만(price_usd 있음), 티커 이름순 정렬
    rows = [r for r in snap.values() if r.get('price_usd')]
    rows.sort(key=lambda r: r.get('ticker', ''))
    if not rows:
        return False, "바이낸스 상장 코인 중 점수 데이터가 없습니다"

    PAGE_SIZE = 10
    pages = [rows[i:i + PAGE_SIZE] for i in range(0, len(rows), PAGE_SIZE)]
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    out_dir = save_dir or REPORT_SAVE_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        return False, f"저장 경로를 만들 수 없습니다: {out_dir} ({e})"

    try:
        pdf = FPDF(orientation='L', unit='mm', format='A3')
        pdf.set_margins(10, 10, 10)
        pdf.set_auto_page_break(True, margin=10)
        warnings = []

        for i, page_rows in enumerate(pages, start=1):
            try:
                pdf.add_page()
                _report_draw_table(pdf, page_rows,
                                    f"Indicator Report - {i}/{len(pages)} ({ts}, alphabetical, Binance-listed only)")
            except Exception as e:
                warnings.append(f"{i}페이지 실패: {e}")
                print(f"⚠️ 리포트 {i}페이지 생성 실패(건너뜀): {e}")

        try:
            full_w = pdf.w - pdf.l_margin - pdf.r_margin
            pdf.add_page()
            pdf.set_font('Helvetica', 'B', 14)
            pdf.cell(full_w, 8, f"Indicator Descriptions ({len(_REPORT_DESCRIPTIONS)} items)", ln=1)
            pdf.ln(2)
            skipped_items = 0
            # 9pt Helvetica 기준 400mm 너비에 대략 165자 정도 들어간다. 여유 있게 140자로 줄바꿈.
            wrap_chars = 140
            for name, desc in _REPORT_DESCRIPTIONS:
                try:
                    pdf.set_x(pdf.l_margin)
                    pdf.set_font('Helvetica', 'B', 10)
                    pdf.cell(full_w, 5, f"- {name}", ln=1)
                    pdf.set_font('Helvetica', '', 9)
                    for line in _wrap_text_to_lines(desc, wrap_chars):
                        pdf.set_x(pdf.l_margin)
                        pdf.cell(full_w, 5, line, ln=1)
                    pdf.ln(1)
                except Exception as e:
                    skipped_items += 1
                    print(f"⚠️ 리포트 설명 항목 '{name}' 건너뜀: {e}")
            if skipped_items:
                warnings.append(f"설명 {skipped_items}/{len(_REPORT_DESCRIPTIONS)}개 항목 건너뜀")
        except Exception as e:
            warnings.append(f"설명 페이지 전체 실패: {e}")
            print(f"⚠️ 리포트 설명 페이지 생성 실패(건너뜀): {e}")

        fname = f"indicator_report_{datetime.now():%Y%m%d_%H%M%S}.pdf"
        path = os.path.join(out_dir, fname)
        pdf.output(path)
        if warnings:
            return True, f"리포트 생성 완료(일부 누락): {path}\n" + " / ".join(warnings)
        return True, f"리포트 생성 완료: {path}"
    except Exception as e:
        return False, f"리포트 생성 실패: {e}"

def srv_set_report_dir(new_dir):
    """PDF 리포트 저장 경로를 바꾼다. 실제로 쓸 수 있는 폴더인지(생성+쓰기 테스트) 확인 후 반영."""
    global REPORT_SAVE_DIR
    new_dir = (new_dir or "").strip()
    if not new_dir:
        return False, "경로가 비어있습니다"
    try:
        os.makedirs(new_dir, exist_ok=True)
        test_path = os.path.join(new_dir, ".write_test")
        with open(test_path, 'w') as f:
            f.write("ok")
        os.remove(test_path)
    except Exception as e:
        return False, f"이 경로에 쓸 수 없습니다: {new_dir} ({e})"
    REPORT_SAVE_DIR = new_dir
    return True, f"리포트 저장 경로를 변경했습니다: {new_dir}"

def srv_get_candles(ticker, interval=None):
    """
    포지션 카드에서 티커 이름을 클릭하면 뜨는 차트 팝업용 데이터를 만든다.
    클라이언트에 matplotlib 같은 무거운 그래픽 라이브러리를 안 얹으려고(Termux에서
    계속 패키지 설치로 고생했던 걸 감안), 서버가 캔들+지표 원본을 CSV로만 내려주고
    실제 그리기는 클라이언트가 Tkinter Canvas로 직접 한다.
    RSI/RSI Delta/EMA20·60·120/볼린저밴드를 같이 계산해서 넣어준다.
    """
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return False, "티커 없음"
    interval = interval if interval in ALLOWED_INTERVALS else CANDLE_INTERVAL
    try:
        df = fetch_candlestick(ticker, chart_intervals=interval)
    except Exception as e:
        return False, f"캔들 조회 실패: {e}"
    if df is None or len(df) == 0:
        return False, f"{ticker} 캔들 데이터를 못 가져왔습니다"
    try:
        df['RSI'] = calculate_rsi(df)
        df['RSI_Delta'] = df['RSI'].diff()
        df['EMA20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['EMA60'] = df['close'].ewm(span=60, adjust=False).mean()
        df['EMA120'] = df['close'].ewm(span=120, adjust=False).mean() if len(df) >= 30 else np.nan
        bb_mid = df['close'].rolling(20).mean()
        bb_std = df['close'].rolling(20).std()
        df['BB_UPPER'] = bb_mid + bb_std * 2
        df['BB_MID'] = bb_mid
        df['BB_LOWER'] = bb_mid - bb_std * 2
    except Exception as e:
        return False, f"지표 계산 실패: {e}"
    path = os.path.join(SCRIPT_DIR, f"chart_{ticker}.csv")
    cols = ['open', 'high', 'low', 'close', 'RSI', 'RSI_Delta',
            'EMA20', 'EMA60', 'EMA120', 'BB_UPPER', 'BB_MID', 'BB_LOWER']
    try:
        out_df = df.tail(150)[cols].copy()
        out_df.index.name = 'timestamp'  # 클라이언트가 x축 날짜 라벨(mm.dd)에 쓸 수 있게 이름 고정
        out_df.to_csv(path)
    except Exception as e:
        return False, f"차트 데이터 저장 실패: {e}"
    return True, f"차트 데이터 준비 완료 ({interval}): {path}"

def srv_open(ticker, position_type, amount_won, leverage):
    global balance
    if not ticker: return False, "티커 없음"
    existing = positions.get(ticker)
    if existing and existing.get('position_type') != position_type:
        return False, "반대 방향 포지션이 이미 있습니다 (먼저 청산 후 진입하세요)"
    if amount_won <= 0: return False, "금액이 올바르지 않습니다"
    if leverage <= 0: leverage = DEFAULT_LEVERAGE
    entry_fee = (amount_won * leverage) * FEE_RATE
    total_cost = amount_won + entry_fee
    if balance < total_cost:
        return False, f"잔고 부족 (현재 ${balance:,.2f})"
    if CROSS_MARGIN_MODE:
        # 진입 직후 남는 여유현금이 유지증거금(전체 포지션 명목가치×비율)보다 적으면
        # 가격이 1도 안 움직여도(수수료/슬리피지만으로) 바로 청산되는 상황이 생긴다.
        # 그런 무리한 진입은 미리 막는다. (existing_notional엔 지금 물타기하려는 티커의
        # 기존 포지션도 이미 포함돼있고, new_notional은 이번에 '추가'하는 물량만이라 합산하면 정확하다.)
        existing_notional = sum(p.get('amount', 0) * p.get('leverage', 1) for p in positions.values())
        new_notional = amount_won * leverage
        required = (existing_notional + new_notional) * CROSS_MARGIN_MAINTENANCE_RATIO
        free_after = balance - total_cost
        if free_after < required:
            return False, (f"진입 직후 청산 위험 — 여유현금 ${free_after:,.2f}이 유지증거금 ${required:,.2f}보다 적습니다 "
                            f"(명목가치 ${new_notional:,.2f}, 유지증거금 {CROSS_MARGIN_MAINTENANCE_RATIO*100:.1f}%). "
                            f"증거금을 늘리거나 레버리지를 낮추세요.")
    with data_lock:
        current_price = latest_prices_usd.get(ticker, 0)
    if current_price <= 0:
        return False, "USD 가격 없음 (바이낸스 미상장 코인은 거래 불가)"
    fill_price = current_price * (1 + SLIPPAGE_RATE) if position_type == "long" else current_price * (1 - SLIPPAGE_RATE)
    balance -= total_cost
    if balance < 0: balance = 0
    # 진입 당시 '유효 점수'(추세추종/매집형 중 더 높은 쪽) 저장 — Predict Score의
    # Level 항목이 "진입 당시 원본 스코어"를 기준으로 하기 때문에 이 시점에 스냅샷을 남긴다.
    # (물타기/불타기로 추가 진입할 땐 최초 진입 점수를 그대로 유지 — Level은 '처음' 판단 기준이라서)
    snap = score_cache.get(ticker, {})
    if position_type == "long":
        entry_score = max(snap.get('long_score', 0), snap.get('prepump_score', 0))
    else:
        entry_score = max(snap.get('short_score', 0), snap.get('preshort_score', 0))

    if existing:
        # 바이낸스식 물타기/불타기: 명목가치(증거금×레버리지) 가중평균으로 진입가·레버리지를 재계산.
        old_notional = existing['amount'] * existing['leverage']
        add_notional = amount_won * leverage
        combined_notional = old_notional + add_notional
        combined_amount = existing['amount'] + amount_won
        weighted_entry = (existing['entry_price'] * old_notional + fill_price * add_notional) / combined_notional
        combined_leverage = max(1, round(combined_notional / combined_amount))
        positions[ticker] = {
            "entry_price": weighted_entry, "amount": combined_amount, "leverage": combined_leverage,
            "position_type": position_type, "entry_time": existing.get('entry_time', datetime.now()),
            "entry_fee": existing.get('entry_fee', 0) + entry_fee,
            "entry_score": existing.get('entry_score', entry_score),  # 최초 진입 시점 점수 유지
        }
        direction = "롱" if position_type == "long" else "숏"
        trade_history.append({'type': '추가진입', 'ticker': ticker, 'direction': direction,
                              'amount': amount_won, 'leverage': leverage, 'entry_price': fill_price,
                              'exit_price': 0, 'pnl': 0, 'pnl_rate_pct': 0,
                              'entry_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'exit_time': ''})
        save_to_csv()
        pfmt = f"{weighted_entry:,.2f}" if weighted_entry >= 1 else f"{weighted_entry:,.4f}"
        return True, f"{ticker} {direction} 추가진입 @ ${fill_price:,.4f} (평균단가 ${pfmt}, 합계 ${combined_amount:,.2f}/{combined_leverage}x)"

    positions[ticker] = {
        "entry_price": fill_price, "amount": amount_won, "leverage": leverage,
        "position_type": position_type, "entry_time": datetime.now(), "entry_fee": entry_fee,
        "entry_score": entry_score,
    }
    direction = "롱" if position_type == "long" else "숏"
    trade_history.append({'type': '진입', 'ticker': ticker, 'direction': direction,
                          'amount': amount_won, 'leverage': leverage, 'entry_price': fill_price,
                          'exit_price': 0, 'pnl': 0, 'pnl_rate_pct': 0,
                          'entry_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'exit_time': ''})
    save_to_csv()
    pfmt = f"{fill_price:,.2f}" if fill_price >= 1 else f"{fill_price:,.4f}"
    return True, f"{ticker} {direction} 진입 @ ${pfmt}"

def srv_close(ticker):
    global balance
    if ticker not in positions:
        return False, "보유 포지션이 없습니다"
    with data_lock:
        price = latest_prices_usd.get(ticker, 0)
    if price <= 0:
        return False, "USD 가격 없음 (잠시 후 다시 시도)"
    pos = positions[ticker]
    ptype = pos['position_type']
    rate = (price - pos['entry_price']) / pos['entry_price'] if ptype == 'long' else (pos['entry_price'] - price) / pos['entry_price']
    # 격리마진 모드에서는 포지션 손실이 배정 증거금(amount)을 못 넘도록 -100%에서 바닥을 잡았지만,
    # 크로스 마진 모드에서는 계좌 전체 잔고가 받쳐주므로 -100% 밑으로도 그대로 내려가게 둔다.
    raw_pnl = rate * pos['amount'] * pos['leverage']
    pnl = raw_pnl if CROSS_MARGIN_MODE else max(raw_pnl, -pos['amount'])
    exit_fee = (pos['amount'] * pos['leverage']) * FEE_RATE
    if CROSS_MARGIN_MODE:
        # floor 없이 그대로 반영 — 배정 증거금을 넘는 초과 손실은 계좌 잔고에서 계속 깎인다.
        # 잔고 자체가 마이너스로 표시되는 것만 막는다(최종 안전장치).
        balance += (pos['amount'] + pnl - exit_fee)
        if balance < 0:
            balance = 0
    else:
        balance += max(0, pos['amount'] + pnl - exit_fee)
    direction = "롱" if ptype == "long" else "숏"
    pnl_rate_pct = round(pnl / pos['amount'] * 100, 2) if pos['amount'] > 0 else 0
    record = {'type': '청산', 'ticker': ticker, 'direction': direction,
              'amount': pos['amount'], 'leverage': pos['leverage'],
              'entry_price': pos['entry_price'], 'exit_price': price,
              'pnl': round(pnl, 2), 'pnl_rate_pct': pnl_rate_pct,
              'entry_time': pos['entry_time'].strftime('%Y-%m-%d %H:%M:%S') if isinstance(pos['entry_time'], datetime) else str(pos['entry_time']),
              'exit_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    trade_history.append(record)
    append_history_csv(record)
    del positions[ticker]
    save_to_csv()
    return True, f"{ticker} {direction} 청산 손익 ${pnl:+,.2f} ({pnl_rate_pct:+.2f}%)"

def srv_close_all():
    ok_cnt = 0
    for t in list(positions.keys()):
        ok, _ = srv_close(t)
        if ok: ok_cnt += 1
    return True, f"{ok_cnt}개 포지션 청산 완료"

def srv_close_partial(ticker, fraction):
    """
    포지션의 일부만 청산해서 그만큼의 손익만 현금(balance)으로 확정 반영하고,
    나머지는 계속 띄워둔다. "마진잔고(총자산)에 떠 있는 미실현 이익을 현금으로
    빼고 싶다"는 요청은 실제 거래소에서도 부분청산으로만 가능하다 — 청산 전
    미실현 손익은 실체가 없는 계산값일 뿐이라 그냥 인출할 방법이 없기 때문.
    fraction: 0보다 크고 1 이하 (예: 0.3 = 보유량의 30%만 청산)
    """
    global balance
    if ticker not in positions:
        return False, "보유 포지션이 없습니다"
    try:
        fraction = float(fraction)
    except (TypeError, ValueError):
        return False, "청산 비율이 올바르지 않습니다"
    if not (0 < fraction <= 1):
        return False, "청산 비율은 0~100% 사이여야 합니다"
    if fraction >= 0.999:
        return srv_close(ticker)  # 100%는 그냥 전체 청산과 동일
    with data_lock:
        price = latest_prices_usd.get(ticker, 0)
    if price <= 0:
        return False, "USD 가격 없음 (잠시 후 다시 시도)"
    pos = positions[ticker]
    ptype = pos['position_type']
    lev = pos['leverage']
    realize_amt = pos['amount'] * fraction
    rate = (price - pos['entry_price']) / pos['entry_price'] if ptype == 'long' else (pos['entry_price'] - price) / pos['entry_price']
    raw_pnl = rate * realize_amt * lev  # pnl은 amount에 선형 비례하므로 realize_amt만큼만 계산
    pnl = raw_pnl if CROSS_MARGIN_MODE else max(raw_pnl, -realize_amt)
    exit_fee = (realize_amt * lev) * FEE_RATE
    settle = realize_amt + pnl - exit_fee
    if CROSS_MARGIN_MODE:
        balance += settle
        if balance < 0:
            balance = 0
    else:
        balance += max(0, settle)
    pos['amount'] -= realize_amt  # 남은 포지션은 같은 entry_price/leverage로 계속 유지
    direction = "롱" if ptype == "long" else "숏"
    pnl_rate_pct = round(pnl / realize_amt * 100, 2) if realize_amt > 0 else 0
    record = {'type': '부분청산', 'ticker': ticker, 'direction': direction,
              'amount': round(realize_amt, 2), 'leverage': lev,
              'entry_price': pos['entry_price'], 'exit_price': price,
              'pnl': round(pnl, 2), 'pnl_rate_pct': pnl_rate_pct,
              'entry_time': pos['entry_time'].strftime('%Y-%m-%d %H:%M:%S') if isinstance(pos['entry_time'], datetime) else str(pos['entry_time']),
              'exit_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    trade_history.append(record)
    append_history_csv(record)
    save_to_csv()
    return True, f"{ticker} {direction} {fraction*100:.0f}% 부분청산 손익 ${pnl:+,.2f} → 현금 반영 (잔여 ${pos['amount']:,.2f})"

def _pos_pnl(pos, cur_price):
    entry = pos.get('entry_price', 0)
    if entry <= 0 or cur_price <= 0:
        return 0.0
    ptype = pos.get('position_type')
    rate = (cur_price - entry) / entry if ptype == 'long' else (entry - cur_price) / entry
    return rate * pos.get('amount', 0) * pos.get('leverage', 1)

def _liquidate_position(t, pos, cur_price, tag="강제청산"):
    """포지션 하나를 강제청산 처리하고 거래 기록에 남긴다. balance는 호출부에서 반영."""
    ptype = pos.get('position_type')
    amt = pos.get('amount', 0)
    lev = pos.get('leverage', 1)
    entry = pos.get('entry_price', 0)
    pnl = _pos_pnl(pos, cur_price)
    exit_fee = amt * lev * FEE_RATE
    pnl_after_fee = pnl - exit_fee
    direction = "롱" if ptype == "long" else "숏"
    et = pos.get('entry_time', datetime.now())
    record = {'type': tag, 'ticker': t, 'direction': direction,
              'amount': amt, 'leverage': lev,
              'entry_price': entry, 'exit_price': cur_price,
              'pnl': round(pnl_after_fee, 2), 'pnl_rate_pct': round(pnl_after_fee / max(amt, 1) * 100, 2),
              'entry_time': et.strftime('%Y-%m-%d %H:%M:%S') if isinstance(et, datetime) else str(et),
              'exit_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    trade_history.append(record)
    append_history_csv(record)
    return pnl_after_fee

def check_forced_liquidation():
    """포지션 강제청산 감시 (서버가 GUI 없이도 24시간 감시). CROSS_MARGIN_MODE에 따라
    두 가지 방식 중 하나로 동작한다."""
    if CROSS_MARGIN_MODE:
        _check_cross_margin_liquidation()
    else:
        _check_isolated_liquidation()

def _check_cross_margin_liquidation():
    """
    크로스 마진 방식: 바이낸스 크로스 마진처럼, 개별 포지션이 -100%(그 포지션에 배정한
    증거금 전액)를 넘는 손실을 봐도 계좌 총자산(현금+모든 포지션 미실현손익 합)이
    유지증거금(열려있는 포지션 명목가치 합 × CROSS_MARGIN_MAINTENANCE_RATIO)보다 크면
    아무것도 하지 않는다. 총자산이 그 아래로 떨어질 때만, 손실이 가장 큰 포지션부터
    하나씩 청산해서 계좌를 살린다(실제 거래소의 마진콜 캐스케이드 청산과 동일한 방식).
    """
    global balance
    with data_lock:
        prices = dict(latest_prices_usd)
        snap = {t: dict(p) for t, p in positions.items()}
    if not snap:
        return

    def total_equity(remaining):
        eq = balance
        for t, pos in remaining.items():
            eq += _pos_pnl(pos, prices.get(t, pos.get('entry_price', 0)))
        return eq

    def maintenance_required(remaining):
        notional = sum(pos.get('amount', 0) * pos.get('leverage', 1) for pos in remaining.values())
        return notional * CROSS_MARGIN_MAINTENANCE_RATIO

    remaining = dict(snap)
    if total_equity(remaining) > maintenance_required(remaining):
        return  # 계좌가 버티고 있으면 개별 포지션이 -100% 넘게 물려있어도 그대로 둔다

    while remaining:
        # 손실이 가장 큰(가장 음수인) 포지션을 골라 하나씩 청산
        worst_t, worst_pos, worst_pnl = None, None, None
        for t, pos in remaining.items():
            cur = prices.get(t, pos.get('entry_price', 0))
            pnl = _pos_pnl(pos, cur)
            if worst_pnl is None or pnl < worst_pnl:
                worst_t, worst_pos, worst_pnl = t, pos, pnl
        if worst_t is None:
            break
        cur = prices.get(worst_t, worst_pos.get('entry_price', 0))
        entry = worst_pos.get('entry_price', 0)
        lev = worst_pos.get('leverage', 1)
        rate_pct = ((cur - entry) / entry * 100) if entry > 0 else 0
        pnl_after_fee = _liquidate_position(worst_t, worst_pos, cur, tag="강제청산(크로스)")
        amt = worst_pos.get('amount', 0)
        # 크로스 마진은 floor 없이 그대로 반영 — 배정 증거금을 넘는 초과 손실은 나머지 잔고에서 깎인다
        balance += (amt + pnl_after_fee)
        if balance < 0:
            balance = 0
        if worst_t in positions:
            del positions[worst_t]
        del remaining[worst_t]
        save_to_csv()
        print(f"⚠️ 크로스마진 강제청산: {worst_t} ${pnl_after_fee:+,.2f} "
              f"(레버리지 {lev}x, 가격변동 {rate_pct:+.2f}%, 증거금 ${amt:,.2f} → 명목가치 ${amt*lev:,.2f} | "
              f"계좌 총자산 방어, 잔여 총자산 ${total_equity(remaining):,.2f})")
        if total_equity(remaining) > maintenance_required(remaining):
            break

def _check_isolated_liquidation():
    """격리마진(레거시) 방식: 포지션별로 배정 증거금의 90%를 잃으면 그 포지션만 강제청산한다."""
    global balance
    LIQ_RATIO = 0.9
    with data_lock:
        prices = dict(latest_prices_usd)
        snap = {t: dict(p) for t, p in positions.items()}
    for t, pos in snap.items():
        cur = prices.get(t, pos.get('entry_price', 0))
        amt = pos.get('amount', 0)
        pnl = _pos_pnl(pos, cur)
        if pnl <= -amt * LIQ_RATIO and t in positions:
            pnl_after_fee = _liquidate_position(t, pos, cur, tag="강제청산")
            balance += max(0, amt + pnl_after_fee)
            del positions[t]
            save_to_csv()
            print(f"⚠️ 강제청산({int(LIQ_RATIO*100)}%): {t} ${pnl_after_fee:+,.2f}")

def process_commands():
    """server_cmds/ 폴더의 명령 파일을 처리하고 삭제한다.
    명령 형식(1행): cmd_id,action,ticker,amount_won,leverage,position_type"""
    try:
        files = sorted(os.listdir(CMD_DIR))
    except Exception:
        return
    for fname in files:
        if not fname.startswith('cmd_') or not fname.endswith('.csv'):
            continue
        path = os.path.join(CMD_DIR, fname)
        try:
            with open(path, 'r', newline='', encoding='utf-8') as f:
                row = next(csv.reader(f), None)
            if not row or len(row) < 2:
                os.remove(path); continue
            cmd_id, action = row[0], row[1]
            raw_ticker = row[2].strip() if len(row) > 2 else ''
            ticker = raw_ticker.upper()
            amount = round(float(row[3]), 2) if len(row) > 3 and row[3] else 0
            lev = int(float(row[4])) if len(row) > 4 and row[4] else DEFAULT_LEVERAGE
            ptype = row[5] if len(row) > 5 else 'long'
            if action == 'charge':
                ok, msg = srv_charge(amount)
            elif action == 'withdraw':
                ok, msg = srv_withdraw(amount)
            elif action == 'bank_deposit':
                ok, msg = srv_bank_deposit(amount)
            elif action == 'bank_withdraw':
                ok, msg = srv_bank_withdraw(amount)
            elif action == 'open':
                ok, msg = srv_open(ticker, ptype, amount, lev)
            elif action == 'close':
                ok, msg = srv_close(ticker)
            elif action == 'close_all':
                ok, msg = srv_close_all()
            elif action == 'reset':
                ok, msg = srv_reset_balance()
            elif action == 'set_interval':
                ok, msg = srv_set_interval(ticker)
            elif action == 'set_margin_mode':
                ok, msg = srv_set_margin_mode(ticker)
            elif action == 'generate_report':
                ok, msg = srv_generate_report()
            elif action == 'set_report_dir':
                ok, msg = srv_set_report_dir(raw_ticker)
            elif action == 'get_candles':
                ok, msg = srv_get_candles(ticker, interval=ptype)
            else:
                ok, msg = False, f"알 수 없는 명령: {action}"
            append_result(cmd_id, 'ok' if ok else 'fail', msg)
            print(f"[명령] {action} {ticker} → {'OK' if ok else 'FAIL'}: {msg}")
        except Exception as e:
            append_result(fname, 'fail', f"처리 오류: {e}")
        finally:
            try: os.remove(path)
            except Exception: pass

def account_loop():
    """1초 주기: 명령 처리 → 강제청산 감시 → 계좌 스냅샷 갱신"""
    last_save = time.time()
    while running:
        try:
            process_commands()
            check_forced_liquidation()
            write_account_snapshot()
            if time.time() - last_save >= 60:
                save_to_csv()
                last_save = time.time()
        except Exception as e:
            print(f"account_loop 오류: {e}")
        time.sleep(1)

def snapshot_loop():
    """PRICE_INTERVAL 주기로 마켓 스냅샷 갱신 (가격은 최신, 지표는 마지막 스코어 사이클 값)"""
    while running:
        try:
            write_market_snapshot()
        except Exception as e:
            print(f"snapshot_loop 오류: {e}")
        time.sleep(PRICE_INTERVAL)

# score_updater가 돌 때 스코어 갱신 시각을 남기기 위한 래퍼
_orig_score_updater = score_updater
def score_updater(tickers_ref):  # noqa: F811
    def _mark():
        _last_score_time[0] = datetime.now().strftime('%H:%M:%S')
    import types
    # 간단히: 원본을 그대로 돌리되, 스냅샷 시각은 data_queue.put 대신 여기서 주기 갱신
    def marker_loop():
        while running:
            with score_lock:
                if score_cache:
                    _mark()
            time.sleep(SCORE_INTERVAL)
    threading.Thread(target=marker_loop, daemon=True).start()
    _orig_score_updater(tickers_ref)

# ============================================================
if __name__ == "__main__":
    print("=" * 50)
    print("트레이딩 서버 시작 (계산/계좌 엔진 — 마켓 로깅 없음, 수집은 별도 기기)")
    print(f"데이터 폴더: {SCRIPT_DIR}")
    print(f"마켓 스냅샷: {MARKET_SNAPSHOT}")
    print(f"명령 폴더: {CMD_DIR}")
    print("중지: Ctrl+C  (포지션은 서버가 계속 감시하므로 GUI는 꺼도 됨)")
    print("=" * 50)

    load_from_csv()
    load_history_csv()

    tickers_ref = [[]]
    try:
        price_data = Bithumb.get_current_price("ALL")
        tickers_ref[0] = [k for k in list(price_data.keys()) if k != "date"][:TOP_COIN_COUNT]
        print(f"티커 {len(tickers_ref[0])}개 로드")
    except:
        tickers_ref[0] = ["BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "TRX", "AVAX", "LINK", "BNB"]

    threading.Thread(target=ticker_updater, args=(tickers_ref,), daemon=True).start()
    threading.Thread(target=price_updater, args=(tickers_ref,), daemon=True).start()
    threading.Thread(target=score_updater, args=(tickers_ref,), daemon=True).start()
    threading.Thread(target=snapshot_loop, daemon=True).start()
    threading.Thread(target=account_loop, daemon=True).start()

    try:
        last_status = 0
        while True:
            time.sleep(5)
            if time.time() - last_status >= 60:
                with score_lock:
                    n = len(score_cache)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 서버 가동 | 코인 {n}개 | 컷 {current_min_score} | 잔고 ${balance:,.2f} | 포지션 {len(positions)}개")
                last_status = time.time()
    except KeyboardInterrupt:
        running = False
        save_to_csv()
        print("\n서버 종료 (잔고/포지션 저장됨)")
