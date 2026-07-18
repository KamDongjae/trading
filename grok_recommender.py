# -*- coding: utf-8 -*-
"""
grok_recommender.py
--------------------
서버(trading_server.py) 프로세스가 실행 중이지 않아도 이 스크립트 혼자서 전부 처리한다
(trading_server.py는 계산 함수(process_ticker 등)만 모듈로 import해서 재사용하고, 계좌/
포지션/명령큐 같은 트레이딩 기능은 전혀 안 건드림 — "무거워도 되니 서버 없이 돌아가게"
요청 반영. 대신 코인 수만큼 캔들 fetch를 직접 하니 예전보다 느리다).

파이프라인:
  ① 빗썸 전체 티커 조회 + 가격 히스토리 워밍업 후, 코인별로 process_ticker() 직접 호출해서
     지표/점수를 자체 계산 (score_cache를 이 스크립트가 직접 채움)
  ② 바이낸스 상장된 코인만 골라 후보군 확정 (사전 랭킹 없음, 전부 사용) + 시장 국면(상승/
     하락/횡보) 판단
  ③ 그 점수 스냅샷으로 지표 리포트(PDF, 이름순 10개씩 페이지 분할) 생성
  ④ 후보 코인들의 캔들차트를 matplotlib으로 그려 한 PDF(여러 페이지)로 합치고,
     OHLCV+지표 데이터를 한 CSV로 합침
  ⑤ 지표 리포트(텍스트) + 차트(이미지) + 데이터(CSV 샘플) + 시장국면을 xAI Grok API에 보내서
     롱 후보 최대3개 / 숏 후보 최대3개를 추천받아 "long:AAA,BBB / short:DDD,EEE" 형식으로 출력

준비물:
  - trading_server.py가 같은 폴더에 있어야 함 (import만 함, 실행 중일 필요는 없음)
  - pip install pypdf python-bithumb matplotlib pandas requests
  - API_KEY.txt (OS별 위치 자동 감지) 또는 환경변수 XAI_API_KEY
"""
import os
import platform
import sys
import csv
import re
import io
import json
import random
import time
import base64
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import pandas as pd
import python_bithumb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.patches import Rectangle
from matplotlib.backends.backend_pdf import PdfPages
import pypdf  # PDF 텍스트 추출용 (pip install pypdf) — 순수 파이썬이라 Termux에서도 컴파일 없이 깔림
import requests  # xAI Grok API를 openai SDK 대신 REST로 직접 호출 (openai 패키지가 Rust로 빌드되는
                  # jiter에 의존해서 Termux/안드로이드에서 컴파일이 안 되는 경우가 많아서 회피)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import trading_server as srv  # noqa: E402  경로 상수(CMD_DIR 등)만 재사용, 서버 로직은 안 건드림

# ================== 설정 ==================
def _detect_api_key_path():
    """
    OS별로 API_KEY.txt 위치가 다르니 자동으로 알아서 고른다 — 사용자가 매번 경로 고칠 필요 없게.
      - Windows: D:\\API_KEY.txt
      - 안드로이드(Termux/proot 등, /storage/emulated/0가 존재하는 환경): /storage/emulated/0/Documents/API_KEY.txt
      - 그 외(일반 리눅스/맥 등): 이 스크립트와 같은 폴더
    안드로이드는 platform.system()이 그냥 'Linux'로 나와서 구분이 안 되기 때문에,
    안드로이드에만 있는 /storage/emulated/0 경로 존재 여부로 판별한다.
    """
    if platform.system() == "Windows":
        return "D:\\API_KEY.txt"
    if os.path.isdir("/storage/emulated/0"):
        return "/storage/emulated/0/Documents/API_KEY.txt"
    return os.path.join(SCRIPT_DIR, "API_KEY.txt")


def _load_api_key():
    """
    위 OS별 경로에서 우선 찾고, 없으면 스크립트와 같은 폴더 → 환경변수 XAI_API_KEY 순서로 폴백한다
    (파일 안엔 키 값만 한 줄로).
    """
    candidates = [_detect_api_key_path(), os.path.join(SCRIPT_DIR, "API_KEY.txt")]
    for key_path in candidates:
        if os.path.exists(key_path):
            with open(key_path, "r", encoding="utf-8") as f:
                key = f.read().strip()
            if key:
                print(f"✅ API 키 파일 사용: {key_path}")
                return key
    print(f"⚠️ API_KEY.txt를 못 찾음(확인한 경로: {', '.join(candidates)}) — 환경변수 XAI_API_KEY로 폴백")
    return os.environ.get("XAI_API_KEY", "")

XAI_API_KEY = _load_api_key()
GROK_MODEL = "grok-4.3"   # xAI 콘솔에서 다른 모델로 바꿔도 됨 (예: grok-4-fast-reasoning)
MAX_CHART_IMAGES = 40      # 이제 40개 다 후보군이라 이미지도 다 보냄 (필요하면 낮춰서 비용 절감)
IMAGE_DETAIL = "low"        # 기본 디테일. "low"면 이미지 하나당 고정된 적은 토큰만 소모(훨씬 쌈,
                            # 대신 세밀한 패턴은 덜 보임). HIGH_DETAIL_TOP_N에 뽑힌 상/하위
                            # 후보만 예외적으로 "high"로 승급된다 (아래 build_combined_outputs 참고).
HIGH_DETAIL_TOP_N = 15      # 롱/숏 점수차 기준 상위 N개 + 하위 N개만 high 디테일 이미지로 전송
                            # (나머지는 low 유지) — 해상도 저하로 인한 RSI 다이버전스/미세 캔들
                            # 패턴 인식 실패를 막으면서도 토큰 비용은 억제하는 절충안.

LIQUIDITY_BOTTOM_PCT = 0.20  # 거래대금(vol_24h_m) 하위 20%는 "허위 펌핑/덤핑" 필터 대상으로
                             # Grok에게 별도로 명시(제외는 안 하고 후순위 권고만)

BITHUMB_INTERVAL = "minute60"   # 차트/CSV용 봉 간격 (python_bithumb 규격)
INTERVAL_LABEL = "60min"
CHART_COUNT = 200               # 코인당 캔들 개수

OUT_DIR = os.path.join(SCRIPT_DIR, "grok_run")
os.makedirs(OUT_DIR, exist_ok=True)

# ---- [정확도 개선 제안 PDF 반영] ----------------------------------------
# ①④ 상위타임프레임(4H/일봉) EMA 정배열 확인용
HTF_INTERVALS = [("minute240", "4H"), ("day", "1D")]
HTF_CANDLE_COUNT = 80          # EMA60 계산에 필요한 최소치 + 여유분

# ② ATR 변동성 필터, ③ ADX 추세강도 필터
ATR_PERIOD = 14
ADX_PERIOD = 14
ADX_TREND_MIN = 20             # 이 밑이면 "횡보/무추세"로 보고 신호 신뢰도를 낮춤

# ④ OBV/CMF 자금흐름
CMF_PERIOD = 20

# ⑤ 펀딩비 + 미결제약정 — 빗썸엔 없어서 바이낸스 선물 공개 API(키 불필요)로 보조 조회.
#    코인이 바이낸스 선물에 없으면 조용히 스킵(현물만 있는 코인이 많아서 정상적인 상황).
BINANCE_FUTURES_BASE = "https://fapi.binance.com"

# ⑥ BTC/USDT 도미넌스 — 코인게코 공개 API(키 불필요), 실행당 1회만 조회
COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"

# ⑦ 앙상블 스코어링: Grok 단독 판단 대신, 아래 정량 지표들을 조합한 rule_score(-1~+1)를
#    별도로 계산해서 Grok한테 "참고 신호"로 같이 주고, Grok 응답 이후에도 rule_score와의
#    합치 여부로 최종 신뢰도를 재조정한다 (완전 자동매매가 아니라 Grok+룰 혼합 판단).
DEFAULT_WEIGHTS = {
    "htf": 0.25,        # 상위 타임프레임(4H/1D) EMA 정배열 방향
    "obv": 0.15,        # OBV 최근 기울기(자금 유입/유출)
    "cmf": 0.15,        # CMF 부호(매집/분산)
    "bb_macd": 0.20,    # BB 극단 위치 + MACD_Hist 반전 컨플루언스
    "funding": 0.10,    # 펀딩비 역방향(쏠림 과열 시 반대 신호)
    "dominance": 0.15,  # BTC 도미넌스 상승/하락에 따른 알트 가중치
}
WEIGHTS_STATE_PATH = os.path.join(SCRIPT_DIR, "grok_ensemble_weights.json")

