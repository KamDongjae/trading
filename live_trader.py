# -*- coding: utf-8 -*-
"""
live_trader.py — Pyramide 판정 신호 → 바이낸스 USDT-M 선물 실전 자동매매 (클라이언트와 별개 프로그램)
=====================================================================================
[2026-11-21 신규]

하는 일
  수집기(data_collector.py)가 쓰는 signal_outcomes_verdict.csv 를 20초마다 읽어서,
  새로 뜬 판정 신호(판정 v2: '4h예상' / '모델 숏')가 아래 조건을 통과하면 바이낸스 선물에
  시장가로 진입하고, 정한 시간(기본 48h)이 지나면 시장가로 정리한다. 중간 손절·익절 없음.

시작할 때 물어보는 것 (그냥 Enter = 일일 리포트 1년 백테스트 추천값)
  레버리지        3배
  보유 시간       48시간
  1회 진입 금액   30 USDT (증거금)

  강한 숏 2배      켬 (4h예상 -1% 이하 숏은 진입 금액 ×2)

진입 조건 (일일 리포트·클라 자동진입과 같은 기준)
  - 바이낸스 USDT 무기한 선물에 있는 코인만 (NOFUT 신호·심볼 없음 → 건너뜀)
  - 같은 코인을 이미 들고 있으면 건너뜀 (봇 포지션이든 수동 포지션이든)
  - 레짐이 하락장이면 롱은 건너뜀 (숏은 그대로)
  - 신호가 뜬 지 10분 넘었으면 건너뜀 (늦은 진입 방지)
  - 사용 가능 잔고가 진입 금액보다 적으면 건너뜀
  - 격리 마진(ISOLATED) → 한 건 최대 손실 = 진입 금액

준비
  1) binance_api_key.txt — 두 줄로 API 키 / 시크릿 (grok API_KEY.txt 와 같은 방식)
        윈도우: D:\binance_api_key.txt   (trading 폴더 밖이라 깃에 안 올라감)
        안드로이드: /storage/emulated/0/Documents/binance_api_key.txt
        위에 없으면 trading 폴더 안의 binance_api_key.txt
        첫 줄: API 키
        둘째 줄: 시크릿 키
     (KEY=..., SECRET=... 형식도 됨)
     API 키는 '선물 거래' 권한만, 출금 권한 OFF, IP 제한(화이트리스트) 꼭 걸기.
  2) 실행:  python live_trader.py          (실거래 — 시작 전 yes 확인)
            python live_trader.py --dry    (주문 없이 가상으로만 기록)
  3) 켜 두는 동안만 동작. 꺼도 거래소 포지션은 그대로고, 다시 켜면 이어서 정리 시간을 관리한다.
     (꺼져 있는 동안 정리 시간이 지났으면 켜자마자 정리)

실행 중 명령어: status / pause / resume / close BTCUSDT / closeall / order / manual / mclose BTCUSDT / quit / help
  (manual = 바이낸스 앱 등에서 직접 연 수동 포지션 보기, mclose = 사용자가 요청할 때만 그 수동 포지션 정리. 봇은 수동 포지션을 절대 자동으로 건드리지 않음)
기록: live_trades.csv(거래), live_log.txt(로그), live_state.json(보유 중 포지션)
"""
import os
import sys
import csv
import json
import time
import hmac
import hashlib
import threading
import argparse
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests
try:
    import sizing_rule  # [2026-11-22] 진입 금액 사용자 정의 규칙(C언어 형식 if/else)
except Exception:
    sizing_rule = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_NAME = "binance_api_key.txt"
# [2026-11-21] grok_recommender 의 API_KEY.txt 와 같은 방식: 윈도우 D:\ → 안드로이드 Documents → 같은 폴더
KEY_CANDIDATES = [os.path.join("D:\\", KEY_NAME),
                  os.path.join("/storage/emulated/0/Documents", KEY_NAME),
                  os.path.join(SCRIPT_DIR, KEY_NAME)]
KEY_FILE = next((p for p in KEY_CANDIDATES if os.path.exists(p)), KEY_CANDIDATES[0])
SIGNAL_CSV = os.path.join(SCRIPT_DIR, "signal_outcomes_verdict.csv")
# [2026-11-23 사용자 요청] 바이낸스 탭 판정(수집기가 바이낸스 캔들·바이낸스 학습 모델로 낸 신호) 파일
SIGNAL_CSV_BINANCE = os.path.join(SCRIPT_DIR, "signal_outcomes_verdict_binance.csv")
# 어느 판정을 따라 주문할지: "binance" = 바이낸스 탭 판정만, "krw" = 빗썸 판정만(예전), 둘 다 = ("krw", "binance")
SIGNAL_SOURCES = ("binance",)
STATE_FILE = os.path.join(SCRIPT_DIR, "live_state.json")
TRADES_CSV = os.path.join(SCRIPT_DIR, "live_trades.csv")
LOG_FILE = os.path.join(SCRIPT_DIR, "live_log.txt")
LOCK_FILE = os.path.join(SCRIPT_DIR, "live_trader.lock")
# [2026-11-22 수동, 사용자 요청] Claude 보고용 사본 — 1분마다 claude_view\\live_status.json(설정·잔고·보유·손익)
#   + live_trades.csv 사본 + live_log.txt 마지막 300줄. 키·시크릿은 절대 안 씀. claude_view/ 는 .gitignore
CLAUDE_VIEW = True
CLAUDE_VIEW_SEC = 60
CLAUDE_VIEW_DIR = os.path.join(SCRIPT_DIR, "claude_view")

BASE_URL = "https://fapi.binance.com"
RECV_WINDOW = 10000

# 일일 리포트 1년 백테스트(25/11~26/11, 바이낸스 28종목, 같은 코인 건너뜀, 1h봉 꼬리 청산) 추천값
#   3배·48h·$30: +$887(+89%), 청산 16, 최악월 -$20  /  5배: +$1,175, 청산 44, 9월 -$91
DEFAULT_LEVERAGE = 3
DEFAULT_HOLD_HOURS = 48.0
DEFAULT_MARGIN_USDT = 30.0

