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
import time
import base64
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

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
IMAGE_DETAIL = "low"        # "low"면 이미지 하나당 고정된 적은 토큰만 소모(훨씬 쌈, 대신 세밀한
                            # 패턴은 덜 보임). 정밀하게 보고 싶으면 "high"로 바꾸되 비용 늘어남.

BITHUMB_INTERVAL = "minute60"   # 차트/CSV용 봉 간격 (python_bithumb 규격)
INTERVAL_LABEL = "60min"
CHART_COUNT = 200               # 코인당 캔들 개수

OUT_DIR = os.path.join(SCRIPT_DIR, "grok_run")
os.makedirs(OUT_DIR, exist_ok=True)
# ===========================================

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
    ticker = f"KRW-{coin}"
    df = python_bithumb.get_ohlcv(ticker=ticker, interval=BITHUMB_INTERVAL, count=CHART_COUNT)
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


def build_combined_outputs(tickers):
    """20개 코인 차트를 한 PDF(여러 페이지)로, 데이터를 한 CSV로 합친다."""
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    combined_pdf_path = os.path.join(OUT_DIR, f"charts_{ts}.pdf")
    combined_csv_path = os.path.join(OUT_DIR, f"data_{ts}.csv")

    csv_frames = []
    chart_images_b64 = []
    ok_tickers = []

    with PdfPages(combined_pdf_path) as pdf_pages:
        for coin in tickers:
            print(f"   - {coin} 차트/데이터 생성 중...")
            df = load_data(coin)
            if df is None:
                print(f"     ⚠️ {coin} 데이터 못 가져옴, 건너뜀")
                continue
            fig = build_figure(df, coin)
            pdf_pages.savefig(fig, facecolor=fig.get_facecolor())

            if len(chart_images_b64) < MAX_CHART_IMAGES:
                buf = io.BytesIO()
                fig.savefig(buf, format='png', facecolor=fig.get_facecolor(), bbox_inches='tight')
                chart_images_b64.append((coin, base64.b64encode(buf.getvalue()).decode('utf-8')))
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

    return combined_pdf_path, combined_csv_path, chart_images_b64, ok_tickers


# ============================================================
# ④ Grok API에 보내서 롱3/숏3 추천 받기
# ============================================================
def ask_grok(indicator_pdf_path, combined_csv_path, chart_images_b64, all_tickers,
             regime, regime_detail):
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

    system_prompt = (
        "You are a crypto futures trading analyst. You are given (1) an indicator report with raw "
        "indicator values for the coins currently tracked, (2) recent OHLCV+indicator history samples "
        "per coin, and (3) candlestick chart images per coin. This is NOT a pre-filtered shortlist — "
        "it is the full set of coins currently tracked, with no prior ranking or bias applied. "
        "You must judge every coin yourself from the raw data and decide its direction (if any). "
        f"MARKET REGIME (computed from breadth across all these coins): "
        f"{regime} — {regime_detail} {bias_instruction} "
        f"The candidate pool (same pool for both LONG and SHORT — you decide each coin's direction, "
        f"if any) is: {', '.join(all_tickers)}. "
        "Pick UP TO 3 tickers for LONG and UP TO 3 for SHORT, only from this pool "
        "(never invent tickers outside the pool, never put the same ticker on both sides). "
        "Only include a ticker if you are genuinely confident in it — do NOT pad the list just to "
        "reach 3, and do NOT force a balanced 3-and-3 split just for symmetry. Let the market regime "
        "above and each coin's own setup drive how many you pick on each side — it's fine (and often "
        "correct) for one side to have more picks than the other, or for a side to be completely blank. "
        "Reply with ONLY one line, lowercase tickers, no extra commentary, in exactly this format "
        "(a blank side just has nothing between the colon and the slash):\n"
        "long:xrp,btc,eth / short:doge,bch,mtl\n"
        "long: / short:doge,bch,mtl\n"
        "long:xrp / short:"
    )

    content = [
        {"type": "text", "text": "=== Indicator report (text-extracted from PDF) ===\n" + report_text},
        {"type": "text", "text": "=== Recent OHLCV+indicator sample (last 20 rows per coin) ===\n" + csv_text},
    ]
    for coin, b64 in chart_images_b64:
        content.append({"type": "text", "text": f"--- Chart image: {coin} ---"})
        content.append({"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{b64}", "detail": IMAGE_DETAIL}})

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


def main():
    computed_rows = compute_scores_standalone()

    print("② 바이낸스 상장 코인 후보군 확정 중...")
    all_tickers, all_rows = get_all_tickers(computed_rows)
    print(f"   바이낸스 상장 후보군 {len(all_tickers)}개:", ", ".join(all_tickers))

    regime, composite_score, regime_detail = detect_market_regime(all_rows)
    print(f"   시장 국면: {regime} — {regime_detail}")

    indicator_pdf = generate_indicator_report()

    print(f"④ {len(all_tickers)}개 코인 차트/CSV 생성 중...")
    combined_pdf, combined_csv, images, ok_tickers = build_combined_outputs(all_tickers)
    print("   차트 PDF:", combined_pdf)
    print("   데이터 CSV:", combined_csv)

    answer = ask_grok(indicator_pdf, combined_csv, images, all_tickers,
                       regime, regime_detail)
    print("\n=== Grok 원본 응답 ===")
    print(answer)

    m = re.search(r'long\s*:\s*([a-zA-Z0-9,\s]*?)\s*/\s*short\s*:\s*([a-zA-Z0-9,\s]*)', answer, re.IGNORECASE)
    if m:
        longs = [t.strip().upper() for t in m.group(1).split(',') if t.strip()]
        shorts = [t.strip().upper() for t in m.group(2).split(',') if t.strip()]
        print(f"\n=== 최종 추천 (시장 국면: {regime}) ===")
        # 후보가 없으면(빈 리스트) 그냥 빈칸으로 나온다 — 억지로 3개 채우지 않음
        print(f"long:{','.join(longs).lower()} / short:{','.join(shorts).lower()}")
    else:
        print("\n⚠️ 응답 형식이 예상과 달라 자동 파싱 실패 — 위 원본 텍스트를 직접 확인하세요")


if __name__ == "__main__":
    main()