# ⑧ 워크포워드 백테스트 + 자동 가중치 최적화 — 단, 이번 실행에서 fetch한 시계열(빗썸
#    시간봉 200개)만으로 재구성 가능한 신호(obv/cmf/bb_macd)만 최적화 대상이다. htf/
#    funding/dominance는 "현재 시점 스냅샷"만 있고 과거 시계열을 안 쌓아서 워크포워드로
#    최적화할 수 없다 — 이 3개는 DEFAULT_WEIGHTS 값 그대로 고정하고, 나머지 3개
#    (obv/cmf/bb_macd) 비중만 과거 데이터로 재배분한다. 정직하게 밝히는 한계.
BACKTEST_HOLD_BARS = 6          # 시간봉 6개(=6시간) 뒤 수익률로 라벨링
BACKTEST_STEP = 4               # 4봉 간격으로 샘플링(계산량 절감)
BACKTEST_MIN_LOOKBACK = 60      # 지표 워밍업(MA60 등) 확보를 위한 시작 오프셋
BACKTEST_OPTIMIZABLE = ["obv", "cmf", "bb_macd"]
BACKTEST_RANDOM_ITERS = 250     # 랜덤서치 반복 횟수 (scipy 없이 순수 파이썬으로)

# ⑨ 신뢰도 점수 — Grok 응답에 confidence(0~1)를 같이 받아서, rule_score와의 합치도로
#    보정한 최종 신뢰도가 기준 미달이면 그 픽은 버린다.
CONFIDENCE_MIN_DEFAULT = 0.45

# ⑩ 추천 결과 추적 + 재보정 — 매 실행마다 과거에 낸 추천이 EVAL_HORIZON_HOURS가 지났으면
#    현재가로 승/패를 판정해서 로그에 기록하고, 최근 히트레이트로 CONFIDENCE_MIN을 조금씩
#    자동 조정한다(성과가 나쁘면 더 깐깐하게, 좋으면 살짝 완화).
RECOMMENDATION_LOG_PATH = os.path.join(SCRIPT_DIR, "grok_recommendation_log.json")
EVAL_HORIZON_HOURS = 6          # BACKTEST_HOLD_BARS(6시간봉)와 맞춤
HIT_RATE_LOOKBACK = 20          # 최근 몇 건으로 히트레이트 계산할지
# ---------------------------------------------------------------------------
# ===========================================


def _fetch_with_backoff(fetch_fn, max_retries=4, base_delay=0.6):
    """
    python_bithumb 호출을 감싸서 Rate Limit 등으로 None이 오거나 예외가 나면 지수 백오프로
    재시도한다 (0.6s, 1.2s, 2.4s, 4.8s ...). 코인 수만큼 순차/병렬 호출이 몰릴 때 빗썸 API가
    None을 반환하는 경우를 흡수해서 "데이터 못 가져옴, 건너뜀"이 되는 빈도를 줄인다.
    """
    delay = base_delay
    last_err = None
    for attempt in range(max_retries):
        try:
            result = fetch_fn()
            if result is not None and len(result) > 0:
                return result
        except Exception as e:
            last_err = e
        if attempt < max_retries - 1:
            time.sleep(delay)
            delay *= 2
    if last_err:
        print(f"     ⚠️ 재시도 {max_retries}회 실패 (마지막 에러: {last_err})")
    return None