DEFAULT_MARGIN_PCT = 3.0     # [2026-11-22 수동, 사용자 요청] '현금의 %' 모드 기본값
RULE_FILE = os.path.join(SCRIPT_DIR, "live_sizing_rule.txt")  # [2026-11-22] 사용자 정의 진입 금액 규칙(GUI '규칙 편집'에서 저장)
DRY_SEED_USDT = 1000.0       # --dry(모의)에서 '현금의 %' 쓸 때 가상 현금 시작값(정리하면 손익 반영)
MARGIN_TYPE = "ISOLATED"
SKIP_BEAR_LONG = True        # 레짐 하락장이면 롱 건너뜀
MAX_SIGNAL_AGE_MIN = 10.0    # 신호 뜬 지 이 시간(분) 넘으면 진입 안 함
MAX_POSITIONS = 50           # 안전장치: 봇 포지션 동시 최대
POLL_SEC = 20
SIGNAL_REASON_KEYS = ("4h예상", "모델 숏")   # 판정 v2 신호만
# [2026-11-21] 강한 숏(4h예상 ≤ -1.0%)은 진입 금액 ×2 — 1년 백테스트(3배·48h·$30) +$887 → +$1,324,
#   강한 숏 건당 ROI 앞 6개월 +11.8%(66건)·뒤 +11.7%(53건) vs 나머지 +1.5~5.8%. 낙폭 -$119 → -$205
STRONG_SHORT_EXP4 = -1.0
STRONG_MULT = 2.0
# [2026-11-23 수동, 사용자 요청] 강제청산 후 재진입 차단 — 모의 클라에서 11/22 강제청산된 ELSA·PIEVERSE를 11/23에 다시 숏 쳐서
# 또 -63%/-15%. 강제청산된 코인은 같은 방향으로 LIQ_BLOCK_H 동안 자동진입 안 함(수동 주문은 그대로).
# 강제청산 기록은 live_state.json "liq_records"에 누적 보관(지우지 않음). 시작할 때 live_trades.csv의 강제청산 행도 다시 읽음.
# '거래소에서 사라짐'은 그 순간 표시가격 기준 증거금 손실이 LIQ_DETECT_PCT% 이상이면 강제청산으로 본다.
# 되돌리기: LIQ_BLOCK_H = 0 또는 backups\20261123_reentry_guard\live_trader.py
LIQ_BLOCK_H = 168  # [2026-11-24 수동, 사용자 요청] 48h→7일(168h). 바이낸스 백테스트(원화 코인): 청산 14→11, $+564→$+613, 최악월 -$69→-$18. 되돌리기: 48 또는 backups\20261124_liq_block_7d\live_trader.py
LIQ_DETECT_PCT = 70
# 강제청산 기록 사본 — trading 폴더 json이라 auto_git_push.bat로 git에 올라감(금액 없이 코인·방향·시각만). 누적만 하고 안 지움.
LIQ_HISTORY_FILE = os.path.join(SCRIPT_DIR, "liquidations_live.json")

_print_lock = threading.Lock()


def log(msg):
    line = f"[{datetime.now():%m-%d %H:%M:%S}] {msg}"
    with _print_lock:
        print(line, flush=True)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def load_keys():
    """binance_api_key.txt: 두 줄(키/시크릿) 또는 KEY=/SECRET= 형식."""
    if not os.path.exists(KEY_FILE):
        return "", ""
    with open(KEY_FILE, encoding="utf-8-sig") as f:
        lines = [x.strip() for x in f if x.strip() and not x.strip().startswith("#")]
    key = sec = ""
    plain = []
    for s in lines:
        if "=" in s or ":" in s:
            k, v = s.replace(":", "=", 1).split("=", 1)
            k, v = k.strip().upper(), v.strip().strip('"').strip("'")
            if "SECRET" in k:
                sec = v
            elif "KEY" in k:
                key = v
        else:
            plain.append(s)
    if not key and plain:
        key = plain[0]
    if not sec and len(plain) > 1:
        sec = plain[1]
    return key, sec


def parse_exp4(reason):
    """판정 사유 '... 4h예상 -1.23% ...' 에서 4h 예상수익(%) 숫자를 꺼낸다."""
    import re
    m = re.search(r"4h예상\s*([+-]?[0-9.]+)", reason or "")
    return float(m.group(1)) if m else None


def atomic_write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def kst_today_start_ms():
    now = datetime.now()
    return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def acquire_lock():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE) as f:
                pid = int(f.read().strip() or 0)
        except Exception:
            pid = 0
        if pid and _pid_alive(pid):
            print(f"❌ 이미 실행 중입니다 (PID {pid}). 두 개 켜면 주문이 두 번 나갑니다.")
            print(f"   확실히 꺼져 있다면 {LOCK_FILE} 를 지우고 다시 실행하세요.")
            sys.exit(1)
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))


def _pid_alive(pid):
    if os.name == "nt":
        try:
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not h:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return code.value == 259
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def release_lock():
    try:
        os.remove(LOCK_FILE)
    except Exception:
        pass


# ------------------------------------------------------------------
# 바이낸스 USDT-M 선물 REST (binance_executor.py 와 같은 코드)
# ------------------------------------------------------------------
class BinanceError(Exception):
    def __init__(self, code, msg):
        super().__init__(f"{code}: {msg}")
        self.code = code
        self.msg = msg


