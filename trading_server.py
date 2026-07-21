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
import json
import io
import matplotlib
matplotlib.use("Agg")  # 헤드리스(서버/Termux) 환경 — GUI 백엔드 없이 이미지만 렌더링
import matplotlib.pyplot as plt
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
SLIPPAGE_RATE = 0.007  # [2026-07-21 조정] 0.3%→0.7% — 실제 바이낸스 체결과 비교해보니 0.3%는 낮았음
# 크로스 마진 모드: 바이낸스 크로스 마진처럼, 개별 포지션 손실이 그 포지션에 배정한
# 증거금(amount)의 100%를 넘어도(-100% 초과) 계좌 전체 잔고가 버텨주는 한 그 포지션 하나만
# 따로 강제청산하지 않는다. 계좌 총자산(현금+미실현손익 합)이 바닥날 때만 청산한다.
# False로 두면 기존처럼 포지션별 격리마진(90% 손실 시 그 포지션만 강제청산) 방식으로 동작한다.
# [2026-07-21 변경] True(크로스)일 때는 페이퍼계좌 여유잔고가 넉넉하면 포지션 하나가 -50~80%
# 손실이 나도 안 죽어서, 실제 바이낸스 화면의 Liq. Price(포지션별로 진입가 대비 일정 %에서
# 뜨는 값)와 체감 청산거리가 크게 벌어지는 문제가 있었다(실측 비교: 20배 기준 실제 바이낸스
# 갭 5.12%인데 크로스모드 시뮬레이션은 여유잔고 때문에 훨씬 넓게 나옴). 포지션별로 바이낸스
# 화면 속 Liq. Price와 비슷한 거리에서 청산되게 격리마진으로 전환.
CROSS_MARGIN_MODE = False
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
ALLOWED_INTERVALS = ["1h", "2h", "6h", "12h"]   # 클라이언트 버튼으로 전환 가능한 계산 기준 캔들("기준봉")
CHART_INTERVALS = ["10m", "30m", "1h", "2h", "6h", "12h"]  # 포지션/티커 차트 팝업 전용(계산 기준봉과는 별개)
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
data_queue_upbit = _NullQueue()
price_queue_upbit = _NullQueue()
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
latest_chg24h = {}  # 빗썸 24시간 변동률(%) 캐시 — price_updater가 벌크조회 김에 같이 채움(업비트는 _latest_upbit_ticker_info에서 바로 계산)
latest_prices_usd = {}   # 바이낸스 선물 체결가(USD). 거래/손익/청산 전부 이 가격 기준(거래소 무관 공유).
latest_prices_upbit = {}  # 업비트 KRW 현재가(코인탭 표시/차트 기준가용, 거래체결가로는 안 씀)
_latest_upbit_ticker_info = {}  # market("KRW-BTC") -> 업비트 ticker API 원본(24h거래대금 등, price_updater_upbit가 갱신)
data_lock = threading.Lock()
score_lock = threading.Lock()
score_cache = {}
score_cache_upbit = {}

# 최근 가격 히스토리 (30분/1시간 등 단기 변동률 계산용). price_updater가 PRICE_INTERVAL(2초)마다
# 채워준다. 약 70분치를 보관해두면 30분/1시간 변동률을 캔들 fetch 없이 바로 구할 수 있다.
PRICE_HISTORY_MAXLEN = int(70 * 60 / PRICE_INTERVAL)
price_history = defaultdict(lambda: deque(maxlen=PRICE_HISTORY_MAXLEN))
price_history_upbit = defaultdict(lambda: deque(maxlen=PRICE_HISTORY_MAXLEN))  # 업비트 전용(빗썸과 같은 티커라도 섞이면 안 됨)
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
_discord_alerted = {'bithumb': set(), 'upbit': set()}  # {(ticker,'long'|'short'),...} — 지금 컷을 넘어있는 것들(거래소별)
_discord_last_alert_time = {}  # (exchange, ticker, direction) -> 마지막 알림 보낸 시각
_DISCORD_ALERT_COOLDOWN = 300  # 같은 코인+방향은 이 시간(초) 안에는 재알림 안 함(경계 플래핑 스팸 방지)

def render_candle_chart_png(df, ticker, n=48):
    """
    최근 n개 1시간봉을 간단한 캔들차트 PNG로 렌더링한다(mplfinance 등 추가
    의존성 없이 matplotlib만으로 직접 그린다 — Termux에 이미 있는 라이브러리만 사용).
    """
    d = df.tail(n)
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=110)
    width = 0.6
    for i, (_, row) in enumerate(d.iterrows()):
        up = row['close'] >= row['open']
        color = '#e03131' if up else '#1971c2'  # 국내 관례: 상승 빨강 / 하락 파랑
        ax.plot([i, i], [row['low'], row['high']], color=color, linewidth=1)
        lower = min(row['open'], row['close'])
        height = abs(row['close'] - row['open'])
        if height <= 0:
            height = row['high'] * 0.0008  # 시가=종가(도지)일 때도 얇게라도 보이게
        ax.add_patch(plt.Rectangle((i - width / 2, lower), width, height, color=color))
    step = max(1, len(d) // 8)
    ax.set_xticks(range(0, len(d), step))
    ax.set_xticklabels([d.index[i].strftime('%m/%d %H:%M') for i in range(0, len(d), step)],
                        rotation=30, ha='right', fontsize=8)
    ax.set_title(f"{ticker}  1H", fontsize=11)
    ax.set_xlim(-1, len(d))
    ax.grid(alpha=0.2)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format='png')
    plt.close(fig)
    buf.seek(0)
    return buf.read()

def send_discord_alert(message):
    """텍스트만 보내는 기본 버전(차트가 없거나 캔들 조회에 실패했을 때의 폴백).
    디스코드 웹후크 호출은 느리거나(네트워크 지연) 실패할 수 있는데, score_updater의
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

def send_discord_alert_with_chart(ticker, message, exchange='bithumb'):
    """
    [2026-07-19 추가] 전송시각 + 1시간봉 차트 첨부 버전. 캔들 조회/차트 렌더링/
    웹후크 전송을 전부 백그라운드 스레드 안에서 처리해서 메인 스코어링 루프를
    절대 블로킹하지 않는다(차트 렌더링이 네트워크 호출보다 훨씬 느릴 수 있어서
    send_discord_alert처럼 POST만 스레드로 빼는 걸로는 부족함).
    exchange='upbit'이면 업비트 캔들로 차트를 그린다(빗썸 심볼과 겹쳐도 정확한 소스로).
    """
    if not DISCORD_WEBHOOK_URL:
        return
    def _fire():
        try:
            if exchange == 'upbit':
                df = fetch_candlestick_upbit(ticker, chart_intervals="1h", timeout=8, retries=1)
            else:
                df = fetch_candlestick(ticker, chart_intervals="1h", timeout=8, retries=1)
            image_bytes = None
            if df is not None and len(df) >= 5:
                try:
                    image_bytes = render_candle_chart_png(df, ticker)
                except Exception as e:
                    print(f"[{ticker}] 차트 렌더링 실패: {e}")
            if image_bytes:
                payload = json.dumps({"content": message})
                files = {"file": (f"{ticker}_1h.png", image_bytes, "image/png")}
                requests.post(DISCORD_WEBHOOK_URL, data={"payload_json": payload}, files=files, timeout=15)
            else:
                requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=8)
        except Exception as e:
            print(f"디스코드 알림(차트) 실패: {e}")
    threading.Thread(target=_fire, daemon=True).start()

def check_discord_alerts(results, min_score, exchange='bithumb'):
    """score_updater 한 사이클이 끝날 때마다 호출(빗썸/업비트 각각). 이번에 새로 컷을
    넘은 코인은 (디스코드 웹후크 설정 여부와 무관하게) signal_outcomes.csv에 항상
    거래소 태그와 함께 기록해서 나중에 자동 가중치 재학습에 쓴다. 디스코드 알림
    자체는 웹후크가 설정된 경우에만, 거래소별로 독립된 쿨다운을 적용해서 보낸다."""
    now_over = set()
    for r in results:
        t = r.get('ticker', '')
        if r.get('long_score', 0) >= min_score:
            now_over.add((t, 'long'))
        if r.get('short_score', 0) >= min_score:
            now_over.add((t, 'short'))

    alerted_set = _discord_alerted.setdefault(exchange, set())
    newly_over = now_over - alerted_set
    now = time.time()
    regime = current_market_regime if exchange == 'bithumb' else current_market_regime_upbit
    for t, direction in newly_over:
        r = next((x for x in results if x['ticker'] == t), None)
        if not r:
            continue
        log_signal_open(t, direction, r, regime, exchange=exchange)  # 학습용 로그 — 웹후크 유무와 무관
        if not DISCORD_WEBHOOK_URL:
            continue
        key = (exchange, t, direction)
        last = _discord_last_alert_time.get(key, 0)
        if now - last < _DISCORD_ALERT_COOLDOWN:
            continue  # 컷 경계에서 방금 왔다갔다한 것 — 스팸 방지로 건너뜀
        score = r.get('long_score' if direction == 'long' else 'short_score', 0)
        emoji = "🟢" if direction == 'long' else "🔴"
        label = "롱" if direction == 'long' else "숏"
        ex_label = "빗썸" if exchange == 'bithumb' else "업비트"
        sent_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        msg = f"{emoji} **[{ex_label}] {t}** {label} 진입컷 돌파 ({score}점, 컷 {min_score}점)\n🕐 {sent_at}"
        send_discord_alert_with_chart(t, msg, exchange=exchange)
        _discord_last_alert_time[key] = now

    alerted_set.clear()
    alerted_set.update(now_over)
    _discord_alerted[exchange] = alerted_set

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
def record_price_history(updates, ts=None, exchange='bithumb'):
    """price_updater가 매 폴링마다 호출해서 (시각, 가격)을 ticker별로 누적한다.
    exchange='upbit'이면 별도 저장소(price_history_upbit)를 써서 같은 심볼("BTC")이
    두 거래소에서 겹쳐도 서로 섞이지 않는다."""
    store = price_history_upbit if exchange == 'upbit' else price_history
    ts = ts or time.time()
    with price_history_lock:
        for ticker, price in updates.items():
            if price and price > 0:
                store[ticker].append((ts, price))

def get_recent_pct_change(ticker, minutes=30, exchange='bithumb'):
    """
    자체 수집한 price_history에서 약 `minutes`분 전 대비 현재가 변동률(%)을 구한다.
    캔들 fetch 없이 price_updater가 이미 모아둔 데이터를 재사용하므로 API 호출이 추가로 들지 않는다.
    앱을 막 시작해서 히스토리가 부족하면 0.0을 반환한다.
    """
    store = price_history_upbit if exchange == 'upbit' else price_history
    with price_history_lock:
        hist = store.get(ticker)
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
TOTAL_SCORE_WEIGHT = 89  # [2026-07-19 재조정] 가격위치(RSI중심) 40 + EMA 10 + CVD 8 + OI 10(숏전용)
                          # + VolZ 8 + 모멘텀 8 + 유동성 5 = 89(숏 기준, 롱은 OI 없어 79가 실질상한)
                           # 롱 점수는 이 중 OI(15점)를 안 받지만, 분모는 그대로 105를 쓴다 —
                           # 분모를 90으로 줄이면 남은 점수들이 상대적으로 부풀어서 진입컷을
                           # 넘는 코인이 오히려 127개->345개로 늘어나는 부작용이 실측으로
                           # 확인됐다(과열 캡의 효과가 재정규화로 상쇄돼버림). 분모를 그대로
                           # 두면 OI를 안 받는 만큼 롱 점수 상한이 자연스럽게 ~86점으로
                           # 낮아지면서 진입컷 비교가 원래 기준과 계속 맞는다.

def score_ema_trend(price, ema20, ema60, ema120, direction, ema20_slope_pct=0.0):
    """
    EMA 삼중(20/60/120) 정배열 점수(최대 20점). [2026-07-19 세분화] 단순 배열만 보면
    "정배열이면 거의 다 만점"이라 초입/막바지 구분이 안 되는 문제가 있어, EMA20 기울기
    (직전 몇 캔들 대비 변화율)를 같이 봐서 5단계로 나눴다.
      롱: 정배열(20>60>120) + 기울기 뚜렷한 양(+0.05%↑) → 20 (강한 초입/지속)
          정배열이나 기울기 미미                        → 16 (이미 평평해지는 중)
          20>60만(60/120 무관)                          → 12 (부분 정배열)
          가격>EMA20뿐(정배열 아님)                      → 6
          그 외(가격<EMA60 등)                           → 0
      숏: 대칭(기울기 음수 방향).
    """
    try:
        if price is None or ema20 is None or ema60 is None or ema120 is None:
            return 0
        if direction == 'long':
            if ema20 > ema60 > ema120:
                return 20 if ema20_slope_pct > 0.05 else 16
            elif ema20 > ema60:
                return 12
            elif price > ema20:
                return 6
            return 0
        else:
            if ema20 < ema60 < ema120:
                return 20 if ema20_slope_pct < -0.05 else 16
            elif ema20 < ema60:
                return 12
            elif price < ema20:
                return 6
            return 0
    except Exception:
        return 0

def score_price_position_long(rsi, bb_percent, rsi_delta=0.0):
    """
    가격위치 점수(최대 40점, [2026-07-19 전면개편] "돌파추격형"→"저가매수형"으로 철학 교체).
    44,051행 실측 검증: RSI 원값 단독 상관계수가 -0.072로 v3 어느 합성점수보다 강했다
    (RSI 높을수록 이후 수익률 낮음 = 평균회귀 우세 장세). 기존엔 RSI 55~70(모멘텀 구간)을
    최고점으로 줬는데 이게 오히려 실측과 반대 방향이었다 — 그래서 과매도+반등시작을
    최고점으로 뒤집었다. 비중도 20→40으로 크게 키움(가장 강력한 단일신호이므로).
      40점: RSI≤30 & %B≤30 & 이미 반등 시작(rsi_delta>0) — 과매도 바닥+반등 확인
      30점: RSI≤35 & 반등 시작
      20점: RSI≤40
      10점: RSI 40~55 (중립)
       5점: RSI 55~80
       0점: RSI≥80 (과매수 — 롱 진입으로 최악 구간, 실측으로 확인됨)
    """
    try:
        if rsi <= 30 and bb_percent <= 30 and rsi_delta > 0:
            return 40
        elif rsi <= 35 and rsi_delta > 0:
            return 30
        elif rsi <= 40:
            return 20
        elif rsi <= 55:
            return 10
        elif rsi < 80:
            return 5
        return 0
    except Exception:
        return 0

def score_price_position_short(rsi, bb_percent, rsi_delta=0.0):
    """가격위치 숏 점수(최대 40점, 롱과 대칭 — 과매수+반락시작을 최고점으로).
    [2026-07-19 전면개편] 이전엔 RSI 30~45(약한 과매도, "하락 초입") 구간을 최고점으로
    줬는데, 이것도 실측과 반대 방향이었다(short_score 기존 corr +0.067로 부호 자체가
    반대 — "하락 초입으로 본 구간"이 실제로는 상승 신호였음). 명확한 과매수(RSI≥70)
    + 반락 시작을 최고점으로 교체."""
    try:
        if rsi >= 70 and bb_percent >= 70 and rsi_delta < 0:
            return 40
        elif rsi >= 65 and rsi_delta < 0:
            return 30
        elif rsi >= 60:
            return 20
        elif rsi >= 45:
            return 10
        elif rsi > 20:
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
    CVD 추세 점수(최대 15점). [2026-07-19 세분화] cvd_diff를 거래량(vol_window_sum) 대비
    비율로 정규화해서 "방향 일치 여부"만이 아니라 "얼마나 강하게 일치하는지"까지 5단계로
    반영한다(기존 2%는 방향판정 기준선eps로 계속 쓰던 값이라 그대로 경계로 유지).
    """
    if vol_window_sum <= 0:
        return 5  # 판단 불가 — 중립
    want = 1 if direction == 'long' else -1
    aligned_ratio = (cvd_diff / vol_window_sum) * want  # 원하는 방향 기준 정렬된 강도
    if aligned_ratio >= 0.15: return 15
    elif aligned_ratio >= 0.08: return 12
    elif aligned_ratio >= 0.02: return 8
    elif aligned_ratio >= -0.02: return 5
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
    """VolZ 거래량 점수(최대 15점, 롱/숏 공통). [2026-07-19 세분화] 3단계→6단계로 촘촘하게."""
    try:
        if vol_z >= 3.5: return 15
        elif vol_z >= 2.5: return 13
        elif vol_z >= 2.0: return 11
        elif vol_z >= 1.5: return 8
        elif vol_z >= 1.2: return 5
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