def load_weights():
    """직전 실행에서 백테스트로 최적화해둔 앙상블 가중치가 있으면 그걸 쓰고, 없으면 기본값."""
    if os.path.exists(WEIGHTS_STATE_PATH):
        try:
            with open(WEIGHTS_STATE_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            weights = dict(DEFAULT_WEIGHTS)
            weights.update({k: v for k, v in saved.items() if k in DEFAULT_WEIGHTS})
            return weights
        except Exception as e:
            print(f"   ⚠️ 저장된 가중치 로드 실패({e}), 기본값 사용")
    return dict(DEFAULT_WEIGHTS)


def save_weights(weights):
    try:
        with open(WEIGHTS_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(weights, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"   ⚠️ 가중치 저장 실패: {e}")


def load_recommendation_log():
    if os.path.exists(RECOMMENDATION_LOG_PATH):
        try:
            with open(RECOMMENDATION_LOG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"   ⚠️ 추천 로그 로드 실패({e}), 빈 로그로 시작")
    return []


def save_recommendation_log(log):
    try:
        with open(RECOMMENDATION_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"   ⚠️ 추천 로그 저장 실패: {e}")

# 나눔고딕 등 한글 폰트 있으면 쓰고, 없으면 영문 라벨로 자동 전환 (chart.py와 동일한 로직)
def _find_korean_font():
    candidates = ["NanumGothic", "NanumBarunGothic", "Noto Sans CJK KR", "Noto Sans KR"]
    available = {f.name for f in fm.fontManager.ttflist}
    for c in candidates:
        if c in available:
            return c
    return None

_KO_FONT = _find_korean_font()
if _KO_FONT:
    matplotlib.rcParams['font.family'] = _KO_FONT
    matplotlib.rcParams['axes.unicode_minus'] = False
LBL = {
    "volume": "거래량" if _KO_FONT else "Volume",
    "rsi": "RSI(14)",
    "rsi_delta": "RSI Δ" if _KO_FONT else "RSI Delta",
    "chart_suffix": "봉 차트" if _KO_FONT else " chart",
}


# ============================================================
# ① 서버 없이 이 스크립트 혼자서 시세/지표/점수를 직접 계산
#    (trading_server.py의 process_ticker 등 계산 함수만 재사용, 계좌/포지션/명령큐 같은
#    트레이딩 기능은 전혀 안 건드림 — 그래서 실행 중인 서버가 없어도 된다)
# ============================================================
def compute_scores_standalone(warmup_seconds=15):
    """
    참고: chg_30m(최근 30분 변동률)은 원래 서버가 계속 켜져있으면서 쌓은 가격 히스토리로
    계산하는데, 이 스크립트는 방금 막 켜진 거라 그 히스토리가 없다. warmup_seconds 동안
    가격을 미리 폴링해서 최소한의 히스토리를 만들지만, 진짜 "30분 전" 데이터는 아니라서
    chg_30m 값이 서버를 오래 켜뒀을 때보다 부정확할 수 있다(무거워도 된다는 전제하에
    이 정도 오차는 감수하는 설계).
    """
    print(f"① 서버 없이 직접 시세 수집 중 (워밍업 {warmup_seconds}초)...")
    try:
        price_data = srv.Bithumb.get_current_price("ALL")
        tickers = [k for k in price_data.keys() if k != "date"][:srv.TOP_COIN_COUNT]
    except Exception as e:
        raise RuntimeError(f"빗썸 티커 목록을 못 가져왔습니다: {e}")
    print(f"   티커 {len(tickers)}개 로드")

    srv.running = True
    tickers_ref = [tickers]
    threading.Thread(target=srv.price_updater, args=(tickers_ref,), daemon=True).start()
    time.sleep(warmup_seconds)

    print("   지표/점수 계산 중 (코인마다 캔들 fetch, 코인 수만큼 시간 걸림)...")
    results = []
    with ThreadPoolExecutor(max_workers=srv.MAX_WORKERS) as pool:
        futures = {pool.submit(srv.process_ticker, t): t for t in tickers}
        for future in as_completed(futures):
            try:
                res = future.result()
            except Exception as e:
                print(f"   ⚠️ {futures[future]} 계산 실패: {e}")
                continue
            if res:
                results.append(res)
    with srv.score_lock:
        srv.score_cache.clear()
        for r in results:
            srv.score_cache[r['ticker']] = r
    print(f"   {len(results)}/{len(tickers)}개 코인 점수 계산 완료")
    return results


def generate_indicator_report():
    """srv_generate_report()를 명령 큐 없이 함수 직접 호출로 실행 (score_cache는 이미 채워둔 상태)."""
    print("③ 지표 리포트(PDF) 생성 중...")
    ok, msg = srv.srv_generate_report(save_dir=OUT_DIR)
    if not ok:
        raise RuntimeError(f"리포트 생성 실패: {msg}")
    first_line = msg.split("\n")[0]
    # "리포트 생성 완료: {path}" 뿐 아니라 "리포트 생성 완료(일부 누락): {path}" 형식도 오므로
    # 접두문구 문자열매칭 대신 ".pdf로 끝나는 경로"를 정규식으로 뽑는다(더 안정적).
    m = re.search(r'([A-Za-z]:[\\/]\S*\.pdf|/\S*\.pdf)', first_line)
    path = m.group(1).strip() if m else None
    if not path or not os.path.exists(path):
        raise RuntimeError(f"리포트 파일 경로를 못 찾음: {msg}")
    if len(msg.split("\n")) > 1:
        print(f"   ⚠️ 리포트 일부 페이지 누락됨(계속 진행): {msg.split(chr(10), 1)[1][:200]}")
    print("   ", path)
    return path


def get_all_tickers(all_rows):
    """
    상위 롱10/숏10으로 미리 걸러내지 않고, 방금 계산한 결과 중 바이낸스 상장된 코인
    전부를 후보군으로 쓴다(사전 랭킹으로 후보를 좁히면 그 랭킹 자체(점수체계)가 이미
    걸러낸 영향이 결과에 섞여버려서, Grok이 원 데이터를 다 보고 스스로 판단하게 한다).
    """
    rows = [r for r in all_rows if r.get('price_usd')]
    rows.sort(key=lambda r: r.get('ticker', ''))
    if not rows:
        raise RuntimeError("바이낸스 상장 코인 중 점수 데이터가 없습니다")
    all_tickers = [r['ticker'] for r in rows]
    return all_tickers, rows


def detect_market_regime(rows):
    """
    지금이 상승장/하락장/횡보장인지, 얼마나 강하게 그런지까지 판단한다. 롱3/숏3을 시장
    상황과 무관하게 무조건 강제하면, 상승장에서 억지로 넣은 숏 3개나 하락장에서 억지로
    넣은 롱 3개가 전부 깨질 수 있다는 지적 반영 — 개별 코인 픽 전에 "시장 전체가 롱/숏
    중 어느 쪽에 얼마나 유리한지"를 계산해서 Grok한테 참고 근거로 준다(강제 배분은
    아니고 편향 지시).

    예전엔 "롱 점수가 더 높은 코인 개수 비율"(breadth) 하나만 봤는데, 그것만으로는:
      - 51:49로 근소하게 이긴 것과 80:20으로 압도적으로 이긴 걸 구분 못 함
      - 거래량 없는 잡코인 하나가 튀어도 전체 판단에 똑같은 한 표로 들어감
      - BTC처럼 시장을 선행하는 코인의 영향력을 따로 안 봄
    그래서 아래 4개 신호를 합쳐서 -1(강한 하락) ~ +1(강한 상승) 사이의 종합 점수를 낸다.
    """
    if not rows:
        return "NEUTRAL", 0.5, "데이터 없음"

    # ① Breadth: 롱 우세 코인 비율 (그냥 개수 세기, -1~+1로 변환)
    long_wins = sum(1 for r in rows if r['long_score'] > r['short_score'])
    short_wins = sum(1 for r in rows if r['short_score'] > r['long_score'])
    total = long_wins + short_wins
    long_ratio = (long_wins / total) if total else 0.5
    signal_breadth = (long_ratio - 0.5) * 2  # 0.5(반반)->0, 1.0(전부롱)->+1, 0.0(전부숏)->-1

    # ② 점수차 강도: 코인마다 (long_score-short_score) 격차의 평균 (-100~100 -> -1~+1)
    avg_diff = sum(r['long_score'] - r['short_score'] for r in rows) / len(rows)
    signal_strength = max(-1.0, min(1.0, avg_diff / 50))  # ±50점차면 이미 극단으로 취급

    # ③ 유동성 가중 breadth: 거래대금(vol_24h_m) 클수록 그 코인의 '한 표'를 더 무겁게.
    #    잡코인 하나가 튀는 것보다 실제 많이 거래되는 코인들의 쏠림을 우선한다.
    weighted_sum, weight_total = 0.0, 0.0
    for r in rows:
        w = max((r.get('vol_24h_m') or 0), 1) ** 0.5  # 제곱근으로 극단적 쏠림은 완화
        vote = 1.0 if r['long_score'] > r['short_score'] else (-1.0 if r['short_score'] > r['long_score'] else 0.0)
        weighted_sum += w * vote
        weight_total += w
    signal_liquidity = (weighted_sum / weight_total) if weight_total else 0.0

    # ④ BTC 자체 EMA 정배열/역배열 — 크립토는 BTC가 알트코인 방향을 선행하는 경우가 많다.
    btc_row = next((r for r in rows if r['ticker'] == 'BTC'), None)
    signal_btc = 0.0
    btc_note = " (BTC 데이터 없음)"
    if btc_row:
        ema20, ema60, ema120 = btc_row.get('ema20'), btc_row.get('ema60'), btc_row.get('ema120')
        if ema20 and ema60 and ema120:
            if ema20 > ema60 > ema120:
                signal_btc = 1.0
                btc_trend = "정배열(상승)"
            elif ema20 < ema60 < ema120:
                signal_btc = -1.0
                btc_trend = "역배열(하락)"
            else:
                signal_btc = 0.0
                btc_trend = "혼조"
            btc_note = (f" BTC: EMA {btc_trend}, long={btc_row['long_score']}/short={btc_row['short_score']}.")
        else:
            btc_note = f" BTC: EMA 데이터 없음, long={btc_row['long_score']}/short={btc_row['short_score']}."

    # 종합 점수: breadth·강도·유동성가중 평균에 BTC 신호를 조금 더 크게 반영(시장 선행 지표라서)
    composite = (signal_breadth * 0.20 + signal_strength * 0.25 + signal_liquidity * 0.25 + signal_btc * 0.30)

    if composite >= 0.35:
        regime, regime_ko = "STRONG_UPTREND", "강한 상승장"
    elif composite >= 0.12:
        regime, regime_ko = "UPTREND", "상승장 우세"
    elif composite <= -0.35:
        regime, regime_ko = "STRONG_DOWNTREND", "강한 하락장"
    elif composite <= -0.12:
        regime, regime_ko = "DOWNTREND", "하락장 우세"
    else:
        regime, regime_ko = "NEUTRAL", "횡보/혼조"

    detail = (f"{regime_ko} (종합점수 {composite:+.2f}) — "
              f"breadth {signal_breadth:+.2f}(롱우세 {long_wins}/{total}), "
              f"점수차강도 {signal_strength:+.2f}(평균 {avg_diff:+.1f}점), "
              f"유동성가중 {signal_liquidity:+.2f}.{btc_note}")
    return regime, composite, detail


# ============================================================
# ③ 코인별 차트/데이터 (chart.py와 동일한 로직을 이 스크립트 안에 그대로 갖고 있음 —
#    chart.py는 맨 아래서 바로 Tkinter GUI를 띄우는 구조라 import해서 재사용하기엔
#    안전하지 않아서, 필요한 부분만 복제했다)
# ============================================================
def load_data(coin):
    """
    grok_recommender_improvement 가이드의 [개선 1~3] 반영:
      - chg_30m: 워밍업 방식 대신 minute30봉 2개를 직접 fetch해서 "현재 종가 vs 30분 전 종가"
        괴리율을 역산 (서버 히스토리에 의존하지 않는 정확한 30분 변동률)
      - BB_Position: 볼린저 밴드(20,2) 및 밴드 내 현재가 위치 비율(0~1)
      - MACD/Signal/Hist: MACD(12,26,9) 오실레이터
    빗썸 API Rate Limit으로 인한 None 응답에 대비해 두 fetch 모두 지수 백오프 재시도 적용.
    """
    ticker = f"KRW-{coin}"
    df = _fetch_with_backoff(
        lambda: python_bithumb.get_ohlcv(ticker=ticker, interval=BITHUMB_INTERVAL, count=CHART_COUNT)
    )
    if df is None or len(df) == 0:
        return None
    df.index = pd.to_datetime(df.index)
    df['MA5'] = df['close'].rolling(5).mean()
    df['MA20'] = df['close'].rolling(20).mean()
    df['MA60'] = df['close'].rolling(60).mean()
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = -delta.where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    df['RSI_Delta'] = df['RSI'].diff()

    # [개선 1] 워밍업 프리 30분 변동률 역산
    df_30m = _fetch_with_backoff(
        lambda: python_bithumb.get_ohlcv(ticker=ticker, interval="minute30", count=2)
    )
    if df_30m is not None and len(df_30m) >= 2:
        p_ago = df_30m['close'].iloc[-2]
        df['chg_30m'] = ((df['close'] - p_ago) / p_ago) * 100
    else:
        df['chg_30m'] = 0.0

    # [개선 2] 볼린저 밴드(20, 2) + 밴드 내 위치 비율
    std20 = df['close'].rolling(20).std()
    df['BB_Upper'] = df['MA20'] + (2 * std20)
    df['BB_Lower'] = df['MA20'] - (2 * std20)
    df['BB_Position'] = (df['close'] - df['BB_Lower']) / (df['BB_Upper'] - df['BB_Lower'] + 1e-8)

    # [개선 3] MACD(12, 26, 9) 오실레이터 + 히스토그램
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

    # [정확도 개선 ② ATR(14)] 변동성 필터용 — 절대값과, 가격 대비 비율(%) 둘 다 남긴다.
    prev_close = df['close'].shift(1)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - prev_close).abs(),
        (df['low'] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(ATR_PERIOD).mean()
    df['ATR_Pct'] = (df['ATR'] / df['close']) * 100

    # [정확도 개선 ③ ADX(14)] 추세 강도 — Wilder 방식 +DI/-DI/DX/ADX
    up_move = df['high'].diff()
    down_move = -df['low'].diff()
    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)
    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move[(up_move > down_move) & (up_move > 0)]
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move[(down_move > up_move) & (down_move > 0)]
    atr_wilder = tr.ewm(alpha=1 / ADX_PERIOD, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / ADX_PERIOD, adjust=False).mean() / atr_wilder.replace(0, float('nan')))
    minus_di = 100 * (minus_dm.ewm(alpha=1 / ADX_PERIOD, adjust=False).mean() / atr_wilder.replace(0, float('nan')))
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float('nan'))) * 100
    df['ADX'] = dx.ewm(alpha=1 / ADX_PERIOD, adjust=False).mean()
    df['Plus_DI'] = plus_di
    df['Minus_DI'] = minus_di

    # [정확도 개선 ④ OBV + CMF] 자금 흐름 확인 (가격만으론 안 보이는 매집/분산 신호)
    direction = pd.Series(0, index=df.index)
    direction[df['close'] > prev_close] = 1
    direction[df['close'] < prev_close] = -1
    df['OBV'] = (direction * df['volume']).fillna(0).cumsum()

    mf_multiplier = ((df['close'] - df['low']) - (df['high'] - df['close'])) / (df['high'] - df['low']).replace(0, float('nan'))
    mf_volume = mf_multiplier * df['volume']
    df['CMF'] = mf_volume.rolling(CMF_PERIOD).sum() / df['volume'].rolling(CMF_PERIOD).sum()

    return df