class BinanceFutures:
    def __init__(self, key, secret):
        self.key = key
        self.secret = secret.encode()
        self.s = requests.Session()
        self.s.headers.update({"X-MBX-APIKEY": key})
        self.time_offset = 0
        self.symbols = {}       # symbol -> filters
        self.hedge_mode = False

    def _req(self, method, path, params=None, signed=False, retries=3):
        params = dict(params or {})
        for attempt in range(retries):
            p = dict(params)
            if signed:
                p["timestamp"] = int(time.time() * 1000) + self.time_offset
                p["recvWindow"] = RECV_WINDOW
                q = urlencode(p)
                q += "&signature=" + hmac.new(self.secret, q.encode(), hashlib.sha256).hexdigest()
            else:
                q = urlencode(p)
            url = BASE_URL + path + ("?" + q if q else "")
            try:
                r = self.s.request(method, url, timeout=10)
            except requests.RequestException as e:
                if attempt == retries - 1:
                    raise BinanceError(-1, f"네트워크 오류: {e}")
                time.sleep(1.5 * (attempt + 1))
                continue
            try:
                data = r.json()
            except Exception:
                data = {"code": r.status_code, "msg": r.text[:200]}
            if r.status_code == 200:
                return data
            code = data.get("code", r.status_code) if isinstance(data, dict) else r.status_code
            msg = data.get("msg", "") if isinstance(data, dict) else str(data)
            if code == -1021 and attempt < retries - 1:   # 타임스탬프 어긋남
                self.sync_time()
                continue
            if r.status_code in (429, 418) or (r.status_code >= 500 and attempt < retries - 1):
                time.sleep(3 * (attempt + 1))
                continue
            raise BinanceError(code, msg)
        raise BinanceError(-1, "재시도 초과")

    # --- 공개 ---
    def sync_time(self):
        t = self._req("GET", "/fapi/v1/time")["serverTime"]
        self.time_offset = int(t) - int(time.time() * 1000)

    def load_symbols(self):
        info = self._req("GET", "/fapi/v1/exchangeInfo")
        out = {}
        for s in info.get("symbols", []):
            if s.get("contractType") != "PERPETUAL" or s.get("quoteAsset") != "USDT" or s.get("status") != "TRADING":
                continue
            f = {x["filterType"]: x for x in s.get("filters", [])}
            lot = f.get("MARKET_LOT_SIZE") or f.get("LOT_SIZE") or {}
            out[s["symbol"]] = {
                "step": Decimal(lot.get("stepSize", "1")),
                "min_qty": Decimal(lot.get("minQty", "0")),
                "max_qty": Decimal(lot.get("maxQty", "0") or "0"),
                "min_notional": float((f.get("MIN_NOTIONAL") or {}).get("notional", 5)),
            }
        self.symbols = out
        return out

    def mark_price(self, symbol):
        return float(self._req("GET", "/fapi/v1/premiumIndex", {"symbol": symbol})["markPrice"])

    # --- 서명 ---
    def load_position_mode(self):
        d = self._req("GET", "/fapi/v1/positionSide/dual", signed=True)
        self.hedge_mode = bool(d.get("dualSidePosition"))
        return self.hedge_mode

    def usdt_balance(self):
        for path in ("/fapi/v3/balance", "/fapi/v2/balance"):
            try:
                for b in self._req("GET", path, signed=True):
                    if b.get("asset") == "USDT":
                        return float(b.get("availableBalance", 0)), float(b.get("balance", 0))
                return 0.0, 0.0
            except BinanceError:
                if path.endswith("v2/balance"):
                    raise
        return 0.0, 0.0

    def positions(self):
        """symbol -> list of {side LONG/SHORT, qty(abs), entry, mark, upnl, positionSide}"""
        data = None
        for path in ("/fapi/v3/positionRisk", "/fapi/v2/positionRisk"):
            try:
                data = self._req("GET", path, signed=True)
                break
            except BinanceError:
                if path.endswith("v2/positionRisk"):
                    raise
        out = {}
        for p in data or []:
            amt = float(p.get("positionAmt", 0))
            if amt == 0:
                continue
            out.setdefault(p["symbol"], []).append({
                "side": "LONG" if amt > 0 else "SHORT",
                "qty": abs(amt),
                "entry": float(p.get("entryPrice", 0)),
                "mark": float(p.get("markPrice", 0)),
                "upnl": float(p.get("unRealizedProfit", 0) or p.get("unrealizedProfit", 0) or 0),
                "positionSide": p.get("positionSide", "BOTH"),
            })
        return out

    def set_margin_type(self, symbol, mtype):
        try:
            self._req("POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": mtype}, signed=True)
        except BinanceError as e:
            if e.code != -4046:   # No need to change margin type
                raise

    def set_leverage(self, symbol, lev):
        """lev 부터 내려가며 허용되는 최대값으로 설정. 실제 적용된 레버리지 반환."""
        lev = int(max(1, lev))
        while lev >= 1:
            try:
                d = self._req("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": lev}, signed=True)
                return int(d.get("leverage", lev))
            except BinanceError as e:
                if e.code in (-4028, -2027, -4161) and lev > 1:
                    lev -= 1
                    continue
                raise
        return 1

    def market_order(self, symbol, side, qty_str, position_side=None, reduce_only=False):
        p = {"symbol": symbol, "side": side, "type": "MARKET", "quantity": qty_str,
             "newOrderRespType": "RESULT"}
        if self.hedge_mode:
            p["positionSide"] = position_side
        elif reduce_only:
            p["reduceOnly"] = "true"
        return self._req("POST", "/fapi/v1/order", p, signed=True, retries=1)

    def income_today(self):  # noqa
        """오늘(KST 0시~) 실현손익+수수료+펀딩 합계."""
        total = 0.0
        start = kst_today_start_ms()
        for it in ("REALIZED_PNL", "COMMISSION", "FUNDING_FEE"):
            try:
                rows = self._req("GET", "/fapi/v1/income",
                                 {"incomeType": it, "startTime": start, "limit": 1000}, signed=True)
                total += sum(float(r.get("income", 0)) for r in rows if r.get("asset") == "USDT")
            except BinanceError as e:
                log(f"⚠️ 손익 조회 실패({it}): {e}")
        return total


def floor_step(x, step):
    d = (Decimal(str(x)) / step).to_integral_value(rounding=ROUND_DOWN) * step
    return d.normalize() if d != 0 else Decimal(0)


def fmt_qty(d):
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"