def score_combo_adjustment(direction, pp_score, cvd_score, oi_change_pct, volz_score, ema_score_opposite=0):
    """
    [2026-07-19 추가] 조합(콤보) 보정. 44,051+7,496행(총 36,843건 유효) 실측: 가격위치가
    강세(20점 이상, 새 RSI중심 로직 기준)일 때 다른 지표와 "같이" 확인되는지/"충돌"하는지
    별도로 체크하면, 단순 합산으로는 못 잡는 승률 차이가 뚜렷했다.

    보너스(실측 승률, pp단독 대비):
      롱 pp강세+OI감소(-1%이하): 48.0%→67.1%(n=243) — 반등신호+숏커버(매도압력 완화) 확인
      롱 pp강세+CVD동조: 48.0%→48.9%(n=2940) — 약하지만 일관되게 소폭 개선
      롱 pp강세+VolZ강세: 평균수익 0.08→0.155(n=559) — 승률보다 수익폭 개선
      숏 pp강세+OI증가(3%이상): 52.9%→81.7%(n=60) — 과매수+신규숏유입/롱청산압박
      숏 pp강세+CVD동조+OI증가(3중): 52.9%→88.9%(n=27, 표본 적음 주의) — 최강 조합

    페널티(실측 승률, pp단독 대비):
      숏 pp강세+EMA가 여전히 롱강세(EMA_l≥15): 52.9%→45.3%(n=1186), 평균수익도 -0.05→+0.04로
      역전 — "과매수인데 추세는 여전히 위"인 경우 숏 신호가 실제로는 잘 안 맞았다.
      (롱 쪽은 대칭되는 뚜렷한 충돌 페널티가 실측에서 확인되지 않아 넣지 않음 — 데이터
      없는 곳에 억지로 대칭 만들지 않는다는 원칙 유지.)

    pp_score/cvd_score/volz_score는 레짐·학습 배수 적용 "전" 원점수를 넣어야 임계값이 맞는다.
    """
    adj = 0
    try:
        strong_pp = pp_score >= 20
        if not strong_pp:
            return 0
        if direction == 'long':
            if oi_change_pct is not None and oi_change_pct <= -1:
                adj += 15
            if cvd_score >= 15:
                adj += 5
            if volz_score >= 10:
                adj += 5
        else:
            oi_up = oi_change_pct is not None and oi_change_pct >= 3
            if oi_up:
                adj += 15
                if cvd_score >= 15:
                    adj += 10  # 3중 조합 추가 보너스(위 doctring 88.9% 근거)
            if ema_score_opposite >= 15:
                adj -= 15
        return adj
    except Exception:
        return 0

def score_extension_penalty(extension_pct, direction):
    """
    [2026-07-19 추가, 강도 상향] 이미 추세가 상당부분 끝난 뒤 뒤늦게 쫓아가며 진입컷을
    넘기는 문제를 막는 페널티. extension_pct(calculate_extension_pct — 직전 10개 캔들
    동안 가격이 이미 얼마나 움직였는지)는 원래 이 목적으로 만들어졌던 지표인데, 실제
    calculate_long/short_score 계산식에는 한 번도 반영이 안 돼있던 걸 발견해서 추가함
    (디스코드 실측: QTUM이 직전 1시간에만 +9% 오른 직후에 롱 진입컷을 넘긴 사례로 확인).
    44,051행 실측에서 recent_pct(유사 지표) 단독 상관계수가 -0.046로, v3 어느 합성점수
    보다 강한 2번째 신호였다(1위는 RSI, 위의 score_price_position_* 참고) — 그래서
    페널티 강도를 대폭 올렸다(-15/-8/-3 → -25/-15/-5). 롱은 이미 많이 오른 경우, 숏은
    이미 많이 내린 경우 감점한다. 레짐/학습 배수와 무관하게 항상 고정폭으로 깎는다.
    """
    try:
        e = extension_pct if extension_pct is not None else 0.0
        if direction == 'long':
            if e >= 10: return -25
            elif e >= 6: return -15
            elif e >= 4: return -5
            return 0
        else:
            if e <= -10: return -25
            elif e <= -6: return -15
            elif e <= -4: return -5
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
    maxes = [(ema_pts, 10), (pp_pts, 40), (cvd_pts, 8), (oi_pts, 10),
             (m30_pts, 8), (volz_pts, 8), (liq_pts, 5)]
    n_maxed = sum(1 for v, mx in maxes if v >= mx * 0.9)
    if n_maxed >= 5:
        return min(final_score, 45)
    return final_score

def calculate_long_score(rsi, bb_percent, cvd_diff, vol_window_sum, ls_ratio, oi_change_pct, chg_30m,
                          price_chg, extension_pct, vol_z=0.0, rsi_delta=0.0,
                          atr_pct=0.0, ema20=None, ema60=None, oi_notional_usd=None,
                          funding_rate=0.0, trade_value_usd=None,
                          price=None, ema120=None, vol_24h_m=0, regime='normal', exchange='bithumb',
                          ema20_slope_pct=0.0):
    """
    [2026-07-19 전면재조정] 실질 최대 79점(89점 만점 배점 중 EMA10+가격위치(RSI중심)40+
    CVD8+VolZ8+모멘텀8+유동성5) 롱 점수. OI(oi_sc)는 일부러 뺐다 — 59시간 실측으로 OI
    급증이 롱보다 오히려 하락과 상관관계가 있는 걸 확인해서(숏 점수엔 그대로 유지,
    calculate_short_score 참고). 44,051행 검증으로 EMA/CVD/VolZ/모멘텀은 신호가 약하고
    (|corr|<0.03) RSI가 압도적으로 강한 신호(corr -0.072, v3 어느 합성점수보다 강함)임을
    확인해서 배점을 가격위치(RSI중심)로 집중시켰다(20→40, 나머지는 대폭 축소).
    분모는 TOTAL_SCORE_WEIGHT(89)를 써서 /89×100 환산한다. 롱 점수 실질 상한은 자연히
    ~89점(79/89)이 된다.
    regime: detect_market_regime()이 매긴 현재 시장 상태('상승장'/'하락장'/'횡보장'/'고변동성'/'normal').
    exchange: 'bithumb'/'upbit' — learned_component_weights는 거래소별로 따로 학습되므로
    반드시 그 값이 계산된 원본 거래소를 넘겨야 한다(안 넘기면 기본값 'bithumb' 사용).
    REGIME_WEIGHT_MULTIPLIERS(상식 기반)와 learned_component_weights(실측 신호 로그 기반 자동
    학습, analyze_and_update_weights 참고)를 곱해서 각 세부항목에 적용한다.
    """
    raw = 0
    mult = REGIME_WEIGHT_MULTIPLIERS.get(regime, REGIME_WEIGHT_MULTIPLIERS['normal'])
    lw = learned_component_weights.get(exchange, learned_component_weights['bithumb'])['long']
    try:
        # 배수 2개(레짐 x 학습)가 곱해지면 최대 1.3*1.6=2.08배까지 커질 수 있어, 원래
        # 배점 상한을 넘지 않게 min()으로 잘라준다(과도한 인플레이션 방지).
        # [2026-07-19 재배점] EMA/CVD/VolZ/모멘텀은 실측 신호가 약해(|corr|<0.03) 배점을
        # 축소(원래 함수의 배점 스케일에 비례배분: 20→10, 15→8, 15→8, 15→8). 가격위치는
        # 함수 자체가 이미 40점 만점으로 재설계됨(score_price_position_long 참고).
        raw_pp = score_price_position_long(rsi, bb_percent, rsi_delta)  # 조합보정 임계값은 이 원점수 기준
        raw_cvd = score_cvd_trend(cvd_diff, vol_window_sum, 'long')
        raw_volz = score_volz_v3(vol_z)
        p_ema = min(score_ema_trend(price, ema20, ema60, ema120, 'long', ema20_slope_pct) * 0.5 * mult['ema'] * lw['ema'], 10)
        p_pp = min(raw_pp * mult['pp'] * lw['pp'], 40)
        p_cvd = min(raw_cvd * (8 / 15) * mult['cvd'] * lw['cvd'], 8)
        p_volz = min(raw_volz * (8 / 15) * mult['volz'] * lw['volz'], 8)
        p_m30 = min(score_chg30m_long(chg_30m) * (8 / 15) * mult['m30'] * lw['m30'], 8)
        p_liq = min(score_liquidity_filter(atr_pct, vol_24h_m) * mult['liq'] * lw['liq'], 5)
        penalty = score_extension_penalty(extension_pct, 'long')
        combo = score_combo_adjustment('long', raw_pp, raw_cvd, oi_change_pct, raw_volz)
        raw = p_ema + p_pp + p_cvd + p_volz + p_m30 + p_liq + penalty + combo
        raw = max(raw, 0)
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
                           price=None, ema120=None, vol_24h_m=0, regime='normal', exchange='bithumb',
                           ema20_slope_pct=0.0):
    """89점 만점 숏 점수([2026-07-19 전면재조정] 롱과 대칭, 과열 상한 캡도 동일 적용). 최종 /89×100 환산.
    ema_s(EMA 완전 역배열)는 59시간 실측으로 120~240분 후 오히려 가격이 반등하는 경향이
    확인돼서(완전히 다 떨어진 뒤라는 뜻으로 해석) 다운그레이드한다 — "부분 역배열"과
    "완전 역배열"을 더 이상 구분해서 보너스 주지 않는다.
    regime: calculate_long_score와 동일한 시장상태별(REGIME_WEIGHT_MULTIPLIERS) +
    실측 자동학습(learned_component_weights, exchange별로 따로 학습) 가중치를 적용한다."""
    raw = 0
    mult = REGIME_WEIGHT_MULTIPLIERS.get(regime, REGIME_WEIGHT_MULTIPLIERS['normal'])
    lw = learned_component_weights.get(exchange, learned_component_weights['bithumb'])['short']
    try:
        # [2026-07-19 재배점] EMA/CVD/OI/VolZ/모멘텀은 실측 신호가 약해(|corr|<0.03) 배점을
        # 축소. 가격위치(RSI중심, score_price_position_short)가 이제 40점으로 핵심 항목.
        raw_pp = score_price_position_short(rsi, bb_percent, rsi_delta)  # 조합보정 임계값은 이 원점수 기준
        raw_cvd = score_cvd_trend(cvd_diff, vol_window_sum, 'short')
        raw_ema_l = score_ema_trend(price, ema20, ema60, ema120, 'long', ema20_slope_pct)  # 반대방향 EMA — 충돌 페널티 체크용
        p_ema = score_ema_trend(price, ema20, ema60, ema120, 'short', ema20_slope_pct)
        if p_ema >= 20:
            p_ema = 10
        p_ema = min(p_ema * 0.5 * mult['ema'] * lw['ema'], 10)
        p_pp = min(raw_pp * mult['pp'] * lw['pp'], 40)
        p_cvd = min(raw_cvd * (8 / 15) * mult['cvd'] * lw['cvd'], 8)
        p_oi = min(score_oi_v3(oi_change_pct) * (10 / 15) * mult['oi'] * lw['oi'], 10)
        p_volz = min(score_volz_v3(vol_z) * (8 / 15) * mult['volz'] * lw['volz'], 8)
        p_m30 = min(score_chg30m_short(chg_30m) * (8 / 15) * mult['m30'] * lw['m30'], 8)
        p_liq = min(score_liquidity_filter(atr_pct, vol_24h_m) * mult['liq'] * lw['liq'], 5)
        penalty = score_extension_penalty(extension_pct, 'short')
        combo = score_combo_adjustment('short', raw_pp, raw_cvd, oi_change_pct, None, ema_score_opposite=raw_ema_l)
        raw = p_ema + p_pp + p_cvd + p_oi + p_volz + p_m30 + p_liq + penalty + combo
        raw = max(raw, 0)
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