def build_figure(df, ticker):
    n = len(df)
    x = range(n)
    fig, axes = plt.subplots(
        4, 1, figsize=(14, 10), dpi=130, sharex=True,
        gridspec_kw={'height_ratios': [3.2, 1.2, 1.0, 1.0], 'hspace': 0.08},
        facecolor='#111111'
    )
    ax_price, ax_vol, ax_rsi, ax_rd = axes
    for ax in axes:
        ax.set_facecolor('#111111')
        ax.tick_params(colors='#cccccc', labelsize=8)
        for spine in ax.spines.values():
            spine.set_color('#444444')
        ax.grid(color='#333333', linewidth=0.5, alpha=0.6)

    width = 0.6
    up_color, down_color = '#00ff88', '#ff3838'
    for i, (_, row) in enumerate(df.iterrows()):
        color = up_color if row['close'] >= row['open'] else down_color
        ax_price.plot([i, i], [row['low'], row['high']], color=color, linewidth=0.8, zorder=2)
        body_low = min(row['open'], row['close'])
        body_h = abs(row['close'] - row['open']) or (row['high'] * 0.0005)
        ax_price.add_patch(Rectangle((i - width / 2, body_low), width, body_h,
                                      facecolor=color, edgecolor=color, zorder=3))

    ax_price.plot(x, df['MA5'], color='#ffff00', linewidth=1.2, label='MA5')
    ax_price.plot(x, df['MA20'], color='#00ffff', linewidth=1.2, label='MA20')
    ax_price.plot(x, df['MA60'], color='#ff00ff', linewidth=1.2, label='MA60')
    ax_price.legend(loc='upper left', facecolor='#111111', edgecolor='#444444',
                     labelcolor='#dddddd', fontsize=8)
    ax_price.set_title(f'KRW-{ticker} {INTERVAL_LABEL}{LBL["chart_suffix"]}', color='white', fontsize=13, pad=10)

    vol_colors = [up_color if r['close'] >= r['open'] else down_color for _, r in df.iterrows()]
    ax_vol.bar(x, df['volume'], color=vol_colors, width=width)
    ax_vol.set_ylabel(LBL["volume"], color='#cccccc', fontsize=9)

    ax_rsi.plot(x, df['RSI'], color='#ffa500', linewidth=1.3)
    ax_rsi.axhline(30, color='lime', linestyle='--', linewidth=0.8)
    ax_rsi.axhline(70, color='red', linestyle='--', linewidth=0.8)
    ax_rsi.set_ylabel(LBL["rsi"], color='#cccccc', fontsize=9)
    ax_rsi.set_ylim(0, 100)

    ax_rd.plot(x, df['RSI_Delta'], color='#00ccff', linewidth=1.3)
    ax_rd.axhline(0, color='white', linestyle=':', linewidth=0.8)
    ax_rd.set_ylabel(LBL["rsi_delta"], color='#cccccc', fontsize=9)

    step = max(n // 10, 1)
    tick_idx = list(range(0, n, step))
    tick_labels = [df.index[i].strftime('%m-%d %H:%M') for i in tick_idx]
    ax_rd.set_xticks(tick_idx)
    ax_rd.set_xticklabels(tick_labels, rotation=30, ha='right')
    ax_price.set_xlim(-1, n)
    return fig


def get_htf_trend(coin):
    """
    [정확도 개선 ①] 4H/1D 상위타임프레임 EMA20/60 정배열 여부를 확인한다.
    시간봉만 보고 픽하면 상위 프레임에서 역행하는 신호를 놓칠 수 있어서, 4H·1D 둘 다
    정배열(EMA20>EMA60=상승, 반대=하락)이면 강한 신호, 둘이 엇갈리면 "혼조"로 판단.
    반환: {"4H": 1/-1/0, "1D": 1/-1/0, "agree": bool, "score": -1~+1}
    """
    ticker = f"KRW-{coin}"
    result = {}
    for interval, label in HTF_INTERVALS:
        df = _fetch_with_backoff(
            lambda i=interval: python_bithumb.get_ohlcv(ticker=ticker, interval=i, count=HTF_CANDLE_COUNT)
        )
        if df is None or len(df) < 20:
            result[label] = 0
            continue
        ema20 = df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema60 = df['close'].ewm(span=min(60, len(df) - 1), adjust=False).mean().iloc[-1]
        if ema20 > ema60:
            result[label] = 1
        elif ema20 < ema60:
            result[label] = -1
        else:
            result[label] = 0
    vals = [v for v in result.values()]
    agree = len(set(vals)) == 1 and vals[0] != 0
    score = (sum(vals) / len(vals)) if vals else 0.0
    result["agree"] = agree
    result["score"] = score
    return result


def get_funding_oi(coin):
    """
    [정확도 개선 ⑤] 바이낸스 선물 공개 API(키 불필요)에서 펀딩비 + 미결제약정 조회.
    빗썸엔 선물이 없어서 방향성 참고용 보조 신호로만 쓴다. 코인이 바이낸스 선물에 없으면
    (신규/잡코인) 조용히 None 반환 — 에러 취급 안 함(흔한 정상 케이스라서).
    """
    symbol = f"{coin}USDT"
    try:
        r1 = requests.get(f"{BINANCE_FUTURES_BASE}/fapi/v1/premiumIndex",
                           params={"symbol": symbol}, timeout=8)
        r2 = requests.get(f"{BINANCE_FUTURES_BASE}/fapi/v1/openInterest",
                           params={"symbol": symbol}, timeout=8)
        if r1.status_code != 200 or r2.status_code != 200:
            return None
        funding_rate = float(r1.json().get("lastFundingRate", 0.0))
        open_interest = float(r2.json().get("openInterest", 0.0))
        return {"funding_rate": funding_rate, "open_interest": open_interest}
    except Exception:
        return None


def get_dominance():
    """
    [정확도 개선 ⑥] 코인게코 공개 API(키 불필요)에서 BTC/USDT 도미넌스를 실행당 1회만 조회.
    BTC 도미넌스 상승 = 자금이 알트에서 BTC로 쏠리는 국면(알트 약세 경향),
    USDT 도미넌스 상승 = 관망/현금화 심리(전반적 위험회피) 참고 신호로 쓴다.
    """
    try:
        resp = requests.get(COINGECKO_GLOBAL_URL, timeout=10)
        if resp.status_code != 200:
            return None
        pct = resp.json().get("data", {}).get("market_cap_percentage", {})
        return {"btc_dominance": pct.get("btc"), "usdt_dominance": pct.get("usdt")}
    except Exception:
        return None


def _obv_slope_signal(df):
    """최근 OBV 기울기 부호를 -1~+1로 정규화 (최근 20봉 선형추세 방향)."""
    obv = df['OBV'].dropna()
    if len(obv) < 20:
        return 0.0
    recent = obv.iloc[-20:]
    x = list(range(len(recent)))
    mean_x, mean_y = sum(x) / len(x), recent.mean()
    num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, recent))
    den = sum((xi - mean_x) ** 2 for xi in x) or 1e-9
    slope = num / den
    scale = (abs(recent).max() or 1) / len(recent)  # 코인마다 OBV 스케일이 달라서 정규화
    return max(-1.0, min(1.0, slope / (scale + 1e-9) / 5))


