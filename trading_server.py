import threading
import time
import sys
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
INITIAL_BALANCE = 10000  # USD (달러 기준 계좌)
DEFAULT_LEVERAGE = 5
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
#
# 다만 윈도우 등 다른 환경에서 쓸 때는 이 경로가 아예 안 맞을 수 있어서,
# 스크립트 폴더(_fallback_dir)에 놓인 path_config.txt 파일로 경로를 직접
# 지정할 수 있게 했다. trading_client.py의 "경로 변경" 버튼이 이 파일을
# 쓰고, 서버도 시작할 때 이 파일을 최우선으로 확인한다 — server/client를
# 같은 폴더에 두고 쓰는 걸 전제로 하므로 둘 다 같은 파일을 보게 된다.
SCRIPT_DIR = None
_PATH_CONFIG_FILE = os.path.join(_fallback_dir, "path_config.txt")
if os.path.exists(_PATH_CONFIG_FILE):
    try:
        with open(_PATH_CONFIG_FILE, "r", encoding="utf-8") as _f:
            _custom_path = _f.read().strip()
        if _custom_path:
            os.makedirs(_custom_path, exist_ok=True)
            _test_path = os.path.join(_custom_path, ".write_test")
            with open(_test_path, "w") as _f:
                _f.write("ok")
            os.remove(_test_path)
            SCRIPT_DIR = _custom_path
            print(f"✅ 데이터 저장 위치(사용자 지정): {SCRIPT_DIR}")
    except Exception as e:
        print(f"⚠️ path_config.txt 경로 사용 실패({e}), 기본 경로로 진행")

if SCRIPT_DIR is None:
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
DATA_FILE = os.path.join(SCRIPT_DIR, "simulation_data_usd.csv")

# ============================================================
# 중복 실행 방지 — 서버 두 개가 같은 server_market.csv/server_account.csv에
# 동시에 쓰면, 클라이언트가 두 프로세스의 결과를 번갈아 읽으면서 점수가
# 100점을 넘거나(예: 139), 존재하지 않는 티커가 뜨거나, 컷이 갑자기 0으로
# 보이는 등 "화면이 계속 깜빡이며 말도 안 되는 값이 뜨는" 증상이 생긴다.
# 파일 mtime은 계속 갱신되므로 "방금 갱신됨"인데 값은 엉망인 것처럼 보인다.
# 이를 막기 위해 PID 락 파일로 이미 실행 중인 프로세스가 있는지 확인한다.
# ============================================================
_LOCK_FILE = os.path.join(SCRIPT_DIR, "server.lock")

def _check_single_instance():
    if os.path.exists(_LOCK_FILE):
        try:
            with open(_LOCK_FILE) as f:
                old_pid = int(f.read().strip())
            alive = True
            try:
                os.kill(old_pid, 0)
            except ProcessLookupError:
                alive = False  # 그 PID로 실행 중인 프로세스가 없음 → 오래된 락 파일
            except PermissionError:
                alive = True   # 신호를 보낼 권한만 없을 뿐, 프로세스 자체는 살아있음
            if alive:
                print("=" * 60)
                print(f"⚠️⚠️⚠️  다른 서버 프로세스(PID {old_pid})가 이미 실행 중입니다!")
                print("   서버 두 개가 같은 CSV에 동시에 쓰면 화면이 깜빡이거나")
                print("   점수가 100을 넘는 등 이상한 값이 보일 수 있습니다.")
                print(f"   기존 프로세스를 끄려면: kill {old_pid}   (안 꺼지면: kill -9 {old_pid})")
                print("=" * 60)
        except (OSError, ValueError):
            pass  # 락 파일을 읽을 수 없으면(손상 등) 그냥 무시하고 덮어씀
    try:
        with open(_LOCK_FILE, 'w') as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

def _release_instance_lock():
    try:
        if os.path.exists(_LOCK_FILE):
            with open(_LOCK_FILE) as f:
                if f.read().strip() == str(os.getpid()):
                    os.remove(_LOCK_FILE)
    except Exception:
        pass
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
# 거시 필터 (공포탐욕지수 + BTC 자체 추세) — 개별 코인 지표만으로는 못 잡는
# "시장 전체가 한 번에 롤오버하는" 리스크를 잡기 위한 시장 전체 게이트.
# alternative.me의 공포탐욕지수는 무료/무인증 공개 API.
# ============================================================
FNG_URL = "https://api.alternative.me/fng/"
_fng_cache = {"value": None, "classification": None, "time": 0}
_fng_lock = threading.Lock()

def get_fear_greed_index():
    """공포탐욕지수(0~100)와 등급 문자열을 반환. 하루 1회만 갱신되는 지표라
    캐시는 30분으로 넉넉히 둔다. 조회 실패 시 이전 캐시값(없으면 None)을 반환 —
    거시필터 자체가 죽지 않고, 판단 불가 상황을 그대로 넘긴다."""
    now = time.time()
    with _fng_lock:
        if _fng_cache["value"] is not None and now - _fng_cache["time"] < 1800:
            return _fng_cache["value"], _fng_cache["classification"]
    try:
        resp = requests.get(FNG_URL, params={"limit": 1, "format": "json"}, timeout=8, verify=False)
        resp.raise_for_status()
        item = resp.json()["data"][0]
        value = int(item["value"])
        classification = item.get("value_classification", "")
        with _fng_lock:
            _fng_cache["value"] = value
            _fng_cache["classification"] = classification
            _fng_cache["time"] = now
        return value, classification
    except Exception as e:
        print(f"공포탐욕지수 조회 실패: {e}")
        with _fng_lock:
            return _fng_cache["value"], _fng_cache["classification"]

def compute_macro_state(results):
    """이번 사이클 결과 중 BTC 자체의 EMA 추세를 시장 대표 추세로 쓰고, 공포탐욕지수와
    합쳐서 시장 전체 상태를 하나로 정리한다. (개별 코인 필터는 전부 통과해도 이게
    막히면 진입 보류 — passes_macro_filter 참고)"""
    btc = next((r for r in results if r.get('ticker') == 'BTC'), None)
    comp = (btc or {}).get('components', {}) or {}
    fng_value, fng_class = get_fear_greed_index()
    return {
        "btc_ema_l": comp.get('ema_l', 0),
        "btc_ema_s": comp.get('ema_s', 0),
        "fng_value": fng_value,
        "fng_class": fng_class or "",
    }