# ============================================================
# [2026-07-19 개편] 37,608행(40개 코인, 5분 간격, 7/14~7/19) 실측 로그를 바탕으로
# 각 서브컴포넌트를 60분/120분 후 실제 가격수익률과 상관분석한 결과, 기존 배점이
# 큰 항목(OI지속 25, CVD누적 20, EMA압축/과이격 15)일수록 예측력이 거의 없거나
# 부호가 반대였고, 배점이 작았던 항목(최근상승패널티 5, RSI 5)이 오히려 유의미한
# 신호였다. 이에 따라 배점을 실측 상관계수 크기에 비례해 재분배했다.
#   - prepump_score corr(60m)=-0.028 / corr(120m)=-0.035 → 재개편 전, 총점 자체가
#     의도(매집→상승)와 반대 방향이었음
#   - preshort_score corr(60m)=+0.028 / corr(120m)=+0.022 → 총점이 의도(분산→하락)와 반대
#   - 매집: 최근상승패널티(corr +0.054~+0.073, 급등 후 10~15%p 구간 60분뒤 평균 -0.22%)가
#     가장 유효 → 배점 5→25 확대. OI지속/CVD누적은 거의 무의미(|corr|<0.02) → 25→8, 20→10 축소.
#     EMA압축은 오히려 역방향(compression일수록 저조, corr -0.06~-0.09)이라 15→12로 축소.
#   - 분산: RSI 과열(RSI≥70)이 압도적으로 유효(corr -0.06, n=597, 60분뒤 평균 -0.25%,
#     120분뒤 -0.39%) → 배점 5→35 대폭 확대. OI불일치는 완전히 반대 방향(OI 감소군이
#     오히려 최저수익률) → 25→8 대폭 축소. EMA과이격도 역방향(추세지속, corr +0.06~+0.08)
#     → 15→8 축소.
#   표본이 4~5일 구간(주로 상승장 성격)에 한정돼 있어 레짐이 바뀌면 재검증 필요 —
#   특히 EMA/OI 계열의 "역방향" 신호는 방향을 완전히 뒤집기보다 배점만 낮춰
#   과최적화 위험을 줄였다.
# ============================================================

def score_oi_persistence(oi_change_pct, direction):
    """
    OI 지속 증가 점수(매집 8점 / 분산 8점, 2026-07-19 재조정 — 실측 |corr|<0.02로
    거의 무의미해 배점 대폭 축소, 원래 순위는 유지).
    """
    try:
        if direction == 'prepump':
            if oi_change_pct >= 10: return 8
            elif oi_change_pct >= 7: return 7
            elif oi_change_pct >= 5: return 6
            elif oi_change_pct >= 3: return 4
            elif oi_change_pct >= 1: return 3
            elif oi_change_pct >= 0: return 1
            return 0
        else:
            if oi_change_pct < 0: return 8
            elif oi_change_pct < 1: return 4
            return 0
    except Exception:
        return 0

def score_cvd_cumulative(cvd_1h, direction, chg_30m_pct=0.0):
    """
    CVD 누적 증가 점수(매집 10점 / 분산 12점, 2026-07-19 재조정 — 실측상 신호가
    약해(|corr|<0.03) 원래 배점(20)에서 축소).
    (cvd_1h는 별도 API가 없어 CVD_WINDOW_CANDLES 구간 변화량을 근사치로 쓴다.)
    """
    try:
        if direction == 'prepump':
            if cvd_1h > 0:
                if cvd_1h >= 100000: base = 10
                elif cvd_1h >= 30000: base = 9
                elif cvd_1h >= 5000: base = 7
                else: base = 4
                if -0.3 <= chg_30m_pct <= 0.3:
                    base += 2   # 가격 횡보 + CVD 증가
                elif chg_30m_pct < -0.3:
                    base += 3   # 가격 하락 + CVD 증가 (더 강한 매집 신호)
                return min(base, 10)
            return 0
        else:
            if cvd_1h < 0:
                if cvd_1h <= -100000: return 12
                elif cvd_1h <= -30000: return 10
                elif cvd_1h <= -5000: return 8
                return 4
            return 0
    except Exception:
        return 0

def score_ema_compression(ema20, ema60, ema120, direction):
    """
    EMA 압축도 점수(매집 12점 / 분산 8점, 2026-07-19 재조정 — 실측상 두 방향 모두
    원래 가정과 반대 부호였다(매집: 압축일수록 저조 corr -0.06~-0.09 / 분산: 이격
    클수록 오히려 상승 지속 corr +0.06~+0.08). 완전히 뒤집기엔 표본기간(4~5일)이
    짧아 방향은 유지하되 배점만 낮췄고, 매집 쪽 역배열(aligned_down) 구간에는
    실측 평균수익률이 가장 좋았던 점을 반영해 소폭의 기본점수를 부여했다.
    """
    spread = _ema_spread_pct(ema20, ema60, ema120)
    if spread is None:
        return 0
    aligned_up = ema20 > ema60 > ema120
    aligned_down = ema20 < ema60 < ema120
    if direction == 'prepump':
        if spread <= 0.3: return 12          # 거의 겹침 — 매집 최적 구간(가정)
        elif aligned_up and spread <= 1.0: return 10   # 약한 정배열 시작
        elif aligned_up: return 6             # 완전 정배열 — 이미 매집 끝난 상태
        elif aligned_down: return 3           # 역배열(실측상 평균수익률 최고 구간 — 소폭 반영)
        return 2                              # 과도한 이격(방향 불명)
    else:
        if spread >= 3.0: return 8            # 과도한 이격 — 분산/과열(가정)
        elif spread >= 1.5: return 5
        elif spread <= 0.3: return 1          # 압축 상태는 분산 신호로는 약함
        return 3

def score_atr_state(atr_pct, direction):
    """ATR(변동성) 점수(매집 8점 / 분산 8점, 2026-07-19 재조정 — 실측 신호 약함(노이즈성)."""
    try:
        if direction == 'prepump':
            if 1.0 <= atr_pct <= 2.0: return 8
            elif 0.5 <= atr_pct < 1.0: return 6
            elif 2.0 < atr_pct <= 3.0: return 4
            elif atr_pct > 3.0: return 0
            return 2   # 0.5% 미만 — 거의 죽어있음, 낮은 점수
        else:
            if atr_pct >= 4.0: return 8
            elif atr_pct >= 3.0: return 5
            return 0

    except Exception:
        return 0

def score_volz_state(vol_z, direction):
    """
    거래량(VolZ) 점수(매집 8점 / 분산 8점, 2026-07-19 재조정 — 실측 신호 약함(노이즈성).
    """
    try:
        if direction == 'prepump':
            if 0.5 <= vol_z <= 1.2: return 8
            elif 1.2 < vol_z <= 2.0: return 6
            elif 0.2 <= vol_z < 0.5: return 5
            elif 2.0 < vol_z <= 3.0: return 2
            elif vol_z > 3.0: return 0
            return 2   # 0.2 미만 — 관심도 자체가 없음
        else:
            if vol_z >= 3.0: return 8
            elif vol_z >= 2.0: return 4
            return 0
    except Exception:
        return 0

def score_box_position(current_price, box_high, box_low, direction):
    """
    가격 위치 점수(매집 17점 / 분산 15점, 2026-07-19 재조정 — 실측상 매집 방향은
    모노토닉하게 유효(바닥권일수록 60/120분뒤 수익률 높음, corr +0.02~+0.03)해
    배점을 확대했다. 분산 방향은 신호가 약해 원 배점 유지 수준으로만 조정.
    """
    try:
        if box_high is None or box_low is None or box_high <= box_low:
            return 0
        pos_pct = (current_price - box_low) / (box_high - box_low) * 100
        if direction == 'prepump':
            if pos_pct <= 25: return 17
            elif pos_pct <= 50: return 13
            elif pos_pct <= 80: return 8
            return 0
        else:
            if pos_pct >= 80: return 15
            elif pos_pct >= 60: return 8
            return 0
    except Exception:
        return 0

def score_rsi_box(rsi, direction):
    """
    RSI 점수(매집 12점 / 분산 35점, 2026-07-19 재조정 — 분산 방향의 RSI≥70 과열
    신호가 실측 데이터에서 가장 강력하고 일관됐다(corr -0.06, n=597, 60분뒤 평균
    -0.25%/120분뒤 -0.39%). 원래 배점(5)이 총점에 묻혀 있던 걸 대폭 확대(35).
    """
    try:
        if direction == 'prepump':
            if 45 <= rsi <= 60: return 12
            elif 40 <= rsi < 45: return 10
            elif 60 < rsi <= 70: return 7
            elif 30 <= rsi < 40: return 5
            return 0
        else:
            if rsi >= 70: return 35
            elif rsi >= 60: return 21
            return 0
    except Exception:
        return 0

def score_recent_move(recent_pct, direction):
    """
    최근 상승률 점수(매집: 급등 패널티 25점 / 분산: 급등 보너스 6점, 2026-07-19
    재조정). 매집 방향은 실측에서 가장 유효했던 신호(corr +0.05~+0.07, 이미
    10~15% 급등한 종목은 60분뒤 평균 -0.22%)라 배점을 5→25로 대폭 확대했다.
    분산 방향은 표본이 거의 없어(급등 15%+ 케이스 희소) 원 배점 수준 유지.
    """
    try:
        if direction == 'prepump':
            if recent_pct <= 3: return 25
            elif recent_pct <= 7: return 15
            elif recent_pct <= 10: return 10
            elif recent_pct <= 15: return 5
            return 0
        else:
            if recent_pct >= 15: return 6
            elif recent_pct >= 10: return 4
            return 0
    except Exception:
        return 0