def _bb_macd_confluence_signal(df):
    """BB_Position 극단 + MACD_Hist 반전이 동시에 나타나면 강한 신호로 본다 (기존 프롬프트
    지침과 동일한 논리를 rule_score에도 반영)."""
    if len(df) < 3:
        return 0.0
    last = df.iloc[-1]
    bb_pos = last.get('BB_Position', 0.5)
    hist_now, hist_prev = last.get('MACD_Hist', 0), df.iloc[-2].get('MACD_Hist', 0)
    signal = 0.0
    if bb_pos <= 0.05 and hist_now > hist_prev:      # 하단 눌림 + 히스토그램 반등 시작
        signal = 1.0
    elif bb_pos >= 0.95 and hist_now < hist_prev:    # 상단 눌림 + 히스토그램 하락 시작
        signal = -1.0
    else:
        signal = max(-1.0, min(1.0, (bb_pos - 0.5) * -2 * (1 if hist_now < hist_prev else -1) * 0.3))
    return signal


def compute_rule_score(df, htf, funding_oi, dominance, is_btc, weights):
    """
    [정확도 개선 ⑦ 앙상블] Grok의 정성 판단과 별개로, 정량 지표만으로 -1(숏 우세)~+1(롱 우세)
    사이 rule_score를 계산한다. ADX가 낮으면(횡보) 신뢰도를 통째로 낮춰서 무추세 구간에서
    과신호를 내지 않게 한다.
    반환: (rule_score, confidence_scale, detail_dict)
    """
    if df is None or len(df) < 20:
        return 0.0, 0.3, {}

    last = df.iloc[-1]
    htf_score = htf.get("score", 0.0) if htf else 0.0
    obv_score = _obv_slope_signal(df)
    cmf_val = last.get('CMF', 0.0)
    cmf_score = max(-1.0, min(1.0, ((cmf_val if pd.notna(cmf_val) else 0.0)) * 5))  # CMF는 보통 -0.3~+0.3 범위라 5배 확대
    bb_macd_score = _bb_macd_confluence_signal(df)

    funding_score = 0.0
    if funding_oi and funding_oi.get("funding_rate") is not None:
        # 펀딩비가 과도하게 양수(롱 쏠림)면 역방향(하락 압력) 소신호, 반대는 반대.
        fr = funding_oi["funding_rate"]
        funding_score = max(-1.0, min(1.0, -fr * 200))  # 0.01(1%)이면 이미 극단으로 취급

    dominance_score = 0.0
    if dominance and dominance.get("btc_dominance") is not None and not is_btc:
        # 알트코인 한정: BTC 도미넌스가 높은 국면일수록 알트에는 약한 역풍으로 반영.
        # 50%를 중립으로 보고 그보다 높으면 알트 약세 쪽으로 소폭 감점.
        btc_dom = dominance["btc_dominance"]
        dominance_score = max(-1.0, min(1.0, -(btc_dom - 50) / 25))

    composite = (
        weights.get("htf", 0) * htf_score +
        weights.get("obv", 0) * obv_score +
        weights.get("cmf", 0) * cmf_score +
        weights.get("bb_macd", 0) * bb_macd_score +
        weights.get("funding", 0) * funding_score +
        weights.get("dominance", 0) * dominance_score
    )

    # ADX 게이트: 추세가 약하면(횡보) 신호를 죽이지는 않되 신뢰도 배율을 낮춘다.
    adx_val = last.get('ADX', 0); adx_val = 0.0 if pd.isna(adx_val) else float(adx_val)
    confidence_scale = 1.0 if adx_val >= ADX_TREND_MIN else max(0.3, adx_val / ADX_TREND_MIN)

    detail = {
        "htf_score": round(htf_score, 3), "obv_score": round(obv_score, 3),
        "cmf_score": round(cmf_score, 3), "bb_macd_score": round(bb_macd_score, 3),
        "funding_score": round(funding_score, 3), "dominance_score": round(dominance_score, 3),
        "adx": round(float(adx_val), 1),
    }
    return max(-1.0, min(1.0, composite)), confidence_scale, detail


# ============================================================
# ⑧ 워크포워드 백테스트 + 자동 가중치 최적화
#    (obv/cmf/bb_macd만 최적화 — htf/funding/dominance는 과거 시계열이 없어서 고정, 위
#    DEFAULT_WEIGHTS 주석 참고)
# ============================================================
def _backtest_score_sample(df, idx, weights):
    """idx 시점까지의 데이터만 써서 rule_score 재계산 (미래 데이터 참조 금지)."""
    sub = df.iloc[:idx + 1]
    obv_score = _obv_slope_signal(sub)
    cmf_val = sub.iloc[-1].get('CMF', 0.0)
    cmf_score = max(-1.0, min(1.0, ((cmf_val if pd.notna(cmf_val) else 0.0)) * 5))
    bb_macd_score = _bb_macd_confluence_signal(sub)
    return (weights.get("obv", 0) * obv_score +
            weights.get("cmf", 0) * cmf_score +
            weights.get("bb_macd", 0) * bb_macd_score)


def _build_backtest_samples(dfs_by_ticker):
    """코인별 df에서 (시점 idx, 미래 forward_return) 페어를 뽑아둔다. 무거운 지표 재계산은
    가중치와 무관한 부분(obv/cmf/bb_macd 원재료)이라 샘플 추출 시 한 번만 하고, 가중치
    최적화 루프에서는 이미 계산된 시그널 값만 재조합한다(랜덤서치 250회를 매번 지표
    재계산하면 너무 느려서, 시그널 3개를 미리 뽑아 캐싱)."""
    samples = []  # list of (obv_score, cmf_score, bb_macd_score, forward_return)
    for coin, df in dfs_by_ticker.items():
        if df is None or len(df) < BACKTEST_MIN_LOOKBACK + BACKTEST_HOLD_BARS + 5:
            continue
        n = len(df)
        for idx in range(BACKTEST_MIN_LOOKBACK, n - BACKTEST_HOLD_BARS, BACKTEST_STEP):
            sub = df.iloc[:idx + 1]
            obv_score = _obv_slope_signal(sub)
            cmf_val = sub.iloc[-1].get('CMF', 0.0)
            cmf_score = max(-1.0, min(1.0, ((cmf_val if pd.notna(cmf_val) else 0.0)) * 5))
            bb_macd_score = _bb_macd_confluence_signal(sub)
            p0 = df['close'].iloc[idx]
            p1 = df['close'].iloc[idx + BACKTEST_HOLD_BARS]
            fwd_ret = (p1 - p0) / p0 if p0 else 0.0
            samples.append((obv_score, cmf_score, bb_macd_score, fwd_ret))
    return samples


def _objective(samples, w_obv, w_cmf, w_bbm):
    """가중치 조합 하나의 성능 = (rule_score 방향과 실제 미래수익률 방향이 맞은 히트레이트)
    x 평균 수익률 크기. 단순 상관 대신 히트레이트를 써서 "방향을 맞췄는가"에 집중한다."""
    if not samples:
        return 0.0
    hits, total, ret_sum = 0, 0, 0.0
    for obv_s, cmf_s, bbm_s, fwd_ret in samples:
        score = w_obv * obv_s + w_cmf * cmf_s + w_bbm * bbm_s
        if abs(score) < 0.05:   # 신호가 너무 약하면 판단 보류(무추세로 간주, 채점 제외)
            continue
        total += 1
        predicted_up = score > 0
        actual_up = fwd_ret > 0
        if predicted_up == actual_up:
            hits += 1
        ret_sum += (fwd_ret if predicted_up else -fwd_ret)
    if total == 0:
        return 0.0
    hit_rate = hits / total
    avg_signed_ret = ret_sum / total
    return hit_rate * 0.7 + max(-1.0, min(1.0, avg_signed_ret * 20)) * 0.3