def passes_macro_filter(direction, macro):
    """
    시장 전체 거시 게이트. 개별 종목 필터를 다 통과해도 이게 막히면 진입하지 않는다.
      롱: BTC가 완전 역배열(자체 하락추세, btc_ema_s==30)이면 차단 |
          공포탐욕지수≥80(극단적 탐욕, 조정 위험)이면 차단
      숏: BTC가 완전 정배열(자체 상승추세, btc_ema_l==30)이면 차단 |
          공포탐욕지수≤20(극단적 공포, 반등/숏스퀴즈 위험)이면 차단
    BTC/지수 조회에 실패해 정보가 없으면(None) 안전하게 통과시킨다 — 조회 실패
    하나로 전체 매매가 멈추는 걸 막기 위함(단, 이 경우 거시 리스크 방어는 안 됨).
    """
    fng = macro.get("fng_value")
    if direction == "long":
        if macro.get("btc_ema_s", 0) == 30:
            return False
        if fng is not None and fng >= 80:
            return False
    else:
        if macro.get("btc_ema_l", 0) == 30:
            return False
        if fng is not None and fng <= 20:
            return False
    return True

# ============================================================
# 빗썸 전체 마켓 조회 캐싱 — process_ticker가 종목마다(사이클당 최대 ~40번) 이걸
# 직접 호출하고 있었는데, 매번 빗썸 전체 마켓을 새로 조회하는 건 낭비였다. 사이클
# 하나에 40번 호출해봤자 그 사이 데이터가 바뀔 리 없는데, 그만큼 네트워크 부하만
# 늘어서 사이클이 가끔 느려지는(클라이언트 '연결 끊김' 깜빡임의 유력한 원인) 문제가
# 있었다. 8초 캐시로 사이클당 실제 호출은 사실상 1번만 나가게 한다.
# ============================================================
_all_ticker_cache = {"data": None, "time": 0}
_all_ticker_lock = threading.Lock()