def calculate_prepump_score(oi_change_pct, cvd_1h, ema20, ema60, ema120, atr_pct, vol_z,
                             current_price, box_high, box_low, rsi, recent_pct, chg_30m_pct=0.0):
    """매집 총점(0~100, 2026-07-19 재조정) = OI지속(8)+CVD누적(10)+EMA압축(12)+ATR(8)+VolZ(8)+가격위치(17)+RSI(12)+최근상승패널티(25)."""
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
    """분산 총점(0~100, 2026-07-19 재조정) = OI감소·불일치(8)+CVD지속감소(12)+EMA과이격(8)+ATR급증(8)+VolZ폭발(8)+신고가부근(15)+RSI70+(35)+최근급등(6)."""
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
current_min_score_upbit = MIN_SCORE  # 업비트 파이프라인 전용(빗썸과 독립적으로 시장상태에 따라 갱신)

# ============================================================
# [2026-07-19 추가] 시장 상태별 자동 가중치 조정 (v8 6단계)
#   주의: 아래 배수는 데이터로 최적화한 값이 아니라 "상승장엔 추세추종 지표를,
#   횡보장엔 평균회귀 지표를 더 본다"는 상식적 방향성만 반영한 1차 버전이다.
#   지난 실측(37,608행/32코인/4.5일)에서 v3 세부점수 개별 상관계수가 전부
#   |corr|<0.07로 약해서, 이 레짐 배수가 실제로 승률을 개선하는지는 아직
#   검증되지 않았다 — 로그가 몇 주치 더 쌓이면 detect_market_regime이 매긴
#   레짐별로 실제 forward return을 다시 갈라서 배수 자체를 재조정해야 한다.
#   배수는 항상 1.0 근처(0.7~1.3)로 제한해 레짐 판정이 잘못돼도 점수가
#   과하게 튀지 않게 했다.
# ============================================================
REGIME_WEIGHT_MULTIPLIERS = {
    #             ema   pp    cvd   oi    volz  m30   liq
    '상승장':   {'ema': 1.3, 'pp': 1.0, 'cvd': 1.1, 'oi': 0.9, 'volz': 0.9, 'm30': 1.1, 'liq': 1.0},
    '하락장':   {'ema': 1.1, 'pp': 1.0, 'cvd': 1.3, 'oi': 1.1, 'volz': 0.9, 'm30': 1.0, 'liq': 1.0},
    '횡보장':   {'ema': 0.7, 'pp': 1.3, 'cvd': 0.9, 'oi': 0.8, 'volz': 1.0, 'm30': 0.7, 'liq': 1.2},
    '고변동성': {'ema': 0.9, 'pp': 0.9, 'cvd': 1.0, 'oi': 1.0, 'volz': 1.3, 'm30': 0.8, 'liq': 1.0},
    'normal':   {'ema': 1.0, 'pp': 1.0, 'cvd': 1.0, 'oi': 1.0, 'volz': 1.0, 'm30': 1.0, 'liq': 1.0},
}
current_market_regime = 'normal'  # score_updater가 매 사이클 끝에 갱신, 다음 사이클 점수계산에 반영(1사이클 지연)
current_market_regime_upbit = 'normal'  # 업비트 전용

def detect_market_regime(results):
    """
    전체 코인 평균 ATR%(변동성)와 EMA 정배열/역배열 비율(추세 방향)로 시장 상태를
    4가지로 분류한다. compute_dynamic_min_score와 같은 입력(results)을 재사용한다.
    """
    try:
        atrs = [r.get('atr_pct', 0) for r in results if r.get('atr_pct', 0) > 0]
        if not atrs:
            return 'normal'
        avg_atr = sum(atrs) / len(atrs)

        bull = bear = 0
        for r in results:
            price = r.get('price'); e20 = r.get('ema20'); e60 = r.get('ema60'); e120 = r.get('ema120')
            if price is None or e20 is None or e60 is None or e120 is None:
                continue
            if e20 > e60 > e120 and price > e20:
                bull += 1
            elif e20 < e60 < e120 and price < e20:
                bear += 1
        total = bull + bear
        trend_bias = (bull - bear) / total if total > 0 else 0.0  # +1(전부 상승정배열) ~ -1(전부 하락역배열)

        if avg_atr >= 2.0:
            return '고변동성'
        elif trend_bias >= 0.15:
            return '상승장'
        elif trend_bias <= -0.15:
            return '하락장'
        return '횡보장'
    except Exception:
        return 'normal'

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
# [2026-07-19 추가] v8 — 실측 승률 기반 자동 가중치 학습 시스템
#   "점수표를 사람이 정하는 게 아니라 데이터가 정하게" 하는 요청을 반영한 구현.
#   흐름: 진입컷을 넘는 신호 발생 → signal_outcomes.csv에 그 순간 세부점수 스냅샷
#   기록(log_signal_open) → 15분마다 120분 이상 지난 미해결 신호를 1시간봉으로 다시
#   조회해 60분/120분 수익률·MFE·MAE 채움(resolve_signal_outcomes) → 해결된 신호가
#   충분히 쌓이면(SIGNAL_MIN_TOTAL) 컴포넌트별 상관관계로 배수 재계산
#   (analyze_and_update_weights) → learned_weights.json에 저장, calculate_long/
#   short_score가 다음 계산부터 자동 반영.
#
#   과최적화 방지 안전장치 (지난 대화에서 지적한 표본 부족/레짐 편중 문제 때문에 필수):
#     1) 표본 부족(<SIGNAL_MIN_SAMPLES_PER_COMPONENT) 컴포넌트는 배수를 안 건드림(유지)
#     2) 상관관계 부호가 반대로 나온(역신호로 확인된) 컴포넌트는 무조건 최소배수(0.5)로—
#        부호를 자동으로 뒤집어서 "매수신호였던 걸 매도신호로" 재해석하지는 않음
#     3) 한 번 재학습에 배수가 이전 값 대비 ±0.25 이상 못 움직임(급변 방지, 완만한 적응)
#     4) 전체 재학습은 해결된(=60/120분 뒤 결과가 이미 확정된) 신호만 사용 — 워크포워드,
#        미래 데이터 참조 없음
#     5) 재학습마다 weight_retrain_log.csv에 근거(상관계수/표본수/변경폭)를 남겨서
#        나중에 "왜 이 배수로 바뀌었는지" 추적 가능
#   그래도 근본적인 한계는 남는다 — 신호 자체가 드물게 나오는 구조라(진입컷 넘는
#   경우만 기록) SIGNAL_MIN_TOTAL(300건)까지 쌓이는 데도 실제로는 몇 주 걸릴 수 있고,
#   그 기간이 특정 장세에 쏠려 있으면 여전히 그 장세에 과적합된 배수가 나온다.
# ============================================================
SIGNAL_LOG_FILE = os.path.join(SCRIPT_DIR, "signal_outcomes.csv")
LEARNED_WEIGHTS_FILE = os.path.join(SCRIPT_DIR, "learned_weights.json")
WEIGHT_RETRAIN_LOG_FILE = os.path.join(SCRIPT_DIR, "weight_retrain_log.csv")
SIGNAL_LOG_COLS = ["signal_id", "opened_ts", "exchange", "ticker", "direction", "entry_price", "score", "regime",
                    "ema", "pp", "cvd", "oi", "volz", "m30", "liq",
                    "resolved", "resolve_ts", "ret_60m", "ret_120m", "mfe_pct", "mae_pct"]

LONG_COMPONENTS = ['ema', 'pp', 'cvd', 'volz', 'm30', 'liq']
SHORT_COMPONENTS = ['ema', 'pp', 'cvd', 'oi', 'volz', 'm30', 'liq']
EXCHANGES = ['bithumb', 'upbit']  # [2026-07-19 추가] 업비트 병행 — signal_outcomes.csv 한 파일에
                                  # exchange 컬럼으로 구분해서 같이 쌓고, 배수는 거래소별로 따로 학습한다.

SIGNAL_MIN_TOTAL = 300                  # 이 이상 '해결된' 신호가(전체, 두 거래소 합산) 쌓여야 재학습 시작
SIGNAL_MIN_SAMPLES_PER_COMPONENT = 30   # 거래소×방향×컴포넌트별 최소 표본(부족하면 배수 유지)
WEIGHT_MULT_MIN, WEIGHT_MULT_MAX = 0.5, 1.6
WEIGHT_MULT_MAX_STEP = 0.25             # 한 번 재학습에 배수가 움직일 수 있는 최대폭
RESOLVE_INTERVAL_SEC = 900              # 15분마다 미해결 신호 결과 확인 + 재학습 여부 체크
RESOLVE_AFTER_MIN = 120                 # 신호 발생 후 이만큼 지나야 결과 확정 시도

_signal_log_lock = threading.Lock()

def _load_signal_log():
    """resolved 컬럼이 CSV 왕복하면서 문자열("True"/"False")이 되는 문제를 정규화해서 읽는다.
    exchange 컬럼이 없는 구버전 로그(업비트 추가 전에 쌓인 것)는 전부 'bithumb'으로 채운다."""
    df = pd.read_csv(SIGNAL_LOG_FILE)
    if 'resolved' in df.columns:
        df['resolved'] = df['resolved'].astype(str).str.strip().eq('True')
    if 'exchange' not in df.columns:
        df['exchange'] = 'bithumb'
    else:
        df['exchange'] = df['exchange'].fillna('bithumb')
    return df