def run_walk_forward_backtest(dfs_by_ticker, base_weights):
    """
    이번 실행에서 이미 fetch해둔 시간봉 200개 히스토리로 워크포워드 방식(각 시점까지의
    데이터만 사용, 미래 참조 없음)으로 obv/cmf/bb_macd 3개 가중치를 랜덤서치로 재배분한다.
    표본이 코인당 최대 (200-66)/4 ≈ 33개 x 코인수 정도라 완전한 백테스트라기보단 "최근
    수일간 어떤 신호 조합이 잘 맞았는지"를 매 실행마다 가볍게 재추정하는 온라인 재보정에
    가깝다 — 정직하게 그 한계를 로그에 남긴다.
    """
    print("⑧ 워크포워드 백테스트로 앙상블 가중치 재추정 중...")
    samples = _build_backtest_samples(dfs_by_ticker)
    if len(samples) < 30:
        print(f"   ⚠️ 백테스트 표본 부족({len(samples)}개) — 기존 가중치 유지")
        return base_weights

    fixed_sum = sum(v for k, v in base_weights.items() if k not in BACKTEST_OPTIMIZABLE)
    optimizable_budget = max(0.05, 1.0 - fixed_sum)  # obv+cmf+bb_macd가 나눠 가질 총량

    base_score = _objective(
        samples, base_weights["obv"], base_weights["cmf"], base_weights["bb_macd"]
    )

    best = (base_weights["obv"], base_weights["cmf"], base_weights["bb_macd"])
    best_score = base_score
    for _ in range(BACKTEST_RANDOM_ITERS):
        a, b, c = random.random(), random.random(), random.random()
        s = (a + b + c) or 1e-9
        w_obv, w_cmf, w_bbm = (a / s) * optimizable_budget, (b / s) * optimizable_budget, (c / s) * optimizable_budget
        score = _objective(samples, w_obv, w_cmf, w_bbm)
        if score > best_score:
            best_score = score
            best = (w_obv, w_cmf, w_bbm)

    new_weights = dict(base_weights)
    new_weights["obv"], new_weights["cmf"], new_weights["bb_macd"] = best
    print(f"   표본 {len(samples)}개, 목적함수 {base_score:+.3f} → {best_score:+.3f} "
          f"(obv={best[0]:.2f}, cmf={best[1]:.2f}, bb_macd={best[2]:.2f})")
    return new_weights


def _pick_high_detail_tickers(rows, top_n=HIGH_DETAIL_TOP_N):
    """
    [개선] 이미지 토큰 비용 효율화: 전부 low로 보내면 해상도 저하로 RSI 다이버전스나 미세한
    캔들 패턴 인식이 어려워지므로, 롱/숏 점수차(long_score - short_score) 기준 상위 N개
    (가장 롱 우세) + 하위 N개(가장 숏 우세)만 골라 high 디테일로 승급시킨다. 나머지는 저비용
    low 유지.
    """
    scored = [r for r in rows if r.get('long_score') is not None and r.get('short_score') is not None]
    if not scored:
        return set()
    scored.sort(key=lambda r: r['long_score'] - r['short_score'])
    bottom = scored[:top_n]                       # 가장 숏 우세
    top = scored[-top_n:] if top_n else []          # 가장 롱 우세
    return {r['ticker'] for r in (top + bottom)}


def build_combined_outputs(tickers, rows=None, weights=None, dominance=None):
    """20개 코인 차트를 한 PDF(여러 페이지)로, 데이터를 한 CSV로 합친다.

    rows가 주어지면(점수 데이터 포함) 롱/숏 점수차 상위·하위 HIGH_DETAIL_TOP_N개 코인만
    high 디테일 이미지로 보내고 나머지는 저비용 low를 유지한다 (이미지 토큰 비용 효율화).

    [정확도 개선 ①⑤⑥⑦] 코인마다 HTF(4H/1D) 추세, 펀딩비/OI, 도미넌스를 추가로 조회해서
    rule_score(정량 앙상블 신호)를 계산하고 CSV에 컬럼으로 얹어 Grok한테도 넘긴다. 또한
    코인별 원본 df와 컨텍스트를 별도로 돌려줘서(dfs_by_ticker, coin_context) 이후 단계
    (워크포워드 백테스트, 최종 신뢰도 재계산)에서 다시 fetch하지 않고 재사용한다.
    """
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    combined_pdf_path = os.path.join(OUT_DIR, f"charts_{ts}.pdf")
    combined_csv_path = os.path.join(OUT_DIR, f"data_{ts}.csv")
    weights = weights or DEFAULT_WEIGHTS

    high_detail_set = _pick_high_detail_tickers(rows) if rows else set()
    if high_detail_set:
        print(f"   이미지 high 디테일 승급 대상 ({len(high_detail_set)}개): "
              f"{', '.join(sorted(high_detail_set))}")

    csv_frames = []
    chart_images_b64 = []  # (coin, b64, detail)
    ok_tickers = []
    dfs_by_ticker = {}
    coin_context = {}

    with PdfPages(combined_pdf_path) as pdf_pages:
        for coin in tickers:
            print(f"   - {coin} 차트/데이터/HTF/펀딩·OI 생성 중...")
            df = load_data(coin)
            if df is None:
                print(f"     ⚠️ {coin} 데이터 못 가져옴, 건너뜀")
                continue

            htf = get_htf_trend(coin)
            funding_oi = get_funding_oi(coin)
            rule_score, conf_scale, detail = compute_rule_score(
                df, htf, funding_oi, dominance, is_btc=(coin == 'BTC'), weights=weights
            )
            coin_context[coin] = {
                "rule_score": rule_score, "confidence_scale": conf_scale, "detail": detail,
                "htf": htf, "funding_oi": funding_oi,
            }
            df = df.copy()
            df['rule_score'] = rule_score
            df['rule_confidence_scale'] = conf_scale
            df['htf_4h'] = htf.get("4H", 0) if htf else 0
            df['htf_1d'] = htf.get("1D", 0) if htf else 0
            df['funding_rate'] = funding_oi.get("funding_rate") if funding_oi else None
            df['open_interest'] = funding_oi.get("open_interest") if funding_oi else None
            dfs_by_ticker[coin] = df

            fig = build_figure(df, coin)
            pdf_pages.savefig(fig, facecolor=fig.get_facecolor())

            if len(chart_images_b64) < MAX_CHART_IMAGES:
                buf = io.BytesIO()
                fig.savefig(buf, format='png', facecolor=fig.get_facecolor(), bbox_inches='tight')
                detail_lvl = "high" if coin in high_detail_set else IMAGE_DETAIL
                chart_images_b64.append((coin, base64.b64encode(buf.getvalue()).decode('utf-8'), detail_lvl))
            plt.close(fig)

            d = df.copy()
            d.insert(0, 'ticker', coin)
            csv_frames.append(d)
            ok_tickers.append(coin)

    if not csv_frames:
        raise RuntimeError("코인 데이터를 하나도 못 가져왔습니다")

    print(f"   CSV 저장 시도: {combined_csv_path}")
    try:
        pd.concat(csv_frames).to_csv(combined_csv_path, index=True, index_label="datetime", encoding="utf-8-sig")
    except Exception as e:
        raise RuntimeError(f"CSV 저장 실패 (PDF는 이미 저장됐을 수 있음): {type(e).__name__}: {e}")
    if not os.path.exists(combined_csv_path):
        raise RuntimeError(f"CSV 저장 함수는 에러 없이 끝났는데 파일이 실제로 없습니다: {combined_csv_path}")
    print(f"   ✅ CSV 저장 확인됨 ({os.path.getsize(combined_csv_path):,} bytes)")

    return combined_pdf_path, combined_csv_path, chart_images_b64, ok_tickers, dfs_by_ticker, coin_context


# ============================================================
# ④ Grok API에 보내서 롱3/숏3 추천 받기
# ============================================================
def _low_liquidity_tickers(all_rows, bottom_pct=LIQUIDITY_BOTTOM_PCT):
    """거래대금(vol_24h_m) 하위 bottom_pct에 해당하는 티커 목록 (허위 펌핑/덤핑 필터용)."""
    vols = sorted(((r.get('vol_24h_m') or 0), r['ticker']) for r in all_rows)
    if not vols:
        return []
    cutoff = max(1, int(len(vols) * bottom_pct))
    return [t for _, t in vols[:cutoff]]