def get_all_ticker_snapshot():
    now = time.time()
    with _all_ticker_lock:
        if _all_ticker_cache["data"] is not None and now - _all_ticker_cache["time"] < 8:
            return _all_ticker_cache["data"]
    try:
        data = Bithumb.get_current_price("ALL")
        with _all_ticker_lock:
            _all_ticker_cache["data"] = data
            _all_ticker_cache["time"] = now
        return data
    except Exception as e:
        print(f"전체 마켓 조회 실패: {e}")
        with _all_ticker_lock:
            return _all_ticker_cache["data"]

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
                                 'entry_price','exit_price','pnl','pnl_rate_pct','entry_time','exit_time',
                                 'entry_score'])
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
                record.get('entry_score', 0),
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
                    # 구버전 CSV(컬럼 없음)는 0으로 처리 — 진입 당시 점수를 몰라도
                    # 나머지 필드로는 계속 정상 동작해야 하므로.
                    'entry_score': float(row.get('entry_score', 0) or 0),
                })
        print(f"✅ 거래기록 {len(trade_history)}건 로드")
        # 구버전 CSV(entry_score 컬럼 없음)를 새 스키마로 한 번만 재작성 —
        # 안 하면 이후 append_history_csv가 12개 값을 쓰는데 헤더는 11개짜리로
        # 남아서 다음 로드 때 컬럼이 밀려 보인다.
        with open(HISTORY_FILE, 'r', newline='', encoding='utf-8') as f:
            first_line = f.readline()
        if 'entry_score' not in first_line:
            try:
                with open(HISTORY_FILE, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(['type','ticker','direction','amount','leverage',
                                     'entry_price','exit_price','pnl','pnl_rate_pct','entry_time','exit_time',
                                     'entry_score'])
                    for rec in trade_history:
                        writer.writerow([rec.get('type',''), rec.get('ticker',''), rec.get('direction',''),
                                         rec.get('amount', 0), rec.get('leverage', 0), rec.get('entry_price', 0),
                                         rec.get('exit_price', 0), rec.get('pnl', 0), rec.get('pnl_rate_pct', 0),
                                         rec.get('entry_time', ''), rec.get('exit_time', ''), rec.get('entry_score', 0)])
                print("✅ 거래기록 CSV를 새 스키마(entry_score 포함)로 갱신")
            except Exception as e:
                print(f"거래기록 CSV 스키마 갱신 실패: {e}")
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
# 85점 만점 점수 체계 (추세추종형 개편안 v5, 최종 100점 환산)
#   — v4(80점) 실측 데이터 검증 결과 방향 자체는 맞다고 확인되어, 변별력을 높이는
#     방향으로 세분화. 함수명은 process_ticker 등 다른 곳에서 참조하고 있어
#     score_oi_v4/score_volz_v4처럼 기존 이름을 그대로 유지했다(내용은 v5로 갱신).
#
#   ① EMA 정배열 + 가격위치           30점  (4단계로 세분화, v4 대비 변별력↑)
#   ② OI Synergy                    20점  (v4:15 → 실자금 유입 신호가 생각보다 강해 비중↑)
#   ③ CVD                           10점  (방향일치 + 강도(증가세)까지 반영)
#   ④ RSI + BB% (과열/과매도 필터)   10점  (v4:10 유지, 경계값만 정리)
#   ⑤ VolZ                          5점  (v4와 동일 — 거래량 초기 여부 판단용)
#   ⑥ 30분 모멘텀                    5점  (v7 — 실측 데이터로 방향 반전. "막 오르기 시작"이 아니라
#                                            "아직 안 움직인 상태"가 forward return이 가장 좋았음)
#   ⑦ ATR + 거래대금 필터            5점  (3단계로 세분화 — 너무 낮아도/높아도 감점)
#   합계 85점 → final = round(raw합 / 85 × 100), 0~100 클램프
#
#   [v5 추가 권장 필터] passes_v5_hard_filters()가 아래 조건들을 검사한다:
#     - EMA 역배열 또는 OI 방향 불일치
#     - CVD가 반대 방향
#     - VolZ≥2.0 (이미 거래량 폭발 — 추격매수/매도 방지)
#     - 30분 모멘텀 조건 미충족(이미 많이 진행됐거나 모멘텀 자체가 없음)
#     - 유동성 필터 탈락
#   이전 버전은 이 필터를 서버가 통과 못 하면 최종 점수를 강제로 0으로 만들었지만,
#   그러면 "필터만 없었으면 몇 점이었는지"가 사라져서 필터 자체의 효과를 사후
#   검증할 수 없었다. 그래서 지금은 calculate_long_score/short_score가 항상
#   raw합 기반 점수를 그대로 반환하고, process_ticker가 filters_ok_long/short
#   필드로 통과 여부만 별도로 함께 내려준다. 실제로 필터를 적용할지 말지는
#   trading_client.py의 "필터 적용" 체크박스에서 사용자가 직접 선택한다.
# ============================================================
TOTAL_SCORE_WEIGHT = 85  # 위 7개 항목 배점의 합. 항목을 추가/변경하면 같이 맞춰야 함

EMA_EXTENSION_THRESHOLD = 1.0  # v7 — 실측 64,880행 분석 결과 기존 3.0은 상위 96%ile로 너무 느슨했음.
                                 # extension_pct 상위 20%(약 0.9~1.3, 80~85%ile)부터 이미 forward
                                 # return이 나빠지길래 그 경계에 맞춰 1.0으로 당겼다.

def score_ema_trend(price, ema20, ema60, ema120, direction, extension_pct=0.0):
    """
    EMA 삼중(20/60/120) 정배열 점수(최대 30점, v6 — extension_pct로 "이미 다 뻗은 뒤늦은
    정배열"과 "막 정배열이 시작된 건강한 추세"를 구분).
    실측 검증 결과: 정배열이 완벽히 정착한 시점(가격이 EMA20에서 이미 많이 벌어진 상태)에
    진입하면 1h 수익률은 눌림목 때문에 마이너스/횡보였다가 2h에야 플러스로 전환되는
    패턴이 강했다 — 즉 "완성된 정배열"은 고점 부근 추격매수 위험 신호. 그래서 v5에서
    최고점을 주던 "가격 > EMA20"을, 얼마나 뻗었는지(extension_pct)로 다시 나눴다.
      롱: 정배열 + 가격≈EMA20(±0.3%, 초입/지지 확인 구간)                → 30 (가장 좋은 타점)
          정배열 + 가격>EMA20 이지만 아직 안 뻗음(extension_pct 정상)     → 25
          정배열 + 가격>EMA20 인데 이미 많이 뻗음(extension_pct 과열)     → 12 (고점 추격 위험)
          정배열 + 가격이 EMA20~EMA60 사이(눌림목)                        → 18
          역배열이거나 EMA60 밑으로 이탈                                   → 0
      숏: 대칭.
    """
    try:
        if price is None or not ema20 or ema60 is None or ema120 is None:
            return 0
        near_tol = 0.003  # EMA20 대비 ±0.3% 이내를 "가격≈EMA20"으로 간주 (문서에 수치 없어 임의 설정)
        if direction == 'long':
            if not (ema20 > ema60 > ema120):
                return 0
            if abs(price - ema20) / ema20 <= near_tol:
                return 30
            elif price > ema20:
                return 12 if extension_pct > EMA_EXTENSION_THRESHOLD else 25
            elif ema60 <= price < ema20:
                return 18
            return 0
        else:
            if not (ema20 < ema60 < ema120):
                return 0
            if abs(price - ema20) / ema20 <= near_tol:
                return 30
            elif price < ema20:
                return 12 if extension_pct < -EMA_EXTENSION_THRESHOLD else 25
            elif ema20 < price <= ema60:
                return 18
            return 0
    except Exception:
        return 0

RSI_DELTA_EXPLOSIVE = 10.0  # 5봉 대비 RSI 변화량 절대값이 이보다 크면 "폭발적 모멘텀"으로 간주
                              # (구체 수치 근거는 없음, 데이터 보며 조정 필요)

def score_price_position_long(rsi, bb_percent, rsi_delta=0.0):
    """
    RSI+BB% 필터 점수(최대 10점, v6 — rsi_delta로 "폭발적 급등 직후 진입" 리스크 반영).
    실측 검증 결과: RSI가 70을 넘고 RSI_delta가 폭발적으로 튄 직후(장대양봉) 진입하면
    방향은 결국 맞아도(2h 뒤엔 양전) 1h 안에서의 변동성(MDD)이 너무 커서 효율적인
    타점이 아니었다. 그래서 "RSI 과열 + 델타 폭발"은 감점한다.
      10점: %B≥80 & 55≤RSI≤70 (RSI_delta 폭발적이지 않을 때만)
       2점: %B≥80 & RSI>70 & RSI_delta 폭발적(급등 직후, 진입 타점 나쁨)
       5점: %B가 70~80 이거나 RSI가 70~80 (과열권 초입)
       2점: RSI≤45 또는 %B<50 (추세 약함)
       0점: 위 어느 조건에도 안 맞음
    """
    try:
        explosive = abs(rsi_delta) >= RSI_DELTA_EXPLOSIVE
        if bb_percent >= 80 and 55 <= rsi <= 70 and not explosive:
            return 10
        elif bb_percent >= 80 and rsi > 70 and explosive:
            return 2
        elif (70 <= bb_percent < 80) or (70 <= rsi <= 80):
            return 5
        elif rsi <= 45 or bb_percent < 50:
            return 2
        return 0
    except Exception:
        return 0

def score_price_position_short(rsi, bb_percent, rsi_delta=0.0):
    """RSI+BB% 필터 숏 점수(최대 10점, 롱과 대칭, v6 — rsi_delta 반영)."""
    try:
        explosive = abs(rsi_delta) >= RSI_DELTA_EXPLOSIVE
        if bb_percent <= 20 and 30 <= rsi <= 45 and not explosive:
            return 10
        elif bb_percent <= 20 and rsi < 30 and explosive:
            return 2
        elif (20 <= bb_percent < 30) or (20 <= rsi <= 30):
            return 5
        elif rsi >= 55 or bb_percent > 50:
            return 2
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
    CVD 점수(최대 10점, v5 — 방향 일치 여부뿐 아니라 강도(증가세)까지 반영).
    "증가세 강함" 기준(거래량 대비 5% 이상)은 문서에 구체 수치가 없어 임의 설정.
      10점: 방향 일치 + 증가세 강함(|cvd_diff| ≥ 거래량의 5%)
       7점: 방향 일치(강도는 약함)
       3점: 보합
       0점: 반대 방향
    """
    try:
        eps = 0.02 * vol_window_sum if vol_window_sum > 0 else 0.0
        strong_eps = 0.05 * vol_window_sum if vol_window_sum > 0 else 0.0
        want = 1 if direction == 'long' else -1
        signed = cvd_diff if want == 1 else -cvd_diff
        if signed > strong_eps: return 10
        elif signed > eps: return 7
        elif signed >= -eps: return 3
        return 0
    except Exception:
        return 0

def score_oi_v4(price_chg, oi_change_pct, direction):
    """
    OI Synergy 점수(최대 20점, v5에서 15→20 상향 — 실측 데이터상 자금 유입 신호가
    비중을 키울 만큼 유효했다). 가격 방향 + OI 변화 방향(상승/보합/하락) 조합.
    OI 변화 ±1%를 "보합" 경계로 임의 설정(문서에 구체 수치 없음).
      롱: 가격↑+OI↑→20 | 가격↑+OI≈→10 | 가격↑+OI↓ 또는 가격↓→0
      숏: 가격↓+OI↑→20 | 가격↓+OI≈→10 | 가격↓+OI↓ 또는 가격↑→0
    """
    try:
        if oi_change_pct >= 1:
            oi_state = 'up'
        elif oi_change_pct <= -1:
            oi_state = 'down'
        else:
            oi_state = 'flat'

        if direction == 'long':
            if price_chg <= 0:
                return 0
            if oi_state == 'up': return 20
            elif oi_state == 'flat': return 10
            return 0
        else:
            if price_chg >= 0:
                return 0
            if oi_state == 'up': return 20
            elif oi_state == 'flat': return 10
            return 0
    except Exception:
        return 0

def score_volz_v4(vol_z):
    """
    VolZ 거래량 점수(최대 5점, v4와 동일 — 거래량이 "이미 터졌는지" 초기 여부만 판단).
    실측 데이터에서 VolZ가 높을수록 이후 수익률이 낮아지는 역신호로 확인됐다.
      VolZ<1.0: 5 | 1.0~2.0: 2 | ≥2.0(이미 폭증): 0
    """
    try:
        if vol_z < 1.0: return 5
        elif vol_z < 2.0: return 2
        return 0
    except Exception:
        return 0

def score_chg30m_long(chg_30m):
    """
    최근 30분 모멘텀 롱 점수(최대 5점, v7.1 — 완전 반전 대신 완만한 4단계로 조정).
    실측 64,880행(약 22시간, 완만한 상승장 구간)에서는 chg_30m≤0%가 전 구간에서
    가장 좋은 forward return을 보였지만, 표본이 한 장세(22시간, 상승장)에 국한돼
    있어서 급등/급락장에서는 반대(막 움직이기 시작한 쪽이 유리)일 가능성이 있다.
    그래서 완전 반전 대신 그라데이션으로 완충한다 — "안 움직인 코인 유리"는
    반영하되 "막 출발한 코인"도 완전히 버리지 않는다. 데이터가 더 쌓여
    여러 장세(횡보/급락 포함, 20~30만 행 이상)에서도 같은 방향이 확인되면
    그때 완전 반전을 검토한다.
      chg_30m≤0%: 5 | 0~0.15%: 4 | 0.15~0.4%: 2 | 0.4% 초과: 0
    """
    if chg_30m <= 0: return 5
    elif chg_30m <= 0.15: return 4
    elif chg_30m <= 0.4: return 2
    return 0

def score_chg30m_short(chg_30m):
    """최근 30분 모멘텀 숏 점수(최대 5점, 롱과 대칭, v7.1 완만한 조정)."""
    if chg_30m >= 0: return 5
    elif chg_30m >= -0.15: return 4
    elif chg_30m >= -0.4: return 2
    return 0

def score_liquidity_filter(atr_pct, vol_24h_m):
    """
    ATR/거래대금 필터(최대 5점, v5에서 3단계로 세분화 — 기존엔 통과/미통과 이진이었으나
    "너무 낮음"뿐 아니라 "너무 높음(과열 변동성)"도 걸러내도록 확장.
    경계값은 문서에 구체 수치가 없어 임의 설정 — 데이터 보면서 조정 필요.
      5점(정상): 0.3%≤ATR≤3.0% & 24h거래대금≥1천만원
      3점(약간 부족): 유동성은 있으나 정상 범위를 살짝 벗어난 경계 구간
      0점: 그 외(너무 죽어있거나 너무 과열된 변동성)
    """
    try:
        if 0.3 <= atr_pct <= 3.0 and vol_24h_m >= 10:
            return 5
        if (0.15 <= atr_pct < 0.3 or 3.0 < atr_pct <= 5.0) and vol_24h_m >= 3:
            return 3
        return 0
    except Exception:
        return 0

def passes_v5_hard_filters(ema_sc, oi_sc, cvd_sc, vol_z, m30_sc, liquidity_sc):
    """
    v5 추가 권장 필터 (v6.2 — v6.1의 OI≥20 기준이 실측 데이터에서 너무 빡빡했던 것을
    되돌림). v6.1에서 oi_sc≥20(방향 완전일치 만점)까지 요구했더니, 실측 11,200행
    중 전체 조건을 통과한 게 0.03%(사실상 0건)였다 — oi_l이 20점을 받는 경우 자체가
    1.3%뿐이라 다른 조건과 겹치면 사실상 신호가 안 나왔다. CVD는 그대로 두고
    (cvd_sc≥7 단독 통과율 39%로 적당했음) OI만 다시 10점(보합 이상)으로 완화한다.
      - ema_sc==0 (역배열) → 탈락
      - oi_sc<10 (OI가 아예 반대 방향(0점)일 때만) → 탈락. 10점(보합)은 다시 허용.
      - cvd_sc<7 (CVD가 보합(3점)이거나 반대 방향(0점)) → 탈락 (v6.1 그대로 유지)
      - vol_z≥2.0 (거래량 이미 폭발 — 추격 방지)
      - m30_sc==0 (30분 모멘텀 조건 미충족 — 이미 많이 진행됐거나 모멘텀 없음)
      - liquidity_sc==0 (유동성 필터 탈락)
    """
    try:
        if ema_sc == 0:
            return False
        if oi_sc < 10:
            return False
        if cvd_sc < 7:
            return False
        if vol_z >= 2.0:
            return False
        if m30_sc == 0:
            return False
        if liquidity_sc == 0:
            return False
        return True
    except Exception:
        return False

def calculate_long_score(rsi, bb_percent, cvd_diff, vol_window_sum, ls_ratio, oi_change_pct, chg_30m,
                          price_chg, extension_pct, vol_z=0.0, rsi_delta=0.0,
                          atr_pct=0.0, ema20=None, ema60=None, oi_notional_usd=None,
                          funding_rate=0.0, trade_value_usd=None,
                          price=None, ema120=None, vol_24h_m=0):
    """
    85점 만점 롱 점수 = EMA+가격위치(30) + OI synergy(20) + CVD(10) + RSI/BB필터(10)
                       + VolZ(5) + 30분모멘텀(5) + 유동성필터(5)
    최종적으로 /85×100 환산한 0~100점을 반환한다. (v5, 데이터 기반 세분화)

    v5 하드필터(passes_v5_hard_filters)는 더 이상 여기서 점수를 강제로 0으로
    만들지 않는다 — 항상 raw합 기반 점수를 그대로 반환하고, 필터 통과 여부는
    process_ticker에서 별도 필드(filters_ok_long/short)로 함께 내려서 클라이언트가
    체크박스로 필터 적용 여부를 직접 선택하게 한다(서버가 정보를 지우지 않음).
    """
    ema_sc = price_sc = cvd_sc = oi_sc = volz_sc = m30_sc = liq_sc = 0
    try:
        ema_sc = score_ema_trend(price, ema20, ema60, ema120, 'long', extension_pct)
        price_sc = score_price_position_long(rsi, bb_percent, rsi_delta)
        cvd_sc = score_cvd_trend(cvd_diff, vol_window_sum, 'long')
        oi_sc = score_oi_v4(price_chg, oi_change_pct, 'long')
        volz_sc = score_volz_v4(vol_z)
        m30_sc = score_chg30m_long(chg_30m)
        liq_sc = score_liquidity_filter(atr_pct, vol_24h_m)
    except Exception:
        pass
    raw = ema_sc + price_sc + cvd_sc + oi_sc + volz_sc + m30_sc + liq_sc
    final = round(raw / TOTAL_SCORE_WEIGHT * 100)
    return max(0, min(final, 100))

def calculate_short_score(rsi, bb_percent, cvd_diff, vol_window_sum, ls_ratio, oi_change_pct, chg_30m,
                           price_chg, extension_pct, vol_z=0.0, rsi_delta=0.0,
                           atr_pct=0.0, ema20=None, ema60=None, oi_notional_usd=None,
                           funding_rate=0.0, trade_value_usd=None,
                           price=None, ema120=None, vol_24h_m=0):
    """85점 만점 숏 점수 (롱과 대칭). 최종 /85×100 환산. (v5, 데이터 기반 세분화)
    (calculate_long_score와 동일하게 하드필터로 인한 강제 0점 처리는 제거됨)"""
    ema_sc = price_sc = cvd_sc = oi_sc = volz_sc = m30_sc = liq_sc = 0
    try:
        ema_sc = score_ema_trend(price, ema20, ema60, ema120, 'short', extension_pct)
        price_sc = score_price_position_short(rsi, bb_percent, rsi_delta)
        cvd_sc = score_cvd_trend(cvd_diff, vol_window_sum, 'short')
        oi_sc = score_oi_v4(price_chg, oi_change_pct, 'short')
        volz_sc = score_volz_v4(vol_z)
        m30_sc = score_chg30m_short(chg_30m)
        liq_sc = score_liquidity_filter(atr_pct, vol_24h_m)
    except Exception:
        pass
    raw = ema_sc + price_sc + cvd_sc + oi_sc + volz_sc + m30_sc + liq_sc
    final = round(raw / TOTAL_SCORE_WEIGHT * 100)
    return max(0, min(final, 100))


# ============================================================
# Pre-Pump / Pre-Short 점수 체계 (100점 만점, 매집형 개편안 v2) — "출발 전 매집 구간" 탐지
#   ① 고래매집/분산 (CVD+RSI 히든 다이버전스)   35점
#   ② 포지션 역발상 (L/S Ratio)                25점
#   ③ 가격정체 (볼린저밴드 폭 압축)             20점
#   ④ 수급선행 (VolZ, 가격 안 움직이는데 거래량만) 20점
#   ENABLE_PREPUMP_SCORE=False면 process_ticker에서 아예 호출하지 않고 0을 채운다.
#
# [원안 대비 구현 메모]
#   - 고래매집/분산: check_bullish_rsi_divergence/check_bearish_rsi_divergence로 탐지한
#     '가격 저점 유지·RSI 저점 상승'형 히든 다이버전스를 최우선으로 본다(35점).
#     다이버전스까진 아니어도 CVD 방향이 약하게 맞으면 15점, 그 외 0점.
#   - 가격정체(BB폭 압축): 문서는 "48시간 최고 BW 대비" 라고 했는데, 서버는 캔들
#     간격(1h/2h/6h/12h)이 바뀔 수 있어 "48시간"이 아니라 "최근 48개 캔들" 기준으로 했다
#     (기준봉이 1h면 결과적으로 원안과 동일).
#   - 수급선행: 원안은 5분봉 기준인데 서버엔 5분봉이 없어 30분 변동률(chg_30m_pct)로 근사.
# ============================================================

def score_divergence(divergence_detected, cvd_1h, rsi, direction):
    """
    고래매집/분산 점수(최대 35점). 진짜 히든 다이버전스(가격 저점은 유지되거나
    낮아지는데 RSI/CVD 저점은 오히려 높아지는 형태)를 탐지했으면 만점.
    다이버전스까진 아니어도 방향이 약하게 맞으면 절반 정도, 그 외 0점.
    """
    try:
        if divergence_detected:
            return 35
        if direction == 'prepump' and cvd_1h > 0 and rsi <= 55:
            return 15
        elif direction == 'preshort' and cvd_1h < 0 and rsi >= 45:
            return 15
        return 0
    except Exception:
        return 0

def score_ls_extreme(ls_ratio, direction):
    """
    포지션 역발상 점수(최대 25점). 개미들이 한쪽으로 극단적으로 쏠려있어야
    반대 방향 스퀴즈(강제청산 유발 폭등/폭락) 여력이 크다고 본다.
      prepump: ls_ratio≤0.85 → 25 (숏 오버슈팅) | 0.95~1.1 → 10 (균형) | 그 외 0
      preshort: ls_ratio≥1.15 → 25 (롱 과열)     | 0.9~1.05 → 10 (균형) | 그 외 0
    """
    if ls_ratio is None or ls_ratio <= 0:
        return 0
    if direction == 'prepump':
        if ls_ratio <= 0.85: return 25
        elif ls_ratio <= 1.10: return 10
        return 0
    else:
        if ls_ratio >= 1.15: return 25
        elif ls_ratio >= 0.90: return 10
        return 0

def score_bb_compression(bb_width_ratio):
    """
    가격정체(변동성 압축) 점수(최대 20점). 최근 48캔들 중 최댓값 대비 현재 볼린저
    밴드 폭 비율(%)이 낮을수록(압축될수록) 만점.
      ≤20% → 20 | ≤45% → 10 | >60% → 0 | 45~60%(갭 보간) → 5
    """
    try:
        if bb_width_ratio <= 20: return 20
        elif bb_width_ratio <= 45: return 10
        elif bb_width_ratio > 60: return 0
        return 5
    except Exception:
        return 0

def score_stealth_volz(chg_30m_pct, vol_z):
    """
    수급선행 VolZ 점수(최대 20점). "가격은 거의 안 움직이는데 거래량만 붙는"
    매집성 거래를 포착. |30분변동률|≤0.1%면서 VolZ≥1.5 → 만점.
    """
    try:
        a = abs(chg_30m_pct)
        if a <= 0.1 and vol_z >= 1.5: return 20
        elif a <= 0.3 and vol_z >= 1.0: return 10
        return 0
    except Exception:
        return 0

def calculate_prepump_score(chg_30m_pct, vol_z, cvd_1h, rsi, ls_ratio,
                             divergence_detected=False, bb_width_ratio=100.0):
    """Pre-Pump 총점(0~100) = 고래매집(35) + 개미숏쏠림(25) + 가격정체(20) + 수급선행(20)."""
    if not ENABLE_PREPUMP_SCORE:
        return 0
    try:
        score = (score_divergence(divergence_detected, cvd_1h, rsi, 'prepump')
                 + score_ls_extreme(ls_ratio, 'prepump')
                 + score_bb_compression(bb_width_ratio)
                 + score_stealth_volz(chg_30m_pct, vol_z))
        return max(0, min(round(score), 100))
    except Exception:
        return 0

def calculate_preshort_score(chg_30m_pct, vol_z, cvd_1h, rsi, ls_ratio,
                              divergence_detected=False, bb_width_ratio=100.0):
    """Pre-Short 총점(0~100) = 고래분산(35) + 개미롱쏠림(25) + 가격정체(20) + 수급선행(20)."""
    if not ENABLE_PREPUMP_SCORE:
        return 0
    try:
        score = (score_divergence(divergence_detected, cvd_1h, rsi, 'preshort')
                 + score_ls_extreme(ls_ratio, 'preshort')
                 + score_bb_compression(bb_width_ratio)
                 + score_stealth_volz(chg_30m_pct, vol_z))
        return max(0, min(round(score), 100))
    except Exception:
        return 0

# ── 동적 진입 컷오프 ─────────────────────────────────────────
# 점수 컷을 75로 고정하지 않고, 시장 상태(전 코인 평균 ATR%)에 따라 조정한다.
#   횡보장(평균 ATR% 낮음)  → 컷 상향 (신호 남발 방지)
#   추세장(평균 ATR% 높음)  → 컷 하향 (기회 포착)
current_min_score = MIN_SCORE  # score_updater가 매 사이클 갱신, GUI가 읽어서 색칠 기준으로 사용
macro_state = {"btc_ema_l": 0, "btc_ema_s": 0, "fng_value": None, "fng_class": ""}  # score_updater가 매 사이클 갱신

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
            all_ticker = get_all_ticker_snapshot()
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
        # Pre-Pump/Pre-Short (매집 구간 탐지). cvd_1h는 별도 API가 없어 위에서 이미 구한
        # cvd_diff(최근 CVD_WINDOW_CANDLES 캔들 변화량)를 근사치로 재사용한다.
        bull_div = check_bullish_rsi_divergence(df)
        bear_div = check_bearish_rsi_divergence(df)
        try:
            bb_width_series = (df['BB_UPPER'] - df['BB_LOWER']) / df['BB_MIDDLE'] * 100
            lookback_bw = bb_width_series.iloc[-48:] if len(bb_width_series) >= 48 else bb_width_series
            max_bw = float(lookback_bw.max())
            cur_bw = float(bb_width_series.iloc[-1])
            bb_width_ratio = (cur_bw / max_bw * 100) if max_bw > 0 else 100.0
        except Exception:
            bb_width_ratio = 100.0
        prepump_score = calculate_prepump_score(chg_30m, vz, cvd_diff, rsi_val, ls_ratio,
                                                 bull_div, bb_width_ratio)
        preshort_score = calculate_preshort_score(chg_30m, vz, cvd_diff, rsi_val, ls_ratio,
                                                   bear_div, bb_width_ratio)
        # 항목별 세부점수 (로그 분석/배점 튜닝용 — 총점과 동일한 함수로 계산)
        components = {
            "ema_l": score_ema_trend(current_price, ema20, ema60, ema120, 'long', extension_pct),
            "ema_s": score_ema_trend(current_price, ema20, ema60, ema120, 'short', extension_pct),
            "pp_l": score_price_position_long(rsi_val, bb_percent, rsi_delta),
            "pp_s": score_price_position_short(rsi_val, bb_percent, rsi_delta),
            "cvd_l": score_cvd_trend(cvd_diff, vol_window_sum, 'long'),
            "cvd_s": score_cvd_trend(cvd_diff, vol_window_sum, 'short'),
            "oi_l": score_oi_v4(price_chg, oi_change_pct, 'long'),
            "oi_s": score_oi_v4(price_chg, oi_change_pct, 'short'),
            "m30_l": score_chg30m_long(chg_30m),
            "m30_s": score_chg30m_short(chg_30m),
            "volz_sc": score_volz_v4(vz),
            "liquidity_sc": score_liquidity_filter(atr_pct, vol_million),
            # Pre-Pump/Pre-Short 세부점수 (calculate_prepump_score/calculate_preshort_score와
            # 동일 함수를 개별 호출 — PDF 리포트 등에서 구성요소별로 보고 싶을 때 쓴다)
            "div_l": score_divergence(bull_div, cvd_diff, rsi_val, 'prepump'),
            "div_s": score_divergence(bear_div, cvd_diff, rsi_val, 'preshort'),
            "lsx_l": score_ls_extreme(ls_ratio, 'prepump'),
            "lsx_s": score_ls_extreme(ls_ratio, 'preshort'),
            "bb_comp_sc": score_bb_compression(bb_width_ratio),
            "stealth_sc": score_stealth_volz(chg_30m, vz),
        }
        # v5 하드필터 통과 여부(방향별) — 서버는 더 이상 이걸로 점수를 0으로 만들지
        # 않고, 통과 여부만 같이 내려서 클라이언트 체크박스가 적용 여부를 결정한다.
        filters_ok_long = passes_v5_hard_filters(
            components["ema_l"], components["oi_l"], components["cvd_l"],
            vz, components["m30_l"], components["liquidity_sc"])
        filters_ok_short = passes_v5_hard_filters(
            components["ema_s"], components["oi_s"], components["cvd_s"],
            vz, components["m30_s"], components["liquidity_sc"])
        # 하드필터를 구성하는 5개 조건을 방향별로 쪼갠 것 — PDF/CSV에서 "어떤 조건
        # 때문에 걸렸는지" 개별적으로 확인할 수 있게 남긴다 (data_collector.py와 동일)
        filt_detail = {
            "filt_ema_oi_l": int(components["ema_l"] != 0 and components["oi_l"] >= 10),
            "filt_ema_oi_s": int(components["ema_s"] != 0 and components["oi_s"] >= 10),
            "filt_cvd_l": int(components["cvd_l"] >= 7),
            "filt_cvd_s": int(components["cvd_s"] >= 7),
            "filt_volz_ok": int(vz < 2.0),
            "filt_m30_l": int(components["m30_l"] != 0),
            "filt_m30_s": int(components["m30_s"] != 0),
            "filt_liquidity_ok": int(components["liquidity_sc"] != 0),
        }
        filt_detail["passes_all_l"] = int(filters_ok_long)
        filt_detail["passes_all_s"] = int(filters_ok_short)

        return {
            "ticker": ticker,
            "price": current_price,
            "price_usd": price_usd,
            "long_score": int(long_score),
            "short_score": int(short_score),
            "prepump_score": int(prepump_score),
            "preshort_score": int(preshort_score),
            "filters_ok_long": bool(filters_ok_long),
            "filters_ok_short": bool(filters_ok_short),
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
            "extension_pct": round(extension_pct, 3),
            "components": components,
            "filt_detail": filt_detail,
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
            global current_min_score, macro_state
            current_min_score = compute_dynamic_min_score(results)
            macro_state = compute_macro_state(results)
            results.sort(key=lambda x: x['long_score'] + x['short_score'], reverse=True)
            with score_lock:
                score_cache.clear()
                for r in results:
                    score_cache[r['ticker']] = r
            data_queue.put(results)
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
               'prepump_score', 'preshort_score', 'filters_ok_long', 'filters_ok_short',
               'rsi', 'rsi_delta', 'vol_z', 'bb_percent', 'cvd', 'cvd_diff', 'funding',
               'vol_24h_m', 'atr_pct', 'oi_change_pct', 'chg_30m', 'ls_ratio',
               'ema20', 'ema60', 'ema120', 'extension_pct',
               # v6 세부 컴포넌트
               'ema_l', 'ema_s', 'pp_l', 'pp_s', 'cvd_l', 'cvd_s', 'oi_l', 'oi_s',
               'm30_l', 'm30_s', 'volz_sc', 'liquidity_sc',
               # Pre-Pump/Pre-Short 세부 컴포넌트
               'div_l', 'div_s', 'lsx_l', 'lsx_s', 'bb_comp_sc', 'stealth_sc',
               # v5 하드필터 개별 통과여부
               'filt_ema_oi_l', 'filt_ema_oi_s', 'filt_cvd_l', 'filt_cvd_s', 'filt_volz_ok',
               'filt_m30_l', 'filt_m30_s', 'filt_liquidity_ok', 'passes_all_l', 'passes_all_s',
               'min_cut', 'watch_cut', 'pp_min_cut', 'interval', 'score_time', 'price_time',
               'btc_ema_l', 'btc_ema_s', 'fng_value', 'fng_class']

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
        comp = r.get('components', {}) or {}
        filt = r.get('filt_detail', {}) or {}
        rows.append([
            t, prices.get(t, r.get('price', 0)),
            r.get('price_usd', '') if r.get('price_usd') is not None else '',
            r.get('long_score', 0), r.get('short_score', 0),
            r.get('prepump_score', 0), r.get('preshort_score', 0),
            int(r.get('filters_ok_long', True)), int(r.get('filters_ok_short', True)),
            r.get('rsi', 0), r.get('rsi_delta', 0), r.get('vol_z', 0),
            r.get('bb_percent', 0), r.get('cvd', 0), r.get('cvd_diff', 0), r.get('funding', 0),
            r.get('vol_24h_m', 0), r.get('atr_pct', 0), r.get('oi_change_pct', 0),
            r.get('chg_30m', 0),
            r.get('ls_ratio', '') if r.get('ls_ratio') is not None else '',
            r.get('ema20', '') if r.get('ema20') is not None else '',
            r.get('ema60', '') if r.get('ema60') is not None else '',
            r.get('ema120', '') if r.get('ema120') is not None else '',
            r.get('extension_pct', 0),
            comp.get('ema_l', 0), comp.get('ema_s', 0), comp.get('pp_l', 0), comp.get('pp_s', 0),
            comp.get('cvd_l', 0), comp.get('cvd_s', 0), comp.get('oi_l', 0), comp.get('oi_s', 0),
            comp.get('m30_l', 0), comp.get('m30_s', 0), comp.get('volz_sc', 0), comp.get('liquidity_sc', 0),
            comp.get('div_l', 0), comp.get('div_s', 0), comp.get('lsx_l', 0), comp.get('lsx_s', 0),
            comp.get('bb_comp_sc', 0), comp.get('stealth_sc', 0),
            filt.get('filt_ema_oi_l', 1), filt.get('filt_ema_oi_s', 1),
            filt.get('filt_cvd_l', 1), filt.get('filt_cvd_s', 1), filt.get('filt_volz_ok', 1),
            filt.get('filt_m30_l', 1), filt.get('filt_m30_s', 1), filt.get('filt_liquidity_ok', 1),
            filt.get('passes_all_l', 1), filt.get('passes_all_s', 1),
            current_min_score, WATCH_MIN_SCORE, PREPUMP_MIN_SCORE, CANDLE_INTERVAL, _last_score_time[0], pt,
            macro_state.get('btc_ema_l', 0), macro_state.get('btc_ema_s', 0),
            macro_state.get('fng_value', '') if macro_state.get('fng_value') is not None else '',
            macro_state.get('fng_class', ''),
        ])
    try:
        _atomic_write_csv(MARKET_SNAPSHOT, rows)
    except Exception as e:
        print(f"마켓 스냅샷 저장 실패: {e}")

def write_account_snapshot():
    with data_lock:
        pos_snap = {t: dict(p) for t, p in positions.items()}
        prices = dict(latest_prices_usd)
    rows = [['balance', round(balance, 2)],
            ['ts', datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
            ['margin_mode', 'cross' if CROSS_MARGIN_MODE else 'isolated'],
            ['bank_balance', round(bank_balance, 2)],
            ['bank_total_deposit', round(bank_total_deposit, 2)],
            ['bank_total_spent', round(bank_total_spent, 2)],
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

def srv_open(ticker, position_type, amount_won, leverage):
    global balance
    if not ticker: return False, "티커 없음"
    if amount_won <= 0: return False, "금액이 올바르지 않습니다"
    if leverage <= 0: leverage = DEFAULT_LEVERAGE

    existing = positions.get(ticker)
    if existing and existing['position_type'] != position_type:
        cur_dir = "롱" if existing['position_type'] == "long" else "숏"
        return False, f"반대 방향({cur_dir}) 포지션이 이미 있습니다. 먼저 청산한 뒤 새로 진입하세요."

    # 거시 필터(BTC추세/공포탐욕)는 더 이상 진입을 막지 않는다 — 상단 상태줄에 정보만
    # 계속 표시하고, 진입 여부는 사람이 그 정보를 보고 직접 판단한다.

    entry_fee = (amount_won * leverage) * FEE_RATE
    total_cost = amount_won + entry_fee
    if balance < total_cost:
        return False, f"잔고 부족 (현재 ${balance:,.2f})"
    if CROSS_MARGIN_MODE:
        # 진입 직후 남는 여유현금이 유지증거금(전체 포지션 명목가치×비율)보다 적으면
        # 가격이 1도 안 움직여도(수수료/슬리피지만으로) 바로 청산되는 상황이 생긴다.
        # 그런 무리한 진입은 미리 막는다.
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
    snap = score_cache.get(ticker, {})
    if position_type == "long":
        entry_score = max(snap.get('long_score', 0), snap.get('prepump_score', 0))
    else:
        entry_score = max(snap.get('short_score', 0), snap.get('preshort_score', 0))
    direction = "롱" if position_type == "long" else "숏"
    pfmt = lambda v: f"{v:,.2f}" if v >= 1 else f"{v:,.4f}"

    if existing:
        # 바이낸스 방식 추가 매수(같은 방향으로 재진입): 평단가는 "수량 기준" 가중평균으로,
        # 증거금은 단순 합산, 레버리지는 (합산 명목가치 / 합산 증거금)의 내재값으로 재계산한다.
        # 이렇게 하면 청산가(entry_price·leverage로부터 계산됨)도 자동으로 같이 바뀐다.
        old_notional = existing['amount'] * existing['leverage']
        new_notional = amount_won * leverage
        old_qty = (old_notional / existing['entry_price']) if existing['entry_price'] > 0 else 0.0
        new_qty = (new_notional / fill_price) if fill_price > 0 else 0.0
        combined_qty = old_qty + new_qty
        combined_notional = old_notional + new_notional
        combined_margin = existing['amount'] + amount_won
        avg_entry_price = (combined_notional / combined_qty) if combined_qty > 0 else fill_price
        combined_leverage = max(1, int(round(combined_notional / combined_margin))) if combined_margin > 0 else leverage

        existing['entry_price'] = avg_entry_price
        existing['amount'] = combined_margin
        existing['leverage'] = combined_leverage
        existing['entry_fee'] = existing.get('entry_fee', 0) + entry_fee
        existing['entry_score'] = entry_score  # 마지막 추가매수 시점 점수로 갱신 (직전 값은 덮어씀)
        # entry_time은 최초 진입 시각을 그대로 유지한다 (포지션 "시작 시점"의 의미를 보존)

        trade_history.append({'type': '추가매수', 'ticker': ticker, 'direction': direction,
                              'amount': amount_won, 'leverage': leverage, 'entry_price': fill_price,
                              'exit_price': 0, 'pnl': 0, 'pnl_rate_pct': 0,
                              'entry_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'exit_time': '',
                              'entry_score': entry_score})
        save_to_csv()
        return True, (f"{ticker} {direction} 추가매수 @ ${pfmt(fill_price)} → "
                      f"평단가 ${pfmt(avg_entry_price)}, 증거금 ${combined_margin:,.2f}, "
                      f"레버리지 {combined_leverage}x로 재계산됨")

    positions[ticker] = {
        "entry_price": fill_price, "amount": amount_won, "leverage": leverage,
        "position_type": position_type, "entry_time": datetime.now(), "entry_fee": entry_fee,
        "entry_score": entry_score,
    }
    trade_history.append({'type': '진입', 'ticker': ticker, 'direction': direction,
                          'amount': amount_won, 'leverage': leverage, 'entry_price': fill_price,
                          'exit_price': 0, 'pnl': 0, 'pnl_rate_pct': 0,
                          'entry_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'exit_time': '',
                          'entry_score': entry_score})
    save_to_csv()
    return True, f"{ticker} {direction} 진입 @ ${pfmt(fill_price)}"

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
              'exit_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
              'entry_score': pos.get('entry_score', 0)}
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
              'exit_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
              'entry_score': pos.get('entry_score', 0)}
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
              'exit_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
              'entry_score': pos.get('entry_score', 0)}
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
            ticker = row[2].strip().upper() if len(row) > 2 else ''
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
    _check_single_instance()
    import atexit
    atexit.register(_release_instance_lock)
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
        _release_instance_lock()
        print("\n서버 종료 (잔고/포지션 저장됨)")