def migrate_signal_log_schema():
    """
    [2026-07-19 추가] signal_outcomes.csv의 실제 헤더가 지금 코드의 SIGNAL_LOG_COLS와
    다르면(예: exchange 컬럼이 나중에 추가됐는데 기존 파일 헤더는 옛날 그대로인 경우)
    자동으로 재정렬해서 다시 쓴다.

    이게 왜 필요하냐면: log_signal_open()은 파일이 "존재하기만 하면" 헤더를 다시 안 쓴다
    (file_exists면 writeheader() 생략). 그래서 컬럼 구성이 바뀐 뒤에도 옛날 헤더가 계속
    남아있는 채로 새 스키마의 값이 추가되면, 헤더는 20개인데 값은 21개인 식으로 밀려서
    쌓이는 문제가 실제로 발생했다(exchange 컬럼 없던 신호 2건 + 있는 신호 6건이 섞여있었음).
    서버 시작 시 한 번 호출해서 기존 파일을 항상 지금 스키마로 맞춰둔다.
    """
    if not os.path.exists(SIGNAL_LOG_FILE):
        return
    try:
        with open(SIGNAL_LOG_FILE, newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            rows = list(reader)
        if not rows:
            return
        header = rows[0]
        if header == SIGNAL_LOG_COLS:
            return  # 이미 최신 스키마 — 손댈 필요 없음
        print(f"⚠️ signal_outcomes.csv 스키마가 예전 버전입니다 — 자동 정렬합니다 "
              f"(기존 헤더 {len(header)}컬럼 → 현재 {len(SIGNAL_LOG_COLS)}컬럼)")
        n = len(SIGNAL_LOG_COLS)
        fixed = []
        for row in rows[1:]:
            if len(row) == n:
                fixed.append(row)  # 이미 새 스키마 순서 — 헤더만 예전 것이었을 뿐
            elif len(row) == n - 1:
                # exchange 컬럼이 통째로 없던 구버전 — signal_id/opened_ts 바로 뒤(인덱스 2)에
                # 'bithumb'을 끼워넣는다(그 시절엔 빗썸밖에 없었으므로).
                fixed.append(row[:2] + ['bithumb'] + row[2:])
            else:
                print(f"  스키마 자동 정렬 실패(알 수 없는 컬럼 수 {len(row)}), 원본 유지: {row[:2]}")
                fixed.append(row)
        with _signal_log_lock:
            with open(SIGNAL_LOG_FILE, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(SIGNAL_LOG_COLS)
                w.writerows(fixed)
        print(f"✅ signal_outcomes.csv 스키마 정렬 완료 ({len(fixed)}행)")
    except Exception as e:
        print(f"signal_outcomes.csv 스키마 정렬 실패: {e}")

def log_signal_open(ticker, direction, r, regime, exchange='bithumb'):
    """진입컷을 새로 넘은 신호를 기록한다(결과는 나중에 resolve_signal_outcomes가 채움)."""
    try:
        comp = r.get('components', {}) or {}
        signal_id = f"{exchange}_{ticker}_{direction}_{int(time.time() * 1000)}"
        row = {
            "signal_id": signal_id,
            "opened_ts": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "exchange": exchange,
            "ticker": ticker, "direction": direction,
            "entry_price": r.get('price', 0),
            "score": r.get('long_score' if direction == 'long' else 'short_score', 0),
            "regime": regime,
            "ema": comp.get('ema_l' if direction == 'long' else 'ema_s', 0),
            "pp": comp.get('pp_l' if direction == 'long' else 'pp_s', 0),
            "cvd": comp.get('cvd_l' if direction == 'long' else 'cvd_s', 0),
            "oi": comp.get('oi_sc', 0) if direction == 'short' else 0,
            "volz": comp.get('volz_sc', 0),
            "m30": comp.get('m30_l' if direction == 'long' else 'm30_s', 0),
            "liq": comp.get('liquidity_sc', 0),
            "resolved": False, "resolve_ts": "", "ret_60m": "", "ret_120m": "", "mfe_pct": "", "mae_pct": "",
        }
        with _signal_log_lock:
            file_exists = os.path.exists(SIGNAL_LOG_FILE)
            with open(SIGNAL_LOG_FILE, 'a', newline='', encoding='utf-8') as f:
                w = csv.DictWriter(f, fieldnames=SIGNAL_LOG_COLS)
                if not file_exists:
                    w.writeheader()
                w.writerow(row)
    except Exception as e:
        print(f"신호 로그 기록 실패: {e}")

def resolve_signal_outcomes():
    """opened_ts로부터 RESOLVE_AFTER_MIN분 이상 지난 미해결 신호에 1시간봉을 다시
    조회해서 60분/120분 후 수익률 + MFE/MAE(방향 기준으로 부호 정리)를 채운다.
    거래소별로 캔들 조회 함수를 다르게 써야 해서 (exchange, ticker) 쌍으로 묶는다."""
    if not os.path.exists(SIGNAL_LOG_FILE):
        return 0
    try:
        with _signal_log_lock:
            df = _load_signal_log()
        if df.empty:
            return 0
        df['opened_ts'] = pd.to_datetime(df['opened_ts'])
        now = datetime.now()
        pending_mask = (~df['resolved']) & ((now - df['opened_ts']).dt.total_seconds() >= RESOLVE_AFTER_MIN * 60)
        if not pending_mask.any():
            return 0
        newly_resolved = 0
        pairs = df.loc[pending_mask, ['exchange', 'ticker']].drop_duplicates()
        for exch, ticker in pairs.itertuples(index=False):
            fetch_fn = fetch_candlestick_upbit if exch == 'upbit' else fetch_candlestick
            candle = fetch_fn(ticker, chart_intervals="1h", timeout=8, retries=1)
            if candle is None or len(candle) < 3:
                continue
            idxs = df.index[pending_mask & (df['ticker'] == ticker) & (df['exchange'] == exch)]
            for idx in idxs:
                try:
                    row = df.loc[idx]
                    entry_time = row['opened_ts']
                    entry_price = float(row['entry_price'])
                    if entry_price <= 0:
                        continue
                    window = candle[(candle.index >= entry_time - pd.Timedelta(minutes=30)) &
                                     (candle.index <= entry_time + pd.Timedelta(minutes=150))]
                    if len(window) < 2:
                        continue
                    t60 = window[window.index >= entry_time + pd.Timedelta(minutes=45)]
                    t120 = window[window.index >= entry_time + pd.Timedelta(minutes=105)]
                    ret60 = (t60.iloc[0]['close'] - entry_price) / entry_price * 100 if len(t60) else None
                    ret120 = (t120.iloc[0]['close'] - entry_price) / entry_price * 100 if len(t120) else None
                    if ret60 is None and ret120 is None:
                        continue  # 아직 그 시점 캔들이 안 나옴 — 다음 주기에 재시도
                    post = window[window.index >= entry_time]
                    mfe = (post['high'].max() - entry_price) / entry_price * 100 if len(post) else None
                    mae = (post['low'].min() - entry_price) / entry_price * 100 if len(post) else None
                    if row['direction'] == 'short' and mfe is not None and mae is not None:
                        mfe, mae = -mae, -mfe  # 숏은 가격 하락이 이득 — MFE/MAE 부호를 방향 기준으로 뒤집음
                    df.loc[idx, 'ret_60m'] = ret60
                    df.loc[idx, 'ret_120m'] = ret120
                    df.loc[idx, 'mfe_pct'] = mfe
                    df.loc[idx, 'mae_pct'] = mae
                    df.loc[idx, 'resolved'] = True
                    df.loc[idx, 'resolve_ts'] = now.strftime('%Y-%m-%d %H:%M:%S')
                    newly_resolved += 1
                except Exception:
                    continue
        if newly_resolved:
            with _signal_log_lock:
                df.to_csv(SIGNAL_LOG_FILE, index=False)
        return newly_resolved
    except Exception as e:
        print(f"신호 결과 해소 실패: {e}")
        return 0

def _load_learned_weights():
    default = {ex: {'long': {c: 1.0 for c in LONG_COMPONENTS}, 'short': {c: 1.0 for c in SHORT_COMPONENTS}}
               for ex in EXCHANGES}
    try:
        if os.path.exists(LEARNED_WEIGHTS_FILE):
            with open(LEARNED_WEIGHTS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if 'long' in data or 'short' in data:
                # 업비트 추가 전(거래소 구분 없던 시절) 파일 — 그 값을 빗썸 값으로 이어받는다
                default['bithumb']['long'].update(data.get('long', {}))
                default['bithumb']['short'].update(data.get('short', {}))
            else:
                for ex in EXCHANGES:
                    if ex in data:
                        default[ex]['long'].update(data[ex].get('long', {}))
                        default[ex]['short'].update(data[ex].get('short', {}))
    except Exception as e:
        print(f"학습 가중치 로드 실패, 기본값(1.0) 사용: {e}")
    return default

learned_component_weights = _load_learned_weights()  # calculate_long/short_score가 매번 참조하는 현재 배수(거래소별)

def analyze_and_update_weights():
    """resolved==True인 신호들의 컴포넌트-수익률 상관관계로, 거래소×방향별 배수를 재계산한다.
    반환값: 하나라도 갱신됐으면 새 가중치 dict, 전부 표본 부족이면 None."""
    global learned_component_weights
    if not os.path.exists(SIGNAL_LOG_FILE):
        return None
    try:
        df = _load_signal_log()
        df = df[df['resolved']].dropna(subset=['ret_60m'])
        if len(df) < SIGNAL_MIN_TOTAL:
            print(f"[자동 재학습] 보류: 해결된 신호 {len(df)}건(전체 거래소 합산, 최소 {SIGNAL_MIN_TOTAL}건 필요)")
            return None

        report = {}
        any_update = False
        new_weights = {ex: {'long': dict(learned_component_weights[ex]['long']),
                             'short': dict(learned_component_weights[ex]['short'])} for ex in EXCHANGES}

        for exchange in EXCHANGES:
            edf = df[df['exchange'] == exchange]
            for direction, components in (('long', LONG_COMPONENTS), ('short', SHORT_COMPONENTS)):
                sub = edf[edf['direction'] == direction]
                if len(sub) < SIGNAL_MIN_SAMPLES_PER_COMPONENT:
                    continue
                aligned_corrs = {}
                for c in components:
                    n_valid = sub[c].notna().sum()
                    if n_valid < SIGNAL_MIN_SAMPLES_PER_COMPONENT:
                        aligned_corrs[c] = None
                        continue
                    corr = sub[c].corr(sub['ret_60m'])
                    # 숏은 가격 하락(음의 수익률)이 좋은 신호라 부호를 뒤집어서 "방향 정렬"한다
                    aligned = corr if direction == 'long' else -corr
                    aligned_corrs[c] = 0.0 if pd.isna(aligned) else aligned

                positive = {c: v for c, v in aligned_corrs.items() if v is not None and v > 0}
                total_pos = sum(positive.values()) or 1e-9
                n = len(components)
                for c in components:
                    old = learned_component_weights[exchange][direction].get(c, 1.0)
                    v = aligned_corrs.get(c)
                    if v is None:
                        target = old                              # 표본 부족 — 그대로 유지
                    elif v <= 0:
                        target = WEIGHT_MULT_MIN                   # 역방향 확인됨 — 최소배수로
                    else:
                        share = v / total_pos
                        target = max(WEIGHT_MULT_MIN, min(WEIGHT_MULT_MAX, share * n))
                    step = max(-WEIGHT_MULT_MAX_STEP, min(WEIGHT_MULT_MAX_STEP, target - old))
                    new_val = round(old + step, 3)
                    new_weights[exchange][direction][c] = new_val
                    report[f"{exchange}_{direction}_{c}"] = {"corr": None if v is None else round(v, 4),
                                                              "n": int(sub[c].notna().sum()),
                                                              "old_mult": old, "new_mult": new_val}
                any_update = True

        if not any_update:
            print("[자동 재학습] 보류: 거래소×방향별 최소 표본(각 30건)을 채운 조합이 아직 없음")
            return None

        learned_component_weights = new_weights
        try:
            with open(LEARNED_WEIGHTS_FILE, 'w', encoding='utf-8') as f:
                json.dump(new_weights, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"학습 가중치 저장 실패: {e}")
        try:
            file_exists = os.path.exists(WEIGHT_RETRAIN_LOG_FILE)
            with open(WEIGHT_RETRAIN_LOG_FILE, 'a', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                if not file_exists:
                    w.writerow(["ts", "n_resolved", "detail_json"])
                w.writerow([datetime.now().strftime('%Y-%m-%d %H:%M:%S'), len(df),
                            json.dumps(report, ensure_ascii=False)])
        except Exception as e:
            print(f"재학습 로그 기록 실패: {e}")

        print(f"[자동 재학습] 해결신호 {len(df)}건 기준 배수 갱신 완료: {new_weights}")
        return new_weights
    except Exception as e:
        print(f"자동 재학습 실패: {e}")
        return None

def weight_learning_loop():
    """RESOLVE_INTERVAL_SEC(기본 15분)마다 미해결 신호를 해소하고, 새로 해결된 신호가
    50건 이상 늘었으면 재학습한다(=매번 하지 않음, 불필요한 재계산/급변 방지).
    빗썸/업비트 신호가 signal_outcomes.csv 한 파일에 같이 쌓이므로 이 루프도 하나면 된다.
    [2026-07-19 버그수정] 진행상황 로그가 analyze_and_update_weights() 안에만 있었는데,
    그 함수 자체가 '이미 300건 넘었을 때만' 호출되는 구조라 300건 채우기 전에는 아무 로그도
    안 찍히는 문제가 있었다 — 그래서 사용자가 진행상황을 전혀 볼 수 없었다. 이제 매 주기마다
    무조건 진행상황을 찍는다."""
    last_retrain_count = 0
    while running:
        try:
            newly_resolved = resolve_signal_outcomes()
            if not os.path.exists(SIGNAL_LOG_FILE):
                print(f"[자동학습] signal_outcomes.csv 아직 없음 — 진입컷을 넘은 신호가 한 번도 안 났다는 뜻")
            else:
                df = _load_signal_log()
                total_count = len(df)
                resolved_count = int(df['resolved'].sum()) if 'resolved' in df.columns else 0
                pending_count = total_count - resolved_count
                print(f"[자동학습] 전체신호 {total_count}건 (해결 {resolved_count} / 대기 {pending_count}, "
                      f"이번 주기 신규해결 {newly_resolved}) — 재학습 최소기준 {SIGNAL_MIN_TOTAL}건")
                if resolved_count >= SIGNAL_MIN_TOTAL and resolved_count - last_retrain_count >= 50:
                    if analyze_and_update_weights() is not None:
                        last_retrain_count = resolved_count
        except Exception as e:
            print(f"가중치 학습 루프 오류: {e}")
        time.sleep(RESOLVE_INTERVAL_SEC)



# ============================================================
# 단일 코인 처리
# ============================================================
BITHUMB_CANDLESTICK_URL = "https://api.bithumb.com/public/candlestick/{}_{}/{}"
BITHUMB_NATIVE_INTERVALS = {"10m", "30m", "1h", "6h", "12h"}  # 빗썸 캔들스틱 API가 직접 지원하는 간격

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

# ============================================================
# [2026-07-19 추가] 업비트 공개 API (Quotation API — 키 불필요)
#   빗썸과 별개의 두 번째 데이터소스. 코인탭에서 "업비트" 버튼을 누르면
#   이 함수들로 조회한 데이터를 같은 calculate_long_score 등 채점 함수에
#   그대로 넣어서(로직은 100% 동일) 빗썸 파이프라인과 나란히 돌린다.
#   포지션/잔고/청산은 이미 바이낸스 USD 마크가격 기준이라 거래소와
#   무관하게 그대로 공유된다 — 여기서 건드리는 건 오직 "시세/지표 조회처"뿐.
# ============================================================
UPBIT_MARKET_ALL_URL = "https://api.upbit.com/v1/market/all?isDetails=false"
UPBIT_TICKER_URL = "https://api.upbit.com/v1/ticker"
UPBIT_CANDLE_MINUTE_URL = "https://api.upbit.com/v1/candles/minutes/{unit}"
UPBIT_NATIVE_MINUTE_UNITS = {"10m": 10, "30m": 30, "1h": 60}  # 업비트가 분캔들로 직접 지원하는 것만
_upbit_market_list_cache = {"tickers": [], "ts": 0}

def _fetch_upbit_candles_raw(market, unit, count=200, timeout=10, retries=2):
    """업비트 분캔들(candles/minutes/{unit}) 원시 조회. 빗썸 쪽 _fetch_candlestick_raw와
    동일한 형식(open/high/low/close/volume, KST naive datetime index)으로 맞춰서 반환한다
    — 그래야 RSI/CVD/EMA 등 나머지 계산 코드를 손댈 필요가 없다."""
    url = UPBIT_CANDLE_MINUTE_URL.format(unit=unit)
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params={"market": market, "count": count}, timeout=timeout)
            data = resp.json()
            if not isinstance(data, list) or not data:
                return None
            df = pd.DataFrame(data)
            df = df.rename(columns={
                "opening_price": "open", "high_price": "high", "low_price": "low",
                "trade_price": "close", "candle_acc_trade_volume": "volume",
            })
            df["candle_date_time_kst"] = pd.to_datetime(df["candle_date_time_kst"])
            df = df.set_index("candle_date_time_kst")[["open", "high", "low", "close", "volume"]]
            df = df.sort_index()  # 업비트는 최신순으로 내려줘서 시간순으로 뒤집어야 함
            return df.astype(float)
        except Exception as e:
            if attempt == retries:
                print(f"[업비트:{market}] 캔들 조회 실패(재시도 {retries}회 소진): {e}")
                return None
            time.sleep(0.5)
    return None

def fetch_candlestick_upbit(ticker, chart_intervals=None, timeout=10, retries=2):
    """빗썸의 fetch_candlestick과 대칭되는 업비트 버전. ticker는 순수 심볼("BTC")을
    받아서 내부에서 "KRW-BTC"로 변환한다. 10m/30m/1h는 업비트가 직접 지원, 2h/6h/12h는
    1시간봉을 리샘플해서 합성한다(빗썸의 2h 합성과 동일한 방식)."""
    interval = chart_intervals or CANDLE_INTERVAL
    market = f"KRW-{ticker}" if not ticker.startswith("KRW-") else ticker
    if interval in UPBIT_NATIVE_MINUTE_UNITS:
        return _fetch_upbit_candles_raw(market, UPBIT_NATIVE_MINUTE_UNITS[interval], timeout=timeout, retries=retries)
    if interval in ("2h", "6h", "12h"):
        df = _fetch_upbit_candles_raw(market, 60, count=200, timeout=timeout, retries=retries)
        if df is None or len(df) < 2:
            return df
        try:
            resampled = df.resample(interval).agg({
                "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
            })
            return resampled.dropna(how="any")
        except Exception as e:
            print(f"[업비트:{ticker}] {interval} 리샘플 실패, 1h로 폴백: {e}")
            return df
    return _fetch_upbit_candles_raw(market, 60, timeout=timeout, retries=retries)

def get_upbit_tickers(limit=None):
    """업비트 원화(KRW-) 마켓 전체를 조회해서 24시간 거래대금 상위 순으로 순수 심볼
    리스트("BTC","ETH",...)를 반환한다. 1분 캐시(빗썸 ticker_updater와 같은 갱신주기 감안)."""
    now = time.time()
    if _upbit_market_list_cache["tickers"] and now - _upbit_market_list_cache["ts"] < 55:
        return _upbit_market_list_cache["tickers"]
    try:
        resp = requests.get(UPBIT_MARKET_ALL_URL, timeout=10)
        markets = [m["market"] for m in resp.json() if m.get("market", "").startswith("KRW-")]
        if not markets:
            return _upbit_market_list_cache["tickers"] or []
        # 한 번에 최대 100개까지 markets 파라미터로 묶어 조회 가능(문서 기준 넉넉히 잡음)
        vol_map = {}
        for i in range(0, len(markets), 100):
            chunk = markets[i:i + 100]
            r = requests.get(UPBIT_TICKER_URL, params={"markets": ",".join(chunk)}, timeout=10)
            for row in r.json():
                vol_map[row["market"]] = row.get("acc_trade_price_24h", 0) or 0
        markets.sort(key=lambda m: vol_map.get(m, 0), reverse=True)
        tickers = [m.replace("KRW-", "") for m in markets]
        if limit:
            tickers = tickers[:limit]
        _upbit_market_list_cache["tickers"] = tickers
        _upbit_market_list_cache["ts"] = now
        return tickers
    except Exception as e:
        print(f"업비트 마켓 목록 조회 실패: {e}")
        return _upbit_market_list_cache["tickers"] or []

def fetch_upbit_ticker_bulk(markets):
    """markets(["KRW-BTC",...]) 현재가/24h 거래대금을 한 번에 조회. 100개씩 나눠 호출."""
    out = {}
    try:
        for i in range(0, len(markets), 100):
            chunk = markets[i:i + 100]
            r = requests.get(UPBIT_TICKER_URL, params={"markets": ",".join(chunk)}, timeout=10)
            for row in r.json():
                out[row["market"]] = row
    except Exception as e:
        print(f"업비트 현재가 일괄조회 실패: {e}")
    return out


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
        # [2026-07-19 추가] 멀티타임프레임 모멘텀 블렌드(5분40%+15분30%+30분30%) — 30분모멘텀
        # 세부점수(score_chg30m_long/short) 계산에만 쓰고, chg_30m 원본은 매집/분산 점수와
        # 표시용으로 그대로 남겨둔다(그쪽은 순수 30분 기준으로 이미 검증된 값이라 안 건드림).
        chg_5m = get_recent_pct_change(ticker, minutes=5)
        chg_15m = get_recent_pct_change(ticker, minutes=15)
        momentum_blend = chg_5m * 0.4 + chg_15m * 0.3 + chg_30m * 0.3
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

        # EMA 추세 (1시간봉 기준 EMA20/60/120 삼중 정배열) + EMA20 기울기(직전 3캔들 대비 변화율,
        # 2026-07-19 추가 — score_ema_trend의 기울기 세분화에 사용)
        try:
            ema_series = df['close'].ewm(span=20, adjust=False).mean()
            ema20 = float(ema_series.iloc[-1])
            ema60 = float(df['close'].ewm(span=60, adjust=False).mean().iloc[-1])
            ema120 = float(df['close'].ewm(span=120, adjust=False).mean().iloc[-1]) if len(df) >= 30 else None
            if len(ema_series) >= 4 and ema_series.iloc[-4] != 0:
                ema20_slope_pct = (ema20 - float(ema_series.iloc[-4])) / float(ema_series.iloc[-4]) * 100
            else:
                ema20_slope_pct = 0.0
        except Exception:
            ema20 = ema60 = ema120 = None
            ema20_slope_pct = 0.0

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
            rsi_val, bb_percent, cvd_diff, vol_window_sum, ls_ratio, oi_change_pct, momentum_blend,
            price_chg, extension_pct, vz, rsi_delta, atr_pct, ema20, ema60, oi_notional_usd,
            funding_rate, trade_value_usd, current_price, ema120, vol_million, current_market_regime,
            exchange='bithumb', ema20_slope_pct=ema20_slope_pct
        )
        short_score = calculate_short_score(
            rsi_val, bb_percent, cvd_diff, vol_window_sum, ls_ratio, oi_change_pct, momentum_blend,
            price_chg, extension_pct, vz, rsi_delta, atr_pct, ema20, ema60, oi_notional_usd,
            funding_rate, trade_value_usd, current_price, ema120, vol_million, current_market_regime,
            exchange='bithumb', ema20_slope_pct=ema20_slope_pct
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
            "ema_l": score_ema_trend(current_price, ema20, ema60, ema120, 'long', ema20_slope_pct),
            "ema_s": score_ema_trend(current_price, ema20, ema60, ema120, 'short', ema20_slope_pct),
            "pp_l": score_price_position_long(rsi_val, bb_percent, rsi_delta),
            "pp_s": score_price_position_short(rsi_val, bb_percent, rsi_delta),
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


def process_ticker_upbit(ticker):
    """process_ticker의 업비트 버전. 채점 함수(calculate_long_score 등)와 OI/펀딩/LS비율
    (전부 바이낸스 선물 기준이라 거래소 무관)은 완전히 그대로 재사용하고, 캔들/현재가/
    24h거래대금/30분변동률 조회처만 업비트 공개 API로 바꿨다."""
    try:
        df = fetch_candlestick_upbit(ticker, chart_intervals=CANDLE_INTERVAL)
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
        vol_window_sum = float(df['volume'].iloc[-CVD_WINDOW_CANDLES:].sum())
        cvd_diff = cvd_value - float(df['CVD'].iloc[-2]) if len(df) >= 2 else 0.0
        bb_percent = calculate_bb_percent(df, latest)
        extension_pct = calculate_extension_pct(df)
        atr_pct = calculate_atr_1h(df)
        if MIN_ATR_PCT > 0 and atr_pct > 0 and atr_pct < MIN_ATR_PCT:
            return None
        # 단기(30분) 가격 변동률: 업비트 전용 히스토리(price_history_upbit)에서 조회
        chg_30m = get_recent_pct_change(ticker, minutes=30, exchange='upbit')
        chg_5m = get_recent_pct_change(ticker, minutes=5, exchange='upbit')
        chg_15m = get_recent_pct_change(ticker, minutes=15, exchange='upbit')
        momentum_blend = chg_5m * 0.4 + chg_15m * 0.3 + chg_30m * 0.3
        price_chg = float(df['close'].iloc[-1]) - float(df['close'].iloc[-5])

        with data_lock:
            latest_prices_upbit[ticker] = current_price

        # 펀딩레이트/USD가격/OI/LS비율은 전부 바이낸스 선물 기준(심볼만 일치하면 됨) — 거래소 무관, 그대로 재사용
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

        oi_change_pct, oi_value = get_oi_change_1h(base)
        ls_ratio = get_long_short_ratio(base)
        oi_notional_usd = (oi_value * price_usd) if (oi_value and price_usd) else None

        try:
            ema_series = df['close'].ewm(span=20, adjust=False).mean()
            ema20 = float(ema_series.iloc[-1])
            ema60 = float(df['close'].ewm(span=60, adjust=False).mean().iloc[-1])
            ema120 = float(df['close'].ewm(span=120, adjust=False).mean().iloc[-1]) if len(df) >= 30 else None
            if len(ema_series) >= 4 and ema_series.iloc[-4] != 0:
                ema20_slope_pct = (ema20 - float(ema_series.iloc[-4])) / float(ema_series.iloc[-4]) * 100
            else:
                ema20_slope_pct = 0.0
        except Exception:
            ema20 = ema60 = ema120 = None
            ema20_slope_pct = 0.0

        rsi_val = float(latest['RSI'])
        rsi_delta = rsi_val - float(df['RSI'].iloc[-5])

        # 24h 거래대금(원화, 백만원 단위) — 업비트 ticker 벌크 캐시(_latest_upbit_ticker_info)에서 조회
        vol_million = 0
        try:
            market = f"KRW-{ticker}"
            info = _latest_upbit_ticker_info.get(market)
            if isinstance(info, dict):
                acc = info.get('acc_trade_price_24h', 0) or 0
                vol_million = round(float(acc) / 1_000_000)
        except:
            pass
        trade_value_usd = None
        try:
            if price_usd and price_usd > 0 and current_price > 0:
                fx_rate = current_price / price_usd
                trade_value_usd = (vol_million * 1_000_000) / fx_rate
        except Exception:
            trade_value_usd = None

        long_score = calculate_long_score(
            rsi_val, bb_percent, cvd_diff, vol_window_sum, ls_ratio, oi_change_pct, momentum_blend,
            price_chg, extension_pct, vz, rsi_delta, atr_pct, ema20, ema60, oi_notional_usd,
            funding_rate, trade_value_usd, current_price, ema120, vol_million, current_market_regime_upbit,
            exchange='upbit', ema20_slope_pct=ema20_slope_pct
        )
        short_score = calculate_short_score(
            rsi_val, bb_percent, cvd_diff, vol_window_sum, ls_ratio, oi_change_pct, momentum_blend,
            price_chg, extension_pct, vz, rsi_delta, atr_pct, ema20, ema60, oi_notional_usd,
            funding_rate, trade_value_usd, current_price, ema120, vol_million, current_market_regime_upbit,
            exchange='upbit', ema20_slope_pct=ema20_slope_pct
        )
        try:
            box_lookback = df.iloc[-20:] if len(df) >= 20 else df
            box_high = float(box_lookback['high'].max())
            box_low = float(box_lookback['low'].min())
        except Exception:
            box_high = box_low = None
        try:
            candles_per_3d = {"1h": 72, "2h": 36, "6h": 12, "12h": 6}.get(CANDLE_INTERVAL, 72)
            ref_idx = -min(candles_per_3d, len(df) - 1) if len(df) > 1 else -1
            ref_price = float(df['close'].iloc[ref_idx])
            recent_pct = (current_price - ref_price) / ref_price * 100 if ref_price > 0 else 0.0
        except Exception:
            recent_pct = 0.0
        # 매집/분산(prepump/preshort)은 거래소 학습가중치가 없는 정적 배점이라 그대로 재사용
        prepump_score = calculate_prepump_score(oi_change_pct, cvd_diff, ema20, ema60, ema120,
                                                 atr_pct, vz, current_price, box_high, box_low,
                                                 rsi_val, recent_pct, chg_30m)
        preshort_score = calculate_preshort_score(oi_change_pct, cvd_diff, ema20, ema60, ema120,
                                                   atr_pct, vz, current_price, box_high, box_low,
                                                   rsi_val, recent_pct, chg_30m)
        components = {
            "ema_l": score_ema_trend(current_price, ema20, ema60, ema120, 'long', ema20_slope_pct),
            "ema_s": score_ema_trend(current_price, ema20, ema60, ema120, 'short', ema20_slope_pct),
            "pp_l": score_price_position_long(rsi_val, bb_percent, rsi_delta),
            "pp_s": score_price_position_short(rsi_val, bb_percent, rsi_delta),
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
            "exchange": "upbit",
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
            "cvd_diff": round(cvd_diff, 2),
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
        print(f"[업비트:{ticker}] 데이터 수집 실패: {e}")
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
            chg24h_updates = {}
            for ticker, info in price_data.items():
                if ticker == "date": continue
                try:
                    if isinstance(info, dict):
                        updates[ticker] = float(info.get('closing_price') or info.get('trade_price') or 0)
                        chg24h_updates[ticker] = float(info.get('fluctate_rate_24H', 0) or 0)
                    else:
                        updates[ticker] = float(info)
                except:
                    updates[ticker] = 0.0
            with data_lock:
                latest_prices.update(updates)
                latest_chg24h.update(chg24h_updates)
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

def price_updater_upbit(tickers_ref):
    """price_updater의 업비트 버전. latest_prices_usd(바이낸스 마크가격)는 거래소 무관 공유라
    여기서 또 갱신할 필요 없음 — bithumb 쪽 price_updater가 이미 채워준다."""
    while running:
        try:
            tickers = tickers_ref[0]
            if tickers:
                markets = [f"KRW-{t}" for t in tickers]
                bulk = fetch_upbit_ticker_bulk(markets)
                updates = {}
                for market, row in bulk.items():
                    t = market.replace("KRW-", "")
                    try:
                        updates[t] = float(row.get('trade_price', 0) or 0)
                    except Exception:
                        updates[t] = 0.0
                with data_lock:
                    latest_prices_upbit.update(updates)
                    _latest_upbit_ticker_info.update(bulk)
                record_price_history(updates, exchange='upbit')
                with score_lock:
                    if score_cache_upbit:
                        merged = [dict(row, price=latest_prices_upbit.get(t, row.get('price', 0)))
                                  for t, row in score_cache_upbit.items()]
                        merged.sort(key=lambda x: x.get('long_score', 0) + x.get('short_score', 0), reverse=True)
                        price_queue_upbit.put(merged)
        except Exception as e:
            print(f"업비트 가격 업데이트 실패: {e}")
        time.sleep(PRICE_INTERVAL)

def score_updater(tickers_ref, exchange='bithumb'):
    """exchange='bithumb'(기본)이면 기존과 완전히 동일하게 동작. exchange='upbit'이면
    같은 로직을 process_ticker_upbit/score_cache_upbit/current_min_score_upbit 등
    업비트 전용 상태로 돌린다 — 로직 자체(정렬/캐시 갱신/디스코드 체크 순서)는 100% 동일."""
    global score_cache, score_cache_upbit
    proc_fn = process_ticker if exchange == 'bithumb' else process_ticker_upbit
    # 첫 실행 전 펀딩레이트 미리 로드 (거래소 무관 공유 캐시라 한쪽만 해도 됨, 중복 호출은 내부에서 캐시로 처리)
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
            futures = {pool.submit(proc_fn, t): t for t in tickers}
            for future in as_completed(futures):
                if not running: break
                res = future.result()
                if res:
                    results.append(res)
        if results:
            results.sort(key=lambda x: x['long_score'] + x['short_score'], reverse=True)
            if exchange == 'bithumb':
                global current_min_score, current_market_regime
                current_min_score = compute_dynamic_min_score(results)
                current_market_regime = detect_market_regime(results)
                with score_lock:
                    score_cache.clear()
                    for r in results:
                        score_cache[r['ticker']] = r
                data_queue.put(results)
                try:
                    check_discord_alerts(results, current_min_score, exchange='bithumb')
                except Exception as e:
                    print(f"디스코드 알림 체크 실패: {e}")
            else:
                global current_min_score_upbit, current_market_regime_upbit
                current_min_score_upbit = compute_dynamic_min_score(results)
                current_market_regime_upbit = detect_market_regime(results)
                with score_lock:
                    score_cache_upbit.clear()
                    for r in results:
                        score_cache_upbit[r['ticker']] = r
                data_queue_upbit.put(results)
                try:
                    check_discord_alerts(results, current_min_score_upbit, exchange='upbit')
                except Exception as e:
                    print(f"디스코드 알림 체크(업비트) 실패: {e}")
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

def ticker_updater_upbit(tickers_ref):
    while running:
        try:
            tickers = get_upbit_tickers(limit=TOP_COIN_COUNT)
            if tickers:
                tickers_ref[0] = tickers
        except Exception as e:
            print(f"업비트 티커 목록 갱신 실패: {e}")
            if not tickers_ref[0]:
                tickers_ref[0] = ["BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "TRX", "AVAX", "LINK", "BNB"]
        time.sleep(60)

# ============================================================
# 서버 <-> 주문용(GUI) CSV 인터페이스
#   server_market.csv        : 빗썸 코인별 최신 지표/점수 스냅샷 (서버가 2초마다 원자적 갱신)
#   server_market_upbit.csv  : 업비트 버전 (동일 포맷)
#   server_account.csv  : 잔고/포지션/실시간 손익 스냅샷 (서버가 1초마다 갱신, 거래소 무관 공유 — 포지션은 바이낸스 USD 기준)
#   server_cmds/        : 주문용이 명령 파일(cmd_*.csv)을 떨어뜨리는 폴더
#   server_results.csv  : 명령 처리 결과 (cmd_id, status, message) 누적
# 계좌(잔고/포지션/강제청산)는 전부 서버가 관리한다. 주문용은 명령만 보낸다.
# ============================================================
MARKET_SNAPSHOT = os.path.join(SCRIPT_DIR, "server_market.csv")
MARKET_SNAPSHOT_UPBIT = os.path.join(SCRIPT_DIR, "server_market_upbit.csv")
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

MARKET_COLS = ['ticker', 'price', 'price_usd', 'chg_24h', 'long_score', 'short_score',
               'prepump_score', 'preshort_score',
               'rsi', 'rsi_delta', 'vol_z', 'bb_percent', 'cvd', 'cvd_diff', 'funding',
               'vol_24h_m', 'atr_pct', 'oi_change_pct', 'chg_30m', 'ls_ratio',
               'ema20', 'ema60',
               'min_cut', 'watch_cut', 'pp_min_cut', 'interval', 'score_time', 'price_time']

_last_score_time = [""]
_last_score_time_upbit = [""]

def write_market_snapshot(exchange='bithumb'):
    """exchange='bithumb'이면 기존과 동일하게 server_market.csv에, 'upbit'이면
    server_market_upbit.csv에 쓴다. 컬럼 포맷은 완전히 동일해서 클라이언트가
    읽는 파서(read_market_snapshot)를 그대로 재사용할 수 있다."""
    if exchange == 'bithumb':
        cache, prices, path, min_score, score_time = score_cache, latest_prices, MARKET_SNAPSHOT, current_min_score, _last_score_time[0]
    else:
        cache, prices, path, min_score, score_time = score_cache_upbit, latest_prices_upbit, MARKET_SNAPSHOT_UPBIT, current_min_score_upbit, _last_score_time_upbit[0]
    with score_lock:
        snap = {t: dict(r) for t, r in cache.items()}
    if not snap:
        return
    with data_lock:
        prices = dict(prices)
        if exchange == 'bithumb':
            chg24_map = dict(latest_chg24h)
        else:
            chg24_map = {}
            for market, info in _latest_upbit_ticker_info.items():
                try:
                    chg24_map[market.replace("KRW-", "")] = float(info.get('signed_change_rate', 0) or 0) * 100
                except Exception:
                    pass
    pt = datetime.now().strftime('%H:%M:%S')
    rows = [MARKET_COLS]
    for t, r in snap.items():
        rows.append([
            t, prices.get(t, r.get('price', 0)),
            r.get('price_usd', '') if r.get('price_usd') is not None else '',
            chg24_map.get(t, 0),
            r.get('long_score', 0), r.get('short_score', 0),
            r.get('prepump_score', 0), r.get('preshort_score', 0),
            r.get('rsi', 0), r.get('rsi_delta', 0), r.get('vol_z', 0),
            r.get('bb_percent', 0), r.get('cvd', 0), r.get('cvd_diff', 0), r.get('funding', 0),
            r.get('vol_24h_m', 0), r.get('atr_pct', 0), r.get('oi_change_pct', 0),
            r.get('chg_30m', 0),
            r.get('ls_ratio', '') if r.get('ls_ratio') is not None else '',
            r.get('ema20', '') if r.get('ema20') is not None else '',
            r.get('ema60', '') if r.get('ema60') is not None else '',
            min_score, WATCH_MIN_SCORE, PREPUMP_MIN_SCORE, CANDLE_INTERVAL, score_time, pt,
        ])
    try:
        _atomic_write_csv(path, rows)
    except Exception as e:
        print(f"마켓 스냅샷 저장 실패({exchange}): {e}")

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

def srv_reset_history():
    """청산 기록(trade_history.csv)만 비운다. 보유 중인 포지션/잔고는 절대 안 건드림 —
    positions는 이 함수가 아예 참조하지 않고, HISTORY_FILE(청산+진입로그)만 초기화한다."""
    global trade_history
    trade_history = []
    try:
        with open(HISTORY_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['type', 'ticker', 'direction', 'amount', 'leverage',
                              'entry_price', 'exit_price', 'pnl', 'pnl_rate_pct', 'entry_time', 'exit_time'])
        return True, "청산/거래 기록을 초기화했습니다 (보유 포지션은 그대로 유지됩니다)"
    except Exception as e:
        return False, f"기록 초기화 실패: {e}"

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

def srv_get_candles(ticker, interval=None, exchange='bithumb'):
    """
    포지션 카드에서 티커 이름을 클릭하면 뜨는 차트 팝업용 데이터를 만든다.
    클라이언트에 matplotlib 같은 무거운 그래픽 라이브러리를 안 얹으려고(Termux에서
    계속 패키지 설치로 고생했던 걸 감안), 서버가 캔들+지표 원본을 CSV로만 내려주고
    실제 그리기는 클라이언트가 Tkinter Canvas로 직접 한다.
    RSI/RSI Delta/EMA20·60·120/볼린저밴드를 같이 계산해서 넣어준다.
    exchange='upbit'이면 업비트 캔들로 조회한다.
    """
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return False, "티커 없음"
    interval = interval if interval in CHART_INTERVALS else CANDLE_INTERVAL
    try:
        if exchange == 'upbit':
            df = fetch_candlestick_upbit(ticker, chart_intervals=interval)
        else:
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

def srv_open(ticker, position_type, amount_won, leverage, exchange='bithumb'):
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
    snap = score_cache.get(ticker, {}) if exchange != 'upbit' else score_cache_upbit.get(ticker, {})
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

def _calc_liq_price_preview(entry_price, leverage, position_type):
    """
    [2026-07-21 추가] 격리마진 기준(배정 증거금의 90% 소진 시점) 청산가 미리보기.
    _check_isolated_liquidation()의 실제 청산 조건(pnl <= -amt*0.9)을 역산한 것과 동일한
    공식이라 화면에 보여주는 값과 실제 청산 로직이 항상 일치한다.
    CROSS_MARGIN_MODE(기본 켜짐)에서는 실제 청산 시점이 계좌 전체 상황(다른 포지션 손익
    포함)에 따라 달라지므로, 이 값은 "이 포지션 하나만 격리했을 때"의 참고용 근사치다.
    """
    try:
        if entry_price <= 0 or leverage <= 0:
            return 0.0
        LIQ_RATIO = 0.9
        if position_type == 'long':
            return entry_price * (1 - LIQ_RATIO / leverage)
        else:
            return entry_price * (1 + LIQ_RATIO / leverage)
    except Exception:
        return 0.0

def srv_open_manual(ticker, position_type, entry_price, amount_won, leverage, exchange='bithumb'):
    """
    [2026-07-21 추가] "빠른 입력" — 실제 거래소(바이낸스 등)에서 이미 체결한 포지션을
    페이퍼 계좌에 똑같이 옮겨 등록하고 싶을 때 쓴다. srv_open과 로직은 거의 동일하지만
    딱 하나 다르다: 진입가를 그 순간 라이브 시세에서 가져오는 게 아니라 사용자가 입력한
    값을 그대로 쓴다(이미 실제로 체결된 가격이므로 슬리피지도 적용 안 함). 수수료 계산·
    유지증거금 안전장치·물타기 시 가중평균 로직은 srv_open과 완전히 동일하게 적용해서
    회계가 서로 안 어긋나게 한다.
    """
    global balance
    if not ticker: return False, "티커 없음"
    existing = positions.get(ticker)
    if existing and existing.get('position_type') != position_type:
        return False, "반대 방향 포지션이 이미 있습니다 (먼저 청산 후 진입하세요)"
    if amount_won <= 0: return False, "투입금액이 올바르지 않습니다"
    if entry_price <= 0: return False, "진입가가 올바르지 않습니다"
    if leverage <= 0: leverage = DEFAULT_LEVERAGE
    entry_fee = (amount_won * leverage) * FEE_RATE
    total_cost = amount_won + entry_fee
    if balance < total_cost:
        return False, f"잔고 부족 (현재 ${balance:,.2f}, 필요 ${total_cost:,.2f} — 증거금 ${amount_won:,.2f}+수수료 ${entry_fee:,.2f})"
    if CROSS_MARGIN_MODE:
        existing_notional = sum(p.get('amount', 0) * p.get('leverage', 1) for p in positions.values())
        new_notional = amount_won * leverage
        required = (existing_notional + new_notional) * CROSS_MARGIN_MAINTENANCE_RATIO
        free_after = balance - total_cost
        if free_after < required:
            return False, (f"진입 직후 청산 위험 — 여유현금 ${free_after:,.2f}이 유지증거금 ${required:,.2f}보다 적습니다 "
                            f"(명목가치 ${new_notional:,.2f}, 유지증거금 {CROSS_MARGIN_MAINTENANCE_RATIO*100:.1f}%). "
                            f"증거금을 늘리거나 레버리지를 낮추세요.")
    fill_price = entry_price  # 라이브 시세 대신 사용자가 준 실제 체결가 그대로 사용
    balance -= total_cost
    if balance < 0: balance = 0
    snap = score_cache.get(ticker, {}) if exchange != 'upbit' else score_cache_upbit.get(ticker, {})
    if position_type == "long":
        entry_score = max(snap.get('long_score', 0), snap.get('prepump_score', 0))
    else:
        entry_score = max(snap.get('short_score', 0), snap.get('preshort_score', 0))
    direction = "롱" if position_type == "long" else "숏"

    if existing:
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
            "entry_score": existing.get('entry_score', entry_score),
        }
        liq = _calc_liq_price_preview(weighted_entry, combined_leverage, position_type)
        trade_history.append({'type': '추가진입(수동)', 'ticker': ticker, 'direction': direction,
                              'amount': amount_won, 'leverage': leverage, 'entry_price': fill_price,
                              'exit_price': 0, 'pnl': 0, 'pnl_rate_pct': 0,
                              'entry_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'exit_time': ''})
        save_to_csv()
        pfmt = f"{weighted_entry:,.4f}"
        return True, (f"{ticker} {direction} 추가진입(수동) @ ${fill_price:,.4f} "
                      f"(평균단가 ${pfmt}, 합계 ${combined_amount:,.2f}/{combined_leverage}x, "
                      f"수수료 ${entry_fee:,.2f}, 청산가(격리기준) ${liq:,.4f})")

    positions[ticker] = {
        "entry_price": fill_price, "amount": amount_won, "leverage": leverage,
        "position_type": position_type, "entry_time": datetime.now(), "entry_fee": entry_fee,
        "entry_score": entry_score,
    }
    liq = _calc_liq_price_preview(fill_price, leverage, position_type)
    trade_history.append({'type': '진입(수동)', 'ticker': ticker, 'direction': direction,
                          'amount': amount_won, 'leverage': leverage, 'entry_price': fill_price,
                          'exit_price': 0, 'pnl': 0, 'pnl_rate_pct': 0,
                          'entry_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'exit_time': ''})
    save_to_csv()
    pfmt = f"{fill_price:,.4f}"
    return True, (f"{ticker} {direction} 진입(수동) @ ${pfmt} "
                  f"(증거금 ${amount_won:,.2f}, 수수료 ${entry_fee:,.2f}, 청산가(격리기준) ${liq:,.4f})")

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
    명령 형식(1행): cmd_id,action,ticker,amount_won,leverage,position_type,exchange,entry_price
    (exchange는 [2026-07-19 추가], entry_price는 [2026-07-21 추가, open_manual 전용] —
    둘 다 없는(구버전) 명령 파일은 각각 'bithumb'/0으로 취급해 하위호환.)"""
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
            exchange = (row[6].strip().lower() if len(row) > 6 and row[6] else 'bithumb')
            if exchange not in ('bithumb', 'upbit'):
                exchange = 'bithumb'
            entry_price = float(row[7]) if len(row) > 7 and row[7] else 0.0
            if action == 'charge':
                ok, msg = srv_charge(amount)
            elif action == 'withdraw':
                ok, msg = srv_withdraw(amount)
            elif action == 'bank_deposit':
                ok, msg = srv_bank_deposit(amount)
            elif action == 'bank_withdraw':
                ok, msg = srv_bank_withdraw(amount)
            elif action == 'open':
                ok, msg = srv_open(ticker, ptype, amount, lev, exchange=exchange)
            elif action == 'open_manual':
                ok, msg = srv_open_manual(ticker, ptype, entry_price, amount, lev, exchange=exchange)
            elif action == 'close':
                ok, msg = srv_close(ticker)
            elif action == 'close_all':
                ok, msg = srv_close_all()
            elif action == 'reset':
                ok, msg = srv_reset_balance()
            elif action == 'reset_history':
                ok, msg = srv_reset_history()
            elif action == 'set_interval':
                ok, msg = srv_set_interval(ticker)
            elif action == 'set_margin_mode':
                ok, msg = srv_set_margin_mode(ticker)
            elif action == 'generate_report':
                ok, msg = srv_generate_report()
            elif action == 'set_report_dir':
                ok, msg = srv_set_report_dir(raw_ticker)
            elif action == 'get_candles':
                ok, msg = srv_get_candles(ticker, interval=ptype, exchange=exchange)
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
    """PRICE_INTERVAL 주기로 마켓 스냅샷 갱신 (가격은 최신, 지표는 마지막 스코어 사이클 값).
    빗썸/업비트 둘 다 매 주기 갱신 — 데이터가 없는 쪽(아직 첫 사이클 전)은 write_market_snapshot이
    내부에서 조용히 스킵한다."""
    while running:
        try:
            write_market_snapshot('bithumb')
            write_market_snapshot('upbit')
        except Exception as e:
            print(f"snapshot_loop 오류: {e}")
        time.sleep(PRICE_INTERVAL)

# score_updater가 돌 때 스코어 갱신 시각을 남기기 위한 래퍼
_orig_score_updater = score_updater
def score_updater(tickers_ref, exchange='bithumb'):  # noqa: F811
    def _mark():
        if exchange == 'bithumb':
            _last_score_time[0] = datetime.now().strftime('%H:%M:%S')
        else:
            _last_score_time_upbit[0] = datetime.now().strftime('%H:%M:%S')
    # 간단히: 원본을 그대로 돌리되, 스냅샷 시각은 data_queue.put 대신 여기서 주기 갱신
    def marker_loop():
        cache = score_cache if exchange == 'bithumb' else score_cache_upbit
        while running:
            with score_lock:
                if cache:
                    _mark()
            time.sleep(SCORE_INTERVAL)
    threading.Thread(target=marker_loop, daemon=True).start()
    _orig_score_updater(tickers_ref, exchange=exchange)

# ============================================================
if __name__ == "__main__":
    print("=" * 50)
    print("트레이딩 서버 시작 (계산/계좌 엔진 — 마켓 로깅 없음, 수집은 별도 기기)")
    print(f"데이터 폴더: {SCRIPT_DIR}")
    print(f"마켓 스냅샷(빗썸): {MARKET_SNAPSHOT}")
    print(f"마켓 스냅샷(업비트): {MARKET_SNAPSHOT_UPBIT}")
    print(f"명령 폴더: {CMD_DIR}")
    print("중지: Ctrl+C  (포지션은 서버가 계속 감시하므로 GUI는 꺼도 됨)")
    print("=" * 50)

    load_from_csv()
    load_history_csv()
    migrate_signal_log_schema()

    tickers_ref = [[]]
    try:
        price_data = Bithumb.get_current_price("ALL")
        tickers_ref[0] = [k for k in list(price_data.keys()) if k != "date"][:TOP_COIN_COUNT]
        print(f"빗썸 티커 {len(tickers_ref[0])}개 로드")
    except:
        tickers_ref[0] = ["BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "TRX", "AVAX", "LINK", "BNB"]

    tickers_ref_upbit = [[]]
    try:
        tickers_ref_upbit[0] = get_upbit_tickers(limit=TOP_COIN_COUNT)
        print(f"업비트 티커 {len(tickers_ref_upbit[0])}개 로드")
    except Exception as e:
        print(f"업비트 티커 목록 최초 로드 실패({e}), ticker_updater_upbit가 재시도함")
        tickers_ref_upbit[0] = ["BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "TRX", "AVAX", "LINK", "BNB"]

    threading.Thread(target=ticker_updater, args=(tickers_ref,), daemon=True).start()
    threading.Thread(target=price_updater, args=(tickers_ref,), daemon=True).start()
    threading.Thread(target=score_updater, args=(tickers_ref,), kwargs={'exchange': 'bithumb'}, daemon=True).start()
    threading.Thread(target=ticker_updater_upbit, args=(tickers_ref_upbit,), daemon=True).start()
    threading.Thread(target=price_updater_upbit, args=(tickers_ref_upbit,), daemon=True).start()
    threading.Thread(target=score_updater, args=(tickers_ref_upbit,), kwargs={'exchange': 'upbit'}, daemon=True).start()
    threading.Thread(target=weight_learning_loop, daemon=True).start()
    threading.Thread(target=snapshot_loop, daemon=True).start()
    threading.Thread(target=account_loop, daemon=True).start()

    try:
        last_status = 0
        while True:
            time.sleep(5)
            if time.time() - last_status >= 60:
                with score_lock:
                    n = len(score_cache)
                    n_upbit = len(score_cache_upbit)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 서버 가동 | 빗썸 {n}개(컷{current_min_score}/{current_market_regime}) | "
                      f"업비트 {n_upbit}개(컷{current_min_score_upbit}/{current_market_regime_upbit}) | "
                      f"잔고 ${balance:,.2f} | 포지션 {len(positions)}개")
                last_status = time.time()
    except KeyboardInterrupt:
        running = False
        save_to_csv()
        print("\n서버 종료 (잔고/포지션 저장됨)")