# ------------------------------------------------------------------
# 실전 자동매매
# ------------------------------------------------------------------
class Trader:
    def __init__(self, lev, hold_h, margin, dry=False, strong_x2=True, margin_pct=None, margin_rule=None, start_seed=None):
        self.lev, self.hold_h, self.margin = int(lev), float(hold_h), float(margin)
        # [2026-11-22] margin_pct 를 주면 1회 진입 = 그 시점 현금(실거래: 사용 가능 USDT, 모의: 가상 현금)의 %
        self.margin_pct = float(margin_pct) if margin_pct else None
        # [2026-11-22] margin_rule(규칙 글) 을 주면 sizing_rule 로 1회 진입 금액 계산(start_seed/current_seed 등)
        self.margin_rule = None
        if margin_rule:
            if not sizing_rule:
                raise RuntimeError("sizing_rule.py 가 없어서 사용자 정의 진입 금액을 쓸 수 없음")
            self.margin_rule = sizing_rule.parse(margin_rule)
            self.margin_rule_src = margin_rule
        self._start_seed_arg = float(start_seed) if start_seed else None
        self.strong_x2 = bool(strong_x2)
        self.dry = dry
        self.paused = False
        self.stop = False
        self.lock = threading.RLock()
        key, sec = load_keys()
        if not dry and (not key or not sec):
            print(f"❌ API 키/시크릿을 못 읽었습니다. 찾은 위치: {', '.join(KEY_CANDIDATES)}")
            print("   D:\\binance_api_key.txt 를 만들고 첫 줄에 API 키, 둘째 줄에 시크릿 키를 넣어 주세요.")
            sys.exit(1)
        self.bn = BinanceFutures(key or "x", sec or "x")
        self.state = {"seen": [], "trades": {}}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, encoding="utf-8") as f:
                    self.state.update(json.load(f))
            except Exception as e:
                log(f"⚠️ 상태 파일 읽기 실패: {e}")
        self.seen = set(self.state.get("seen", []))
        self._seed_liq_records()
        self._last_symbols_load = 0
        if dry and "dry_cash" not in self.state:
            self.state["dry_cash"] = DRY_SEED_USDT
        if self._start_seed_arg:
            self.state["start_seed"] = self._start_seed_arg

    def start_seed(self):
        """처음 시드: 시작할 때 넣은 값 → 없으면 처음 실행 때 현금을 기록해 두고 계속 씀(live_state.json start_seed)."""
        v = self.state.get("start_seed")
        if not v:
            v = self.cash()
            self.state["start_seed"] = v
        return float(v)

    def cash(self):
        """현금: 실거래 = 바이낸스 사용 가능 USDT(포지션 증거금 제외), 모의 = 가상 현금."""
        if self.dry:
            return float(self.state.get("dry_cash", DRY_SEED_USDT))
        return self.bn.usdt_balance()[0]

    def equity(self):
        """[2026-11-22] 총자산 = 현금 + 보유 포지션 증거금 + 평가손익.
        실거래: 바이낸스 지갑 잔고(증거금 포함) + 모든 포지션 미실현 손익 / 모의: 가상 현금 + 봇 포지션 증거금 + 추정 손익."""
        if not self.dry:
            _, wallet = self.bn.usdt_balance()
            upnl = sum(p["upnl"] for lst in self.bn.positions().values() for p in lst)
            return wallet + upnl
        eq = self.cash()
        for t in self.open_trades().values():
            eq += float(t.get("margin", 0))
            try:
                mark = self.bn.mark_price(t["symbol"])
                sign = 1 if t["direction"] == "LONG" else -1
                eq += (mark / float(t["entry_price"]) - 1) * sign * float(t["qty"]) * float(t["entry_price"])
            except Exception:
                pass
        return eq

    def amount_text(self):
        if self.margin_rule:
            return "사용자 정의 규칙"
        return f"현금의 {self.margin_pct:g}%" if self.margin_pct else f"{self.margin:g} USDT"

    # --- 저장 ---
    def save_state(self):
        with self.lock:
            self.state["seen"] = sorted(self.seen)[-3000:]
            try:
                atomic_write_json(STATE_FILE, self.state)
            except Exception as e:
                log(f"⚠️ 상태 저장 실패: {e}")

    def write_trade(self, t):
        new = not os.path.exists(TRADES_CSV)
        cols = ["event_id", "symbol", "direction", "reason", "regime", "mode", "leverage", "margin", "qty",
                "entry_time", "entry_price", "hold_hours", "exit_time", "exit_price", "close_reason", "pnl_usdt_est"]
        try:
            with open(TRADES_CSV, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                if new:
                    w.writeheader()
                w.writerow(t)
        except Exception as e:
            log(f"⚠️ 거래 기록 저장 실패: {e}")

    # --- 시작 ---
    def startup(self):
        self.bn.sync_time()
        self.bn.load_symbols()
        self._last_symbols_load = time.time()
        log(f"바이낸스 USDT 무기한 선물 {len(self.bn.symbols)}종목 로드")
        if not self.dry:
            hedge = self.bn.load_position_mode()
            log(f"포지션 모드: {'헤지(양방향)' if hedge else '단방향(One-way)'}")
            avail, total = self.bn.usdt_balance()
            log(f"USDT 잔고: 사용 가능 {avail:,.2f} / 총 {total:,.2f}")
            self.reconcile()
        rows = self.read_signals()
        before = len(self.seen)
        for r in rows:
            self.seen.add(r.get("event_id", ""))
        log(f"이미 떠 있던 신호 {len(self.seen) - before}건은 건너뜀 — 지금부터 새 신호만 진입")
        self.save_state()

    def reconcile(self):
        try:
            pos = self.bn.positions()
        except BinanceError as e:
            log(f"⚠️ 포지션 조회 실패: {e}")
            return
        with self.lock:
            for eid, t in list(self.state["trades"].items()):
                if t.get("mode") != "live":
                    continue
                if not [p for p in pos.get(t["symbol"], []) if p["side"] == t["direction"]]:
                    log(f"ℹ️ {t['symbol']} {t['direction']} 포지션이 거래소에 없음 → 수동/강제 청산으로 기록")
                    self._finish(eid, t, None, "거래소에서 사라짐(수동 또는 강제청산)")
        self.save_state()

    # --- 신호 ---
    def read_signals(self):
        """[2026-11-23] SIGNAL_SOURCES 에 있는 파일을 다 읽는다. 바이낸스 탭 신호는 event_id 앞에 'bn_'(빗썸 것과 안 겹치게)."""
        out = []
        for src, path in (("krw", SIGNAL_CSV), ("binance", SIGNAL_CSV_BINANCE)):
            if src not in SIGNAL_SOURCES:
                continue
            for _ in range(5):
                try:
                    if not os.path.exists(path):
                        break
                    with open(path, newline="", encoding="utf-8") as f:
                        rows = list(csv.DictReader(f))
                    if src == "binance":
                        for r in rows:
                            r["event_id"] = "bn_" + (r.get("event_id") or "")
                    out += rows
                    break
                except (PermissionError, OSError):
                    time.sleep(0.5)
                except Exception as e:
                    log(f"⚠️ 신호 CSV 읽기 실패({os.path.basename(path)}): {e}")
                    break
        return out

    def to_symbol(self, ticker):
        t = ticker.upper().strip()
        for cand in (f"{t}USDT", f"1000{t}USDT", f"1000000{t}USDT", f"1M{t}USDT"):
            if cand in self.bn.symbols:
                return cand
        return None

    # --- 수동 포지션(봇이 안 연 것): 보기만, 사용자가 요청할 때만 정리 ---
    def manual_positions(self):
        """거래소 포지션 중 봇이 연 것이 아닌 것 목록. 모의 모드면 빈 목록."""
        if self.dry:
            return []
        bot = {(t["symbol"], t["direction"]) for t in self.open_trades().values()}
        out = []
        for sym, lst in self.bn.positions().items():
            for p in lst:
                if (sym, p["side"]) not in bot:
                    out.append(dict(p, symbol=sym))
        return out

    def close_manual(self, symbol, side):
        """수동 포지션 하나를 시장가로 정리(사용자 요청 때만 호출)."""
        if self.dry:
            log("ℹ️ 모의 모드에선 수동 포지션을 정리하지 않음")
            return False
        if any(t["symbol"] == symbol and t["direction"] == side for t in self.open_trades().values()):
            log(f"ℹ️ {symbol} {side} 는 봇 포지션이라 수동 정리 대상 아님 — close 를 써")
            return False
        lst = [p for p in self.bn.positions().get(symbol, []) if p["side"] == side]
        if not lst:
            log(f"ℹ️ {symbol} {side} 수동 포지션 없음")
            return False
        if symbol not in self.bn.symbols:
            self.bn.load_symbols()
        info = self.bn.symbols.get(symbol)
        qty = floor_step(lst[0]["qty"], info["step"]) if info else Decimal(str(lst[0]["qty"]))
        try:
            res = self.bn.market_order(symbol, "SELL" if side == "LONG" else "BUY", fmt_qty(qty),
                                       position_side=side, reduce_only=True)
        except BinanceError as e:
            log(f"❌ {symbol} 수동 포지션 정리 실패: {e}")
            return False
        px = float(res.get("avgPrice") or 0) or lst[0]["mark"]
        log(f"🔚 수동 포지션 정리 {symbol} {side} 수량 {fmt_qty(qty)} @ {px:g} (미실현손익 약 {lst[0]['upnl']:+.2f} USDT)")
        return True

    def open_trades(self):
        return {k: v for k, v in self.state["trades"].items() if v.get("status") == "open"}

    def check_signals(self):
        rows = self.read_signals()
        now = datetime.now()
        for r in rows:
            eid = r.get("event_id", "")
            if not eid or eid in self.seen:
                continue
            self.seen.add(eid)
            try:
                self.consider(r, now)
            except Exception as e:
                log(f"❌ {eid} 처리 중 오류: {e}")
        self.save_state()

    def consider(self, r, now):
        eid, ticker, direction = r["event_id"], r.get("ticker", ""), r.get("direction", "")
        reason, regime = r.get("reason", ""), r.get("regime", "")
        tag = f"{ticker} {direction} ({reason})"

        def skip(why):
            log(f"⏭  {tag} 건너뜀: {why}")

        if self.paused:
            return skip("일시정지 중")
        if direction not in ("LONG", "SHORT"):
            return skip("방향 없음")
        if not any(k in reason for k in SIGNAL_REASON_KEYS):
            return skip("판정 v2 신호 아님")
        try:
            age = (now - datetime.strptime(r["start"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
        except Exception:
            age = 0
        if age > MAX_SIGNAL_AGE_MIN:
            return skip(f"신호가 {age:.0f}분 지남")
        if "NOFUT" in (r.get("liq_risk") or ""):
            return skip("바이낸스 선물 없음")
        if SKIP_BEAR_LONG and direction == "LONG" and regime == "하락장":
            return skip("하락장 롱 제외")
        if time.time() - self._last_symbols_load > 6 * 3600:
            try:
                self.bn.load_symbols()
                self._last_symbols_load = time.time()
            except Exception:
                pass
        symbol = self.to_symbol(ticker)
        if not symbol:
            return skip("바이낸스 USDT 무기한 심볼 없음")
        lq = self.liq_block_until(symbol, direction)
        if lq:
            return skip(f"강제청산 후 {LIQ_BLOCK_H:g}h 차단(~{lq:%m-%d %H:%M})")
        opens = self.open_trades()
        if any(t["symbol"] == symbol for t in opens.values()):
            return skip("이미 이 코인 보유 중(봇)")
        if len(opens) >= MAX_POSITIONS:
            return skip(f"동시 포지션 {MAX_POSITIONS}개 한도")
        mult = 1.0
        if self.strong_x2 and direction == "SHORT":
            e4 = parse_exp4(reason)
            if e4 is not None and e4 <= STRONG_SHORT_EXP4:
                mult = STRONG_MULT
        self.open_position(eid, symbol, direction, reason, regime, mult)

    def open_position(self, eid, symbol, direction, reason, regime, mult=1.0, lev=None, margin=None,
                      hold=None, record=True):
        """record=False 면 주문만 넣고 봇 목록에 안 넣음(= 수동 포지션, 자동 정리 안 함). 성공하면 True."""
        info = self.bn.symbols[symbol]
        mark = self.bn.mark_price(symbol)
        lev = int(lev or self.lev)
        if margin is None and self.margin_rule:
            cash, seed, eq = self.cash(), self.start_seed(), self.equity()
            e4 = parse_exp4(reason) if direction == "SHORT" else None
            strong = 1 if (e4 is not None and e4 <= STRONG_SHORT_EXP4) else 0
            try:
                margin = sizing_rule.run(self.margin_rule, seed, eq, len(self.open_trades()),
                                         1 if direction == "SHORT" else 0, strong, lev, cash=cash)
            except Exception as e:
                log(f"❌ {symbol} 사용자 정의 규칙 오류: {e} — 진입 안 함")
                return False
            log(f"💵 {symbol} 사용자 정의: start_seed {seed:,.2f} / current_seed(총자산) {eq:,.2f} / cash {cash:,.2f} → position_size {margin:g} USDT")
            if margin < 1:
                log(f"⏭  {symbol} 건너뜀: 사용자 정의 결과 {margin:g} USDT")
                return False
        elif margin is None and self.margin_pct:
            cash = self.cash()
            margin = round(cash * self.margin_pct / 100.0, 2)
            log(f"💵 {symbol} 현금 {cash:,.2f} USDT 의 {self.margin_pct:g}% = {margin:g} USDT")
        margin = float(margin if margin is not None else self.margin) * mult
        hold = float(hold if hold is not None else self.hold_h)
        if mult != 1.0:
            log(f"💪 {symbol} 강한 숏(4h예상 ≤ {STRONG_SHORT_EXP4}%) → 진입 금액 ×{mult:g} = {margin:g} USDT")
        if not self.dry:
            avail, _ = self.bn.usdt_balance()
            if avail < margin * 1.05:
                log(f"⏭  {symbol} 건너뜀: 사용 가능 잔고 {avail:.2f} < 진입 금액 {margin:.2f}")
                return False
            if symbol in self.bn.positions():
                log(f"⏭  {symbol} 건너뜀: 거래소에 이미 이 코인 포지션 있음(수동 포함)")
                return False
            self.bn.set_margin_type(symbol, MARGIN_TYPE)
            real = self.bn.set_leverage(symbol, lev)
            if real != lev:
                log(f"ℹ️ {symbol} {lev}배 불가 → {real}배 적용")
            lev = real
        qty = floor_step(margin * lev / mark, info["step"])
        if info["max_qty"] > 0 and qty > info["max_qty"]:
            qty = floor_step(info["max_qty"], info["step"])
        if qty <= 0 or qty < info["min_qty"] or float(qty) * mark < info["min_notional"]:
            log(f"⏭  {symbol} 건너뜀: 주문 금액 {float(qty) * mark:.2f} USDT 가 최소 주문 {info['min_notional']} USDT 미달 "
                f"— 진입 금액이나 레버리지를 올려야 함")
            return False
        side = "BUY" if direction == "LONG" else "SELL"
        entry = mark
        if not self.dry:
            try:
                res = self.bn.market_order(symbol, side, fmt_qty(qty), position_side=direction)
            except BinanceError as e:
                log(f"❌ {symbol} 진입 주문 실패: {e}")
                return False
            entry = float(res.get("avgPrice") or 0) or mark
            qty = Decimal(str(res.get("executedQty") or qty))
        liq = entry * (1 - 0.9 / lev) if direction == "LONG" else entry * (1 + 0.9 / lev)
        if not record:
            log(f"✅ {'[모의] ' if self.dry else ''}수동 주문 {symbol} {direction} {lev}배 ${margin:.2f} 수량 {fmt_qty(qty)} "
                f"@ {entry:g} (대략 청산가 {liq:g}) — 봇이 관리 안 함(직접 정리)")
            return True
        t = {"event_id": eid, "symbol": symbol, "direction": direction, "reason": reason, "regime": regime,
             "mode": "dry" if self.dry else "live", "leverage": lev, "margin": margin, "qty": fmt_qty(qty),
             "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "entry_price": entry,
             "hold_hours": hold, "status": "open"}
        with self.lock:
            self.state["trades"][eid] = t
            if self.dry:
                self.state["dry_cash"] = round(float(self.state.get("dry_cash", DRY_SEED_USDT)) - margin, 4)
        self.save_state()
        log(f"✅ {'[모의] ' if self.dry else ''}진입 {symbol} {direction} {lev}배 ${margin:.2f} 수량 {fmt_qty(qty)} "
            f"@ {entry:g} (대략 청산가 {liq:g}, {hold:g}h 뒤 정리) — {reason}")
        return True

    def manual_open(self, ticker, direction, margin, lev, managed=True, hold=None):
        """사용자 수동 주문. managed=True 면 봇 목록에 넣어 hold 시간 뒤 자동 정리, False 면 수동 포지션(직접 정리)."""
        direction = direction.upper()
        if direction not in ("LONG", "SHORT"):
            log("❌ 방향은 LONG/SHORT")
            return False
        t = ticker.upper().strip()
        symbol = t if t in self.bn.symbols else self.to_symbol(t[:-4] if t.endswith("USDT") else t)
        if not symbol:
            log(f"❌ {ticker}: 바이낸스 USDT 무기한 심볼 없음")
            return False
        if any(x["symbol"] == symbol for x in self.open_trades().values()):
            log(f"⏭  {symbol}: 이미 봇이 들고 있는 코인")
            return False
        eid = f"manual_{datetime.now():%Y%m%d%H%M%S}_{symbol}_{direction}"
        log(f"🖐  수동 주문 요청 {symbol} {direction} {lev}배 ${float(margin):g} "
            + (f"(봇이 {float(hold if hold is not None else self.hold_h):g}h 뒤 정리)" if managed else "(직접 정리)"))
        return self.open_position(eid, symbol, direction, "수동 주문", "", 1.0, lev=lev, margin=margin,
                                  hold=hold, record=managed)

    # --- 보유 관리 ---
    def manage(self):
        opens = self.open_trades()
        if not opens:
            return
        pos = {}
        if not self.dry:
            try:
                pos = self.bn.positions()
            except BinanceError as e:
                log(f"⚠️ 포지션 조회 실패: {e}")
                return
        now = datetime.now()
        for eid, t in opens.items():
            try:
                if not self.dry and not [p for p in pos.get(t["symbol"], []) if p["side"] == t["direction"]]:
                    log(f"ℹ️ {t['symbol']} {t['direction']} 포지션이 거래소에 없음 → 강제청산/수동청산으로 기록")
                    self._finish(eid, t, None, "거래소에서 사라짐(수동 또는 강제청산)")
                    continue
                held = (now - datetime.strptime(t["entry_time"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
                if held >= float(t.get("hold_hours", self.hold_h)):
                    self.close_trade(eid, t, f"{float(t.get('hold_hours', self.hold_h)):g}h 보유 만료")
                elif self.dry:
                    mark = self.bn.mark_price(t["symbol"])
                    sign = 1 if t["direction"] == "LONG" else -1
                    if (mark / float(t["entry_price"]) - 1) * 100 * sign * float(t["leverage"]) <= -90:
                        self.close_trade(eid, t, "강제청산(가상)", mark)
            except Exception as e:
                log(f"⚠️ {t.get('symbol')} 관리 중 오류: {e}")
        self.save_state()

    def close_trade(self, eid, t, why, mark=None):
        symbol, direction = t["symbol"], t["direction"]
        exit_price = mark
        if not self.dry:
            try:
                lst = [p for p in self.bn.positions().get(symbol, []) if p["side"] == direction]
            except BinanceError as e:
                log(f"❌ {symbol} 정리 전 포지션 조회 실패: {e}")
                return
            if not lst:
                self._finish(eid, t, None, "거래소에서 사라짐(수동 또는 강제청산)")
                return
            info = self.bn.symbols.get(symbol)
            qty = floor_step(lst[0]["qty"], info["step"]) if info else Decimal(str(lst[0]["qty"]))
            side = "SELL" if direction == "LONG" else "BUY"
            try:
                res = self.bn.market_order(symbol, side, fmt_qty(qty), position_side=direction, reduce_only=True)
                exit_price = float(res.get("avgPrice") or 0) or lst[0]["mark"]
            except BinanceError as e:
                log(f"❌ {symbol} 정리 주문 실패: {e} — 다음 주기에 다시 시도")
                return
        elif exit_price is None:
            exit_price = self.bn.mark_price(symbol)
        self._finish(eid, t, exit_price, why)

    # [2026-11-23] 강제청산 기록·차단
    def _seed_liq_records(self):
        recs = self.state.setdefault("liq_records", [])
        known = {r.get("event_id") for r in recs}
        try:
            if os.path.exists(TRADES_CSV):
                with open(TRADES_CSV, encoding="utf-8-sig") as f:
                    for r in csv.DictReader(f):
                        if "강제청산" in (r.get("close_reason") or "") and r.get("event_id") not in known:
                            recs.append({"event_id": r.get("event_id"), "symbol": r.get("symbol"),
                                         "direction": r.get("direction"), "exit_time": r.get("exit_time"),
                                         "why": r.get("close_reason")})
                            known.add(r.get("event_id"))
            for r in recs:
                self._liq_history_add(r)
        except Exception as e:
            log(f"⚠️ 강제청산 기록 읽기 실패: {e}")

    def _liq_history_load(self):
        try:
            with open(LIQ_HISTORY_FILE, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, list) else []
        except Exception:
            return []

    def _liq_history_add(self, rec):
        """git 기록 파일에 한 줄 추가(다시 읽어서 합친 뒤 저장, 기존 기록은 안 지움)."""
        try:
            hist = self._liq_history_load()
            key = (rec.get("event_id"), rec.get("exit_time"))
            if key not in {(r.get("event_id"), r.get("exit_time")) for r in hist}:
                hist.append({k: rec.get(k) for k in ("event_id", "symbol", "direction", "exit_time", "why")})
                atomic_write_json(LIQ_HISTORY_FILE, hist)
        except Exception as e:
            log(f"⚠️ 강제청산 기록 파일 저장 실패: {e}")

    def liq_block_until(self, symbol, direction):
        if not LIQ_BLOCK_H:
            return None
        until = None
        for r in self.state.get("liq_records", []) + self._liq_history_load():
            if r.get("symbol") != symbol or r.get("direction") != direction:
                continue
            try:
                u = datetime.strptime(str(r.get("exit_time"))[:19], "%Y-%m-%d %H:%M:%S") + timedelta(hours=LIQ_BLOCK_H)
            except Exception:
                continue
            until = max(until, u) if until else u
        return until if until and until > datetime.now() else None

    def _is_liquidation(self, t, exit_price, why):
        if "강제청산" in why:
            return True
        if "사라짐" not in why:
            return False
        try:
            mark = exit_price or self.bn.mark_price(t["symbol"])
            sign = 1 if t["direction"] == "LONG" else -1
            return (mark / float(t["entry_price"]) - 1) * 100 * sign * float(t["leverage"]) <= -LIQ_DETECT_PCT
        except Exception:
            return False

    def _finish(self, eid, t, exit_price, why):
        try:
            if self._is_liquidation(t, exit_price, why):
                rec = {"event_id": eid, "symbol": t["symbol"], "direction": t["direction"],
                       "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "why": why}
                with self.lock:
                    self.state.setdefault("liq_records", []).append(rec)
                self._liq_history_add(rec)
                log(f"⛔ {t['symbol']} {t['direction']} 강제청산으로 기록 — {LIQ_BLOCK_H:g}h 같은 방향 자동진입 차단")
        except Exception as e:
            log(f"⚠️ 강제청산 판정 실패: {e}")
        pnl = ""
        if exit_price:
            sign = 1 if t["direction"] == "LONG" else -1
            notional = float(t["qty"]) * float(t["entry_price"])
            pnl = round((exit_price / float(t["entry_price"]) - 1) * sign * notional - notional * 0.0005 * 2, 4)
        t.update({"status": "closed", "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                  "exit_price": exit_price or "", "close_reason": why, "pnl_usdt_est": pnl})
        self.write_trade(t)
        with self.lock:
            self.state["trades"].pop(eid, None)
            if self.dry and t.get("mode") == "dry":
                back = float(t.get("margin", 0)) + (pnl if pnl != "" else 0)
                self.state["dry_cash"] = round(float(self.state.get("dry_cash", DRY_SEED_USDT)) + max(back, 0), 4)
        log(f"🔚 {'[모의] ' if self.dry else ''}정리 {t['symbol']} {t['direction']} — {why}"
            + (f" 추정손익 {pnl:+.2f} USDT" if pnl != "" else ""))

    # --- 상태 ---
    def print_status(self):
        lines = ["──────── 설정 ────────",
                 f" 모드       : {'모의(주문 안 냄)' if self.dry else '실거래'}{'  [일시정지]' if self.paused else ''}",
                 f" 레버리지   : {self.lev}배 ({MARGIN_TYPE})",
                 f" 보유 시간  : {self.hold_h:g}h 뒤 정리",
                 f" 1회 진입   : {self.amount_text()}" + (f" (강한 숏은 ×{STRONG_MULT:g})" if self.strong_x2 else ""),
                 f" 조건       : 바이낸스 선물 코인만 · 같은 코인 보유 중 건너뜀 · 하락장 롱 제외"]
        if not self.dry:
            try:
                avail, total = self.bn.usdt_balance()
                lines.append(f" 잔고       : 사용 가능 {avail:,.2f} / 총 {total:,.2f} USDT")
            except Exception:
                pass
        else:
            lines.append(f" 가상 현금  : {self.cash():,.2f} USDT")
        opens = self.open_trades()
        lines.append(f"──────── 보유 {len(opens)}개 ────────")
        for t in opens.values():
            try:
                mark = self.bn.mark_price(t["symbol"])
                sign = 1 if t["direction"] == "LONG" else -1
                mr = (mark / float(t["entry_price"]) - 1) * 100 * sign * float(t["leverage"])
                held = (datetime.now() - datetime.strptime(t["entry_time"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
                lines.append(f" {t['symbol']:<14}{t['direction']:<6}{t['leverage']}배 ${t['margin']} "
                             f"진입 {float(t['entry_price']):g} 현재 {mark:g} 증거금 {mr:+.1f}% "
                             f"보유 {held:.1f}/{float(t['hold_hours']):g}h")
            except Exception:
                lines.append(f" {t['symbol']} {t['direction']} (가격 조회 실패)")
        with _print_lock:
            print("\n".join(lines), flush=True)

    def write_claude_view(self):
        if not CLAUDE_VIEW:
            return
        try:
            os.makedirs(CLAUDE_VIEW_DIR, exist_ok=True)
            st = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "pid": os.getpid(),
                  "mode": "dry" if self.dry else "real", "paused": self.paused,
                  "leverage": self.lev, "hold_h": self.hold_h, "margin": self.margin, "margin_pct": self.margin_pct,
                  "margin_rule": getattr(self, "margin_rule_src", None), "start_seed": self.state.get("start_seed"),
                  "strong_x2": self.strong_x2, "dry_cash": self.state.get("dry_cash") if self.dry else None,
                  "balance_avail": None, "balance_total": None, "open": []}
            if not self.dry:
                try:
                    st["balance_avail"], st["balance_total"] = self.bn.usdt_balance()
                except Exception as e:
                    st["balance_error"] = str(e)[:200]
            for eid, t in self.open_trades().items():
                row = {k: t.get(k) for k in ("symbol", "direction", "leverage", "margin", "entry_time",
                                             "entry_price", "hold_hours", "reason", "mode")}
                row["event_id"] = eid
                try:
                    mark = self.bn.mark_price(t["symbol"])
                    sign = 1 if t["direction"] == "LONG" else -1
                    row["mark"] = mark
                    row["margin_pnl_pct"] = round((mark / float(t["entry_price"]) - 1) * 100 * sign * float(t["leverage"]), 2)
                except Exception:
                    pass
                st["open"].append(row)
            out = os.path.join(CLAUDE_VIEW_DIR, "live_status.json")
            atomic_write_json(out, st)
            if os.path.exists(TRADES_CSV):
                import shutil
                dst = os.path.join(CLAUDE_VIEW_DIR, "live_trades.csv")
                shutil.copyfile(TRADES_CSV, dst + ".tmp")
                os.replace(dst + ".tmp", dst)
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
                    tail = f.readlines()[-300:]
                dst = os.path.join(CLAUDE_VIEW_DIR, "live_log_tail.txt")
                with open(dst + ".tmp", "w", encoding="utf-8") as f:
                    f.writelines(tail)
                os.replace(dst + ".tmp", dst)
        except Exception as e:
            log(f"⚠️ claude_view 저장 실패: {e}")

    def run(self):
        last_sync = time.time()
        while not self.stop:
            try:
                if time.time() - last_sync > 3600:
                    self.bn.sync_time()
                    last_sync = time.time()
                self.manage()
                self.check_signals()
                if time.time() - getattr(self, "_claude_view_t", 0) >= CLAUDE_VIEW_SEC:  # [2026-11-22]
                    self._claude_view_t = time.time()
                    self.write_claude_view()
            except Exception as e:
                log(f"⚠️ 루프 오류: {e}")
            for _ in range(POLL_SEC):
                if self.stop:
                    break
                time.sleep(1)


HELP = """
명령어 (입력 후 Enter):
  status          설정·잔고·보유 포지션 보기
  pause / resume  새 진입 멈춤 / 다시 시작 (보유 포지션 정리는 계속됨)
  close BTCUSDT   그 포지션 바로 정리
  closeall        봇 포지션 전부 바로 정리
  order ZIL short 30 3 [auto|keep]  수동 주문: 코인 방향 금액(USDT) 레버리지
                  auto(기본) = 봇이 정한 시간 뒤 정리 / keep = 수동 포지션으로 두고 직접 정리
  manual          수동 포지션(바이낸스 앱 등에서 직접 연 것) 보기
  mclose ZILUSDT  그 수동 포지션 정리 (yes 확인 후)
  quit            종료 (포지션은 그대로 두고, 다시 켜면 이어서 관리)
"""


def command_loop(tr):
    while not tr.stop:
        try:
            line = input().strip()
        except (EOFError, KeyboardInterrupt):
            tr.stop = True
            break
        if not line:
            continue
        parts = line.split()
        cmd, args = parts[0].lower(), parts[1:]
        try:
            if cmd in ("help", "h", "?"):
                print(HELP)
            elif cmd in ("status", "s"):
                tr.print_status()
            elif cmd == "pause":
                tr.paused = True
                log("⏸  새 진입 멈춤")
            elif cmd == "resume":
                tr.paused = False
                log("▶️  새 진입 다시 시작")
            elif cmd == "close" and args:
                sym = args[0].upper()
                hit = [(k, t) for k, t in tr.open_trades().items() if t["symbol"] in (sym, sym + "USDT")]
                if not hit:
                    print(f"봇 포지션에 {sym} 없음")
                for k, t in hit:
                    tr.close_trade(k, t, "수동 명령 정리")
                tr.save_state()
            elif cmd == "order" and len(args) >= 3:
                tk_, dr = args[0], args[1].upper()
                dr = {"L": "LONG", "S": "SHORT", "롱": "LONG", "숏": "SHORT"}.get(dr, dr)
                amt_ = float(args[2])
                lev_ = int(float(args[3])) if len(args) > 3 and args[3].replace('.', '', 1).isdigit() else tr.lev
                managed = not any(a.lower() == "keep" for a in args[3:])
                ok = input(f"⚠️ 수동 주문 {tk_.upper()} {dr} {lev_}배 ${amt_:g} "
                           f"({'봇이 ' + format(tr.hold_h, 'g') + 'h 뒤 정리' if managed else '직접 정리'}) — yes 입력: ").strip().lower()
                if ok == "yes":
                    tr.manual_open(tk_, dr, amt_, lev_, managed=managed)
                    tr.save_state()
                else:
                    print("취소")
            elif cmd == "manual":
                lst = tr.manual_positions()
                print(f"──────── 수동 포지션 {len(lst)}개 (봇이 관리 안 함) ────────")
                for p in lst:
                    print(f" {p['symbol']:<14}{p['side']:<6}수량 {p['qty']:g} 진입 {p['entry']:g} 현재 {p['mark']:g} "
                          f"미실현 {p['upnl']:+.2f} USDT")
            elif cmd == "mclose" and args:
                sym = args[0].upper()
                sym = sym if sym.endswith("USDT") else sym + "USDT"
                hit = [p for p in tr.manual_positions() if p["symbol"] == sym]
                if not hit:
                    print(f"수동 포지션에 {sym} 없음")
                for p in hit:
                    ok = input(f"⚠️ 수동 포지션 {sym} {p['side']} (미실현 {p['upnl']:+.2f}) 을 시장가로 정리할까? yes 입력: ").strip().lower()
                    if ok == "yes":
                        tr.close_manual(sym, p["side"])
                    else:
                        print("취소")
            elif cmd == "closeall":
                for k, t in list(tr.open_trades().items()):
                    tr.close_trade(k, t, "수동 명령 전체 정리")
                tr.save_state()
            elif cmd in ("quit", "exit", "q"):
                tr.stop = True
            else:
                print("모르는 명령입니다. help 입력")
        except Exception as e:
            log(f"⚠️ 명령 처리 오류: {e}")


def ask(prompt, default, cast, ok):
    while True:
        s = input(f"{prompt} [Enter = {default:g}]: ").strip()
        if not s:
            return cast(default)
        try:
            v = cast(float(s))
            if ok(v):
                return v
        except ValueError:
            pass
        print("  잘못된 값입니다. 다시 입력해 주세요.")


def check_gitignore():
    """API 키 파일이 auto_git_push 로 깃허브에 올라가지 않게 .gitignore 확인."""
    gi = os.path.join(SCRIPT_DIR, ".gitignore")
    try:
        txt = open(gi, encoding="utf-8").read() if os.path.exists(gi) else ""
    except Exception:
        txt = ""
    if "binance_api_key.txt" not in txt.split():
        print("⚠️  .gitignore 에 binance_api_key.txt 가 없습니다! 자동 push 로 API 키가 깃허브에 올라갈 수 있어요.")
        print("    .gitignore 에 'binance_api_key.txt' 한 줄을 추가한 뒤 다시 실행하세요.")
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="Pyramide 판정 → 바이낸스 선물 실전 자동매매")
    ap.add_argument("--dry", action="store_true", help="주문 없이 가상으로만 기록(모의)")
    a = ap.parse_args()
    check_gitignore()
    print("=" * 60)
    print(" Pyramide 실전 자동매매" + (" — 모의 모드(주문 안 냄)" if a.dry else ""))
    print(" 그냥 Enter = 일일 리포트 추천값(1년 백테스트: 3배·48h·$30)")
    print("=" * 60)
    lev = ask("레버리지(배)", DEFAULT_LEVERAGE, int, lambda v: 1 <= v <= 20)
    hold = ask("보유 시간(시간, 지나면 시장가 정리)", DEFAULT_HOLD_HOURS, float, lambda v: 0.1 <= v <= 240)
    m = input("1회 진입 금액 방식 [Enter = 고정 USDT / p = 현금의 % / r = 사용자 정의(live_sizing_rule.txt)]: ").strip().lower()
    pct = None
    rule = None
    seed = None
    amt = DEFAULT_MARGIN_USDT
    if m in ("p", "%", "pct", "퍼센트"):
        pct = ask("현금의 몇 %(진입할 때마다 그때 현금 기준)", DEFAULT_MARGIN_PCT, float, lambda v: 0 < v <= 100)
    elif m in ("r", "rule", "규칙"):
        if not sizing_rule:
            print("❌ sizing_rule.py 가 없음")
            return
        if not os.path.exists(RULE_FILE):
            with open(RULE_FILE, "w", encoding="utf-8") as f:
                f.write(sizing_rule.EXAMPLE + "\n")
            print(f"  {RULE_FILE} 에 예시 규칙을 만들어 뒀음 — 고친 뒤 다시 실행해도 되고, 그대로 써도 됨")
        rule = open(RULE_FILE, encoding="utf-8").read()
        print("----- 규칙 -----\n" + rule.strip() + "\n----------------")
        s0 = input("처음 시드 start_seed (USDT) [Enter = 지난번 값, 처음이면 지금 현금]: ").strip()
        seed = float(s0) if s0 else None
        ok, msg, rows = sizing_rule.check(rule, seed or 1000, DEFAULT_LEVERAGE)
        print(("  ✅ " if ok else "  ❌ ") + msg)
        for d, cur, ps in rows:
            print(f"     {d}: current_seed {cur:,.2f} → position_size {ps:g}")
        if not ok:
            print("  규칙을 고친 뒤 다시 실행해줘")
            return
    else:
        amt = ask("1회 진입 금액(USDT, 증거금)", DEFAULT_MARGIN_USDT, float, lambda v: v > 0)
    s = input(f"강한 숏(4h예상 {STRONG_SHORT_EXP4}% 이하)은 진입 금액 {STRONG_MULT:g}배로? [Enter = 예 / n = 아니오]: ").strip().lower()
    strong = s not in ("n", "no", "아니오", "ㄴ")
    if lev >= 5:
        print(f"  ⚠️ {lev}배: 1년 백테스트에서 5배는 청산 44건·한 달 -$91도 있었음(3배는 16건)")
    acquire_lock()
    try:
        tr = Trader(lev, hold, amt, dry=a.dry, strong_x2=strong, margin_pct=pct, margin_rule=rule, start_seed=seed)
        tr.startup()
        tr.print_status()
        if not a.dry:
            ok = input("⚠️  실거래입니다. 실제 돈으로 주문이 나갑니다. 시작하려면 yes 입력: ").strip().lower()
            if ok != "yes":
                print("취소")
                return
        log(f"ℹ️ 따라갈 판정: {', '.join({'krw': '빗썸', 'binance': '바이낸스 탭'}.get(x, x) for x in SIGNAL_SOURCES)}")
        log(f"▶️  시작 — {lev}배 · {hold:g}h · {tr.amount_text()}{' (강한 숏 ×2)' if strong else ''} · {'모의' if a.dry else '실거래'} (help = 명령어)")
        th = threading.Thread(target=tr.run, daemon=True)
        th.start()
        command_loop(tr)
        tr.stop = True
        th.join(timeout=POLL_SEC + 5)
        tr.save_state()
        log("⏹  종료 — 보유 포지션은 거래소에 그대로 있고, 다시 켜면 이어서 정리 시간을 관리합니다")
    finally:
        release_lock()


if __name__ == "__main__":
    main()