def ask_grok(indicator_pdf_path, combined_csv_path, chart_images_b64, all_tickers,
             regime, regime_detail, all_rows=None, dominance=None):
    if not XAI_API_KEY:
        raise RuntimeError("API 키가 없습니다 — 같은 폴더에 API_KEY.txt 파일을 만들고 그 안에 "
                            "키만 한 줄로 넣으세요 (또는 환경변수 XAI_API_KEY=xai-...)")

    # 지표 리포트 PDF에서 텍스트만 추출 (표 그대로는 아니지만 참고 컨텍스트로는 충분)
    report_text = ""
    try:
        reader = pypdf.PdfReader(indicator_pdf_path)
        for page in reader.pages:
            report_text += (page.extract_text() or "") + "\n"
    except Exception as e:
        report_text = f"(리포트 텍스트 추출 실패: {e})"
    report_text = report_text[:12000]

    # CSV 전체를 다 보내면 너무 크니, 코인별 최근 20행만 샘플링
    df_all = pd.read_csv(combined_csv_path)
    sample_rows = df_all.groupby('ticker').tail(20)
    csv_text = sample_rows.to_csv(index=False)
    if len(csv_text) > 40000:
        csv_text = csv_text[:40000] + "\n... (생략)"

    if regime == "STRONG_UPTREND":
        bias_instruction = (
            "The overall market is STRONGLY LONG-biased right now (multiple independent signals agree). "
            "Strongly prefer LONG picks. Be VERY skeptical of SHORT picks — in a strong uptrend shorts "
            "usually get squeezed hard. Only include a SHORT if it's an exceptionally compelling, "
            "clearly-broken-down coin that's genuinely diverging from the market; otherwise leave SHORT "
            "completely blank."
        )
    elif regime == "UPTREND":
        bias_instruction = (
            "The overall market is currently LONG-biased (uptrend breadth). Be generous with LONG picks "
            "if they're genuinely good, but be EXTRA skeptical of SHORT picks — in an uptrend, shorts tend "
            "to get squeezed. Only include a SHORT if it's a truly compelling, strong setup; otherwise "
            "leave SHORT blank or with fewer picks than LONG."
        )
    elif regime == "STRONG_DOWNTREND":
        bias_instruction = (
            "The overall market is STRONGLY SHORT-biased right now (multiple independent signals agree). "
            "Strongly prefer SHORT picks. Be VERY skeptical of LONG picks — in a strong downtrend longs "
            "usually get caught by falling knives. Only include a LONG if it's an exceptionally compelling, "
            "clearly-breaking-out coin that's genuinely diverging from the market; otherwise leave LONG "
            "completely blank."
        )
    elif regime == "DOWNTREND":
        bias_instruction = (
            "The overall market is currently SHORT-biased (downtrend breadth). Be generous with SHORT picks "
            "if they're genuinely good, but be EXTRA skeptical of LONG picks — in a downtrend, longs tend "
            "to get caught by falling knives. Only include a LONG if it's a truly compelling, strong setup; "
            "otherwise leave LONG blank or with fewer picks than SHORT."
        )
    else:
        bias_instruction = (
            "The overall market is currently NEUTRAL/choppy (no clear breadth bias). Judge each side on its "
            "own merits with no directional bias."
        )

    low_liq = _low_liquidity_tickers(all_rows) if all_rows else []
    low_liq_note = (
        f"LIQUIDITY FILTER: these tickers are in the bottom {int(LIQUIDITY_BOTTOM_PCT*100)}% of this "
        f"pool by 24h traded value (vol_24h_m) — small-cap/thin liquidity: {', '.join(low_liq)}. "
        "Even if their technical indicators look good, treat them as likely fake pump/dump signals — "
        "exclude them or push them to the bottom of consideration unless the setup is exceptional."
        if low_liq else
        "LIQUIDITY FILTER: no reliable 24h-volume data available to rank liquidity this run — weight "
        "liquidity qualitatively from the indicator report instead."
    )

    dominance_note = (
        f"BTC dominance {dominance['btc_dominance']:.1f}%, USDT dominance "
        f"{dominance['usdt_dominance']:.1f}% (snapshot at run time)."
        if dominance and dominance.get("btc_dominance") is not None
        else "Dominance data unavailable this run."
    )

    system_prompt = (
        "You are a crypto futures trading analyst. You are given (1) an indicator report with raw "
        "indicator values for the coins currently tracked, (2) recent OHLCV+indicator history samples "
        "per coin (including BB_Position, MACD/MACD_Signal/MACD_Hist, ATR/ATR_Pct, ADX/Plus_DI/Minus_DI, "
        "OBV, CMF, chg_30m, and rule_score/rule_confidence_scale), and (3) candlestick chart images per "
        "coin. This is NOT a pre-filtered shortlist — it is the full set of coins currently tracked, "
        "with no prior ranking or bias applied. You must judge every coin yourself from the raw data "
        "and decide its direction (if any). "
        f"MARKET REGIME (computed from breadth across all these coins): "
        f"{regime} — {regime_detail} {bias_instruction} MARKET DOMINANCE: {dominance_note} "
        f"The candidate pool (same pool for both LONG and SHORT — you decide each coin's direction, "
        f"if any) is: {', '.join(all_tickers)}. "
        "\n\nADDITIONAL ANALYSIS REQUIREMENTS:\n"
        "1) Multi-Timeframe Check: from the CSV data, first establish each coin's MA5/MA20/MA60 "
        "alignment (bullish stack, bearish stack, or mixed) and check whether short-term indicators "
        "agree with that longer-term trend before picking a direction.\n"
        "2) Divergence & BB Cross: look for divergence between price action and RSI across the chart "
        "images and indicator history. Give extra weight to tickers where BB_Position is near 1 "
        "(pressing the upper band) or near 0 (pressing the lower band) AND MACD_Hist shows a reversal "
        "signal (e.g. histogram flipping sign or shrinking against the prevailing trend) at the same "
        "time — that confluence is a stronger signal than either alone.\n"
        f"3) {low_liq_note}\n"
        "4) Trend Strength & Noise Filter: ADX below ~20 means the coin is choppy/range-bound — be "
        "more skeptical of breakout-style setups there. ATR_Pct tells you how volatile/noisy the coin "
        "currently is; very high ATR_Pct relative to the coin's own recent history means wider, less "
        "reliable swings, so demand a clearer setup before picking it.\n"
        "5) Ensemble Signal: rule_score (range -1 to +1) is an independent quantitative ensemble score "
        "computed from higher-timeframe (4H/1D) trend alignment, OBV/CMF money flow, BB+MACD "
        "confluence, funding-rate crowding, and BTC-dominance regime — NOT from an LLM. Treat it as a "
        "second opinion: when your own read and rule_score agree (same sign), that's a stronger case. "
        "When they clearly disagree, lower your confidence for that ticker rather than ignoring the "
        "disagreement.\n"
        "6) Confidence Score: for every ticker you pick, output your own confidence from 0.00 to 1.00 "
        "(how sure you are of that direction, not how big a mover you expect). Do not pick a ticker "
        "with confidence below 0.40 — leave it out instead.\n"
        "7) Format Strictness: reason internally as much as you need, but the FINAL output must be "
        "exactly one line in the exact format below — no markdown, no explanation, no extra "
        "whitespace or commentary before or after it.\n\n"
        "Pick UP TO 3 tickers for LONG and UP TO 3 for SHORT, only from the candidate pool "
        "(never invent tickers outside the pool, never put the same ticker on both sides). "
        "Only include a ticker if you are genuinely confident in it — do NOT pad the list just to "
        "reach 3, and do NOT force a balanced 3-and-3 split just for symmetry. Let the market regime "
        "above and each coin's own setup drive how many you pick on each side — it's fine (and often "
        "correct) for one side to have more picks than the other, or for a side to be completely blank. "
        "Reply with ONLY one line, lowercase tickers, each followed by :confidence (two decimals), "
        "no extra commentary, in exactly this format (a blank side just has nothing between the colon "
        "and the slash):\n"
        "long:xrp:0.82,btc:0.65,eth:0.51 / short:doge:0.70,bch:0.55,mtl:0.42\n"
        "long: / short:doge:0.70,bch:0.55,mtl:0.42\n"
        "long:xrp:0.60 / short:"
    )

    content = [
        {"type": "text", "text": "=== Indicator report (text-extracted from PDF) ===\n" + report_text},
        {"type": "text", "text": "=== Recent OHLCV+indicator sample (last 20 rows per coin) ===\n" + csv_text},
    ]
    for coin, b64, detail in chart_images_b64:
        content.append({"type": "text", "text": f"--- Chart image: {coin} ---"})
        content.append({"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{b64}", "detail": detail}})

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]

    print(f"⑤ Grok API({GROK_MODEL}) 호출 중... (차트 이미지 {len(chart_images_b64)}개 포함)")
    resp = requests.post(
        "https://api.x.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"},
        json={"model": GROK_MODEL, "messages": messages, "temperature": 0.3},
        timeout=180,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Grok API 호출 실패 (HTTP {resp.status_code}): {resp.text[:500]}")
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def _parse_side_picks(side_str):
    """'xrp:0.82,btc:0.65' 형태를 [(티커, 신뢰도), ...]로 파싱. 신뢰도 누락/파싱 실패시 0.5 기본값."""
    picks = []
    for item in side_str.split(','):
        item = item.strip()
        if not item:
            continue
        parts = item.split(':')
        ticker = parts[0].strip().upper()
        conf = 0.5
        if len(parts) > 1:
            try:
                conf = float(parts[1])
            except ValueError:
                conf = 0.5
        if ticker:
            picks.append((ticker, max(0.0, min(1.0, conf))))
    return picks


def evaluate_pending_recommendations(log):
    """[정확도 개선 ⑩] EVAL_HORIZON_HOURS가 지난 pending 추천을 현재가로 승/패 판정."""
    now = datetime.now()
    price_cache = {}
    updated = False
    for entry in log:
        if entry.get("status") != "pending":
            continue
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
        except Exception:
            continue
        if now - ts < timedelta(hours=EVAL_HORIZON_HOURS):
            continue
        coin = entry["ticker"]
        if coin not in price_cache:
            try:
                price_cache[coin] = python_bithumb.get_current_price(f"KRW-{coin}")
            except Exception:
                price_cache[coin] = None
        cur_price = price_cache[coin]
        if not cur_price:
            continue
        entry_price = entry["entry_price"]
        pnl_pct = ((cur_price - entry_price) / entry_price * 100 if entry["side"] == "long"
                   else (entry_price - cur_price) / entry_price * 100)
        entry["exit_price"] = cur_price
        entry["pnl_pct"] = round(pnl_pct, 3)
        entry["status"] = "won" if pnl_pct > 0 else "lost"
        entry["evaluated_at"] = now.isoformat()
        updated = True
    return updated


def compute_hit_rate(log, lookback=HIT_RATE_LOOKBACK):
    resolved = [e for e in log if e.get("status") in ("won", "lost")][-lookback:]
    if not resolved:
        return None, 0
    wins = sum(1 for e in resolved if e["status"] == "won")
    return wins / len(resolved), len(resolved)


def adaptive_confidence_min(hit_rate):
    """[정확도 개선 ⑩ 재보정] 최근 히트레이트가 나쁘면 신뢰도 기준을 높여 더 깐깐하게,
    좋으면 살짝 낮춰서 기회를 더 잡는다. 표본이 없으면 기본값 그대로."""
    if hit_rate is None:
        return CONFIDENCE_MIN_DEFAULT
    if hit_rate < 0.40:
        return min(0.75, CONFIDENCE_MIN_DEFAULT + 0.15)
    if hit_rate < 0.50:
        return CONFIDENCE_MIN_DEFAULT + 0.05
    if hit_rate > 0.65:
        return max(0.30, CONFIDENCE_MIN_DEFAULT - 0.05)
    return CONFIDENCE_MIN_DEFAULT


def main():
    # ⑩ 이전 실행에서 낸 추천 중 평가 시점이 지난 것들을 먼저 승/패 판정
    reco_log = load_recommendation_log()
    if evaluate_pending_recommendations(reco_log):
        save_recommendation_log(reco_log)
    hit_rate, hit_n = compute_hit_rate(reco_log)
    confidence_min = adaptive_confidence_min(hit_rate)
    if hit_rate is not None:
        print(f"⓪ 최근 추천 히트레이트: {hit_rate*100:.0f}% ({hit_n}건) → "
              f"이번 실행 신뢰도 기준선 {confidence_min:.2f}")
    else:
        print(f"⓪ 과거 추천 기록 없음 → 기본 신뢰도 기준선 {confidence_min:.2f}")

    weights = load_weights()
    dominance = get_dominance()
    if dominance and dominance.get("btc_dominance") is not None:
        print(f"   BTC 도미넌스 {dominance['btc_dominance']:.1f}% / "
              f"USDT 도미넌스 {dominance['usdt_dominance']:.1f}%")

    computed_rows = compute_scores_standalone()

    print("② 바이낸스 상장 코인 후보군 확정 중...")
    all_tickers, all_rows = get_all_tickers(computed_rows)
    print(f"   바이낸스 상장 후보군 {len(all_tickers)}개:", ", ".join(all_tickers))

    regime, composite_score, regime_detail = detect_market_regime(all_rows)
    print(f"   시장 국면: {regime} — {regime_detail}")

    indicator_pdf = generate_indicator_report()

    print(f"④ {len(all_tickers)}개 코인 차트/CSV/HTF/펀딩·OI/rule_score 생성 중...")
    combined_pdf, combined_csv, images, ok_tickers, dfs_by_ticker, coin_context = build_combined_outputs(
        all_tickers, rows=all_rows, weights=weights, dominance=dominance
    )
    print("   차트 PDF:", combined_pdf)
    print("   데이터 CSV:", combined_csv)

    # ⑧ 이번에 모은 시계열로 obv/cmf/bb_macd 가중치를 워크포워드로 재추정, 다음 실행을 위해 저장
    new_weights = run_walk_forward_backtest(dfs_by_ticker, weights)
    save_weights(new_weights)

    answer = ask_grok(indicator_pdf, combined_csv, images, all_tickers,
                       regime, regime_detail, all_rows=all_rows, dominance=dominance)
    print("\n=== Grok 원본 응답 ===")
    print(answer)

    # 정규식 예외 처리 강화: Grok이 대소문자를 혼용하거나 예외적인 공백을 섞어 반환해도
    # 메인 루프 파싱이 끊기지 않도록, 공백을 완전히 제거한 뒤 매칭한다.
    clean_answer = answer.lower().replace(" ", "")
    m = re.search(r'long\s*:\s*([a-z0-9:.,]*)\s*/\s*short\s*:\s*([a-z0-9:.,]*)', clean_answer)
    if not m:
        print("\n⚠️ 응답 형식이 예상과 달라 자동 파싱 실패 — 위 원본 텍스트를 직접 확인하세요")
        return

    long_raw = _parse_side_picks(m.group(1))
    short_raw = _parse_side_picks(m.group(2))

    def _finalize(picks, side):
        """⑦⑨ Grok 신뢰도 + rule_score 합치도를 6:4로 블렌딩한 최종 신뢰도로 필터링."""
        finalized, rejected = [], []
        for ticker, grok_conf in picks:
            info = coin_context.get(ticker, {})
            rule_score = info.get("rule_score", 0.0)
            conf_scale = info.get("confidence_scale", 1.0)
            directional = rule_score if side == "long" else -rule_score
            agreement = max(0.0, directional) * conf_scale  # 방향 불일치면 가산 없음(0)
            final_conf = 0.6 * grok_conf + 0.4 * min(1.0, agreement)
            if final_conf >= confidence_min:
                finalized.append((ticker, round(final_conf, 3)))
            else:
                rejected.append((ticker, round(final_conf, 3)))
        return finalized, rejected

    final_longs, rejected_longs = _finalize(long_raw, "long")
    final_shorts, rejected_shorts = _finalize(short_raw, "short")

    print(f"\n=== 최종 추천 (시장 국면: {regime}, 신뢰도 기준 {confidence_min:.2f}) ===")
    longs_txt = ",".join(f"{t.lower()}:{c}" for t, c in final_longs)
    shorts_txt = ",".join(f"{t.lower()}:{c}" for t, c in final_shorts)
    print(f"long:{longs_txt} / short:{shorts_txt}")
    if rejected_longs or rejected_shorts:
        rej_txt = ", ".join(f"{t}(long,{c})" for t, c in rejected_longs) + \
                  (", " if rejected_longs and rejected_shorts else "") + \
                  ", ".join(f"{t}(short,{c})" for t, c in rejected_shorts)
        print(f"   ⑨ 신뢰도 미달로 제외됨: {rej_txt}")

    # ⑩ 이번에 확정된 추천을 로그에 pending으로 기록 (다음 실행들에서 평가·재보정에 사용)
    now_iso = datetime.now().isoformat()
    for side, picks in (("long", final_longs), ("short", final_shorts)):
        for ticker, conf in picks:
            df = dfs_by_ticker.get(ticker)
            entry_price = float(df['close'].iloc[-1]) if df is not None and len(df) else None
            if entry_price is None:
                continue
            reco_log.append({
                "timestamp": now_iso, "ticker": ticker, "side": side,
                "entry_price": entry_price, "confidence": conf,
                "regime": regime, "status": "pending",
            })
    save_recommendation_log(reco_log)


if __name__ == "__main__":
    main()
