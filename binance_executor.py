# -*- coding: utf-8 -*-
"""
binance_executor.py — Pyramide 판정 신호 → 바이낸스 USDT-M 선물 자동 주문
=====================================================================
[2026-11-13 신규]

흐름
  data_collector.py 가 쓰는 signal_outcomes_verdict.csv 를 POLL_SEC 마다 읽어서
  새로 생긴 신호(event_id)가 필터를 통과하면 바이낸스 선물에 시장가 진입 →
  HOLD_HOURS 뒤 시장가 청산 (일일 리포트 판정 기준과 동일: 중간 청산 없음, 12h 청산).

기본 설정 (실행 중 명령어로 바꿀 수 있음, executor_config.json 에 저장됨)
  - 1회 진입 증거금 20 USDT  (시작할 때 물어봄 / 실행 중 `amt 30`)
  - 레버리지 = 신호의 권장 레버리지(max_lev, calc_liq_risk) / 상한 10x
  - 격리 마진(ISOLATED) → 한 건 최대 손실 = 증거금
  - 허용 신호: '롱점수', '숏점수' 만
      (10/11~11/12 verdict CSV, 바이낸스 상장 종목 기준 12h 결과
       롱점수 14건 승률 57% / 숏점수 24건 71% → 플러스,
       매집 롱 176건 33% / 모델 숏 128건 59%·청산 11건 → 마이너스라 기본 OFF)
  - 동시 포지션 최대 3개, 오늘(KST) 실현손익 -60 USDT 이하면 신규 진입 정지
  - 바이낸스 선물 미상장(NOFUT) 종목은 건너뜀
  - 손절/익절은 기본 OFF (sl/tp 명령으로 켜면 프로그램이 마크가격 감시 후 시장가 청산)

준비
  1) 같은 폴더에 .env 파일:
        BINANCE_API_KEY=xxxx
        BINANCE_API_SECRET=yyyy
     API 키는 '선물' 권한만, 출금 권한 OFF, IP 화이트리스트 필수.
  2) .gitignore 에 아래 두 줄 추가 (키가 깃허브에 올라가지 않게)
        .env
        executor_*.json
  3) 실행:  python binance_executor.py          (실거래)
            python binance_executor.py --dry    (주문 없이 신호만 보고 가상 기록)
            python binance_executor.py --amt 30 (진입금액 지정해서 시작)

실행 중 명령어 (콘솔에 입력 후 Enter) — `help` 로 목록 보기
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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SIGNAL_CSV = os.path.join(SCRIPT_DIR, "signal_outcomes_verdict.csv")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "executor_config.json")
STATE_FILE = os.path.join(SCRIPT_DIR, "executor_state.json")
TRADES_CSV = os.path.join(SCRIPT_DIR, "executor_trades.csv")
LOG_FILE = os.path.join(SCRIPT_DIR, "executor_log.txt")
LOCK_FILE = os.path.join(SCRIPT_DIR, "executor.lock")
ENV_FILE = os.path.join(SCRIPT_DIR, ".env")

BASE_URL = "https://fapi.binance.com"
RECV_WINDOW = 10000

# ------------------------------------------------------------------
# 기본 설정 — executor_config.json 이 있으면 그 값이 우선
# ------------------------------------------------------------------
DEFAULT_CONFIG = {
    "MARGIN_USDT": 20.0,            # 1회 진입 증거금(USDT)
    "LEV_MODE": "rec",              # "rec" = 신호 권장 레버리지(max_lev) / 숫자 = 고정 레버리지
    "LEV_CAP": 10,                  # 레버리지 상한
    "MARGIN_TYPE": "ISOLATED",      # ISOLATED / CROSSED
    "ALLOW_DIRECTIONS": ["LONG", "SHORT"],
    "ALLOW_REASONS": ["롱점수", "숏점수"],   # reason 앞 단어 기준. 그 외: 매집, 모델, 분산
    "SKIP_NOFUT": True,             # liq_risk 에 NOFUT 있으면 건너뜀
    "MAX_POSITIONS": 3,             # 봇이 연 포지션 동시 최대 개수
    "HOLD_HOURS": 12.0,             # 보유 시간 후 시장가 청산
    "SL_MARGIN_PCT": 0.0,           # 증거금 대비 손실 % 도달 시 청산 (0=끔). 예: 50 → 증거금 -50%
    "TP_MARGIN_PCT": 0.0,           # 증거금 대비 수익 % 도달 시 청산 (0=끔)
    "DAILY_LOSS_LIMIT_USDT": 60.0,  # 오늘(KST) 실현손익이 -이 값 이하면 신규 진입 정지 (0=끔)
    "MAX_SIGNAL_AGE_MIN": 10.0,     # 신호 발생 후 이 시간 지나면 진입 안 함(늦은 진입 방지)
    "POLL_SEC": 20,                 # 신호 CSV / 포지션 확인 주기(초)
}

# ------------------------------------------------------------------
# 공용
# ------------------------------------------------------------------
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


def load_env():
    env = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8") as f:
            for raw in f:
                s = raw.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    key = os.environ.get("BINANCE_API_KEY") or env.get("BINANCE_API_KEY", "")
    sec = os.environ.get("BINANCE_API_SECRET") or env.get("BINANCE_API_SECRET", "")
    return key, sec


def atomic_write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            for k in cfg:
                if k in saved:
                    cfg[k] = saved[k]
        except Exception as e:
            log(f"⚠️ 설정 파일 읽기 실패(기본값 사용): {e}")
    return cfg


def save_config(cfg):
    try:
        atomic_write_json(CONFIG_FILE, cfg)
    except Exception as e:
        log(f"⚠️ 설정 저장 실패: {e}")


def kst_today_start_ms():
    # 이 PC 시간이 KST 라는 전제(수집기와 동일)
    now = datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp() * 1000)


def acquire_lock():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE) as f:
                pid = int(f.read().strip() or 0)
        except Exception:
            pid = 0
        if pid and _pid_alive(pid):
            print(f"❌ 이미 실행 중입니다 (PID {pid}). 중복 실행하면 주문이 두 번 나갑니다.")
            print(f"   확실히 꺼져 있다면 {LOCK_FILE} 를 지우고 다시 실행하세요.")
            sys.exit(1)
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))


def _pid_alive(pid):
    if os.name == "nt":
        try:
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return code.value == 259  # STILL_ACTIVE
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
# 바이낸스 USDT-M 선물 REST (requests 만 사용)
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

    def income_today(self):
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
# 실행기
# ------------------------------------------------------------------
class Executor:
    def __init__(self, cfg, dry=False):
        self.cfg = cfg
        self.dry = dry
        self.paused = False
        self.stop = False
        self.lock = threading.RLock()
        key, sec = load_env()
        if not dry and (not key or not sec):
            print("❌ .env 에 BINANCE_API_KEY / BINANCE_API_SECRET 이 없습니다.")
            sys.exit(1)
        self.bn = BinanceFutures(key or "x", sec or "x")
        self.state = {"seen": [], "trades": {}, "closed_today_pnl_dry": 0.0}
        self.load_state()
        self._daily_pnl_cache = (0.0, 0)
        self._last_symbols_load = 0

    # --- 상태 저장 ---
    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, encoding="utf-8") as f:
                    s = json.load(f)
                self.state.update(s)
            except Exception as e:
                log(f"⚠️ 상태 파일 읽기 실패: {e}")
        self.seen = set(self.state.get("seen", []))

    def save_state(self):
        with self.lock:
            self.state["seen"] = sorted(self.seen)[-3000:]
            try:
                atomic_write_json(STATE_FILE, self.state)
            except Exception as e:
                log(f"⚠️ 상태 저장 실패: {e}")

    def write_trade(self, row):
        new = not os.path.exists(TRADES_CSV)
        cols = ["event_id", "symbol", "direction", "reason", "mode", "leverage", "margin", "qty",
                "entry_time", "entry_price", "exit_time", "exit_price", "close_reason", "pnl_usdt_est"]
        try:
            with open(TRADES_CSV, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                if new:
                    w.writeheader()
                w.writerow(row)
        except Exception as e:
            log(f"⚠️ 거래 기록 저장 실패: {e}")

    # --- 초기화 ---
    def startup(self):
        self.bn.sync_time()
        self.bn.load_symbols()
        self._last_symbols_load = time.time()
        log(f"바이낸스 USDT-M 무기한 {len(self.bn.symbols)}종목 로드")
        if not self.dry:
            hedge = self.bn.load_position_mode()
            log(f"포지션 모드: {'헤지(양방향)' if hedge else '단방향(One-way)'}")
            avail, total = self.bn.usdt_balance()
            log(f"USDT 잔고: 사용가능 {avail:,.2f} / 총 {total:,.2f}")
            self.reconcile()
        # 처음 켤 때 이미 CSV 에 있는 신호는 전부 '본 것'으로 처리 (지난 신호로 진입 금지)
        rows = self.read_signals()
        before = len(self.seen)
        for r in rows:
            self.seen.add(r.get("event_id", ""))
        log(f"기존 신호 {len(self.seen) - before}건 건너뜀 표시 — 지금부터 새 신호만 진입")
        self.save_state()

    def reconcile(self):
        """상태 파일의 봇 포지션이 거래소에 아직 있는지 확인."""
        try:
            pos = self.bn.positions()
        except BinanceError as e:
            log(f"⚠️ 포지션 조회 실패: {e}")
            return
        with self.lock:
            for eid, t in list(self.state["trades"].items()):
                if t.get("mode") != "live":
                    continue
                lst = [p for p in pos.get(t["symbol"], []) if p["side"] == t["direction"]]
                if not lst:
                    log(f"ℹ️ {t['symbol']} {t['direction']} 포지션이 거래소에 없음 → 청산(수동/강제청산)된 것으로 처리")
                    self._finish(eid, t, None, "거래소에서 사라짐(수동 또는 강제청산)")
        self.save_state()

    # --- 신호 ---
    def read_signals(self):
        for _ in range(5):
            try:
                if not os.path.exists(SIGNAL_CSV):
                    return []
                with open(SIGNAL_CSV, newline="", encoding="utf-8") as f:
                    return list(csv.DictReader(f))
            except (PermissionError, OSError):
                time.sleep(0.5)     # 수집기가 os.replace 하는 순간
            except Exception as e:
                log(f"⚠️ 신호 CSV 읽기 실패: {e}")
                return []
        return []

    def to_symbol(self, ticker):
        t = ticker.upper().strip()
        for cand in (f"{t}USDT", f"1000{t}USDT", f"1000000{t}USDT", f"1M{t}USDT"):
            if cand in self.bn.symbols:
                return cand
        return None

    def bot_open_trades(self):
        return {k: v for k, v in self.state["trades"].items() if v.get("status") == "open"}

    def daily_pnl(self):
        if self.dry:
            if self.state.get("dry_pnl_date") != datetime.now().strftime("%Y-%m-%d"):
                return 0.0
            return self.state.get("closed_today_pnl_dry", 0.0)
        val, ts = self._daily_pnl_cache
        if time.time() - ts > 60:
            try:
                val = self.bn.income_today()
            except Exception:
                pass
            self._daily_pnl_cache = (val, time.time())
        return val

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
        c = self.cfg
        eid = r["event_id"]
        ticker = r.get("ticker", "")
        direction = r.get("direction", "")
        reason = r.get("reason", "")
        kind = reason.split(" ")[0] if reason else ""
        tag = f"{ticker} {direction} ({reason})"

        def skip(why):
            log(f"⏭  {tag} 건너뜀: {why}")

        if self.paused:
            return skip("일시정지 중")
        if direction not in c["ALLOW_DIRECTIONS"]:
            return skip(f"방향 {direction} 비허용")
        if kind not in c["ALLOW_REASONS"]:
            return skip(f"신호종류 '{kind}' 비허용 (허용: {','.join(c['ALLOW_REASONS'])})")
        try:
            age = (now - datetime.strptime(r["start"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
        except Exception:
            age = 0
        if age > c["MAX_SIGNAL_AGE_MIN"]:
            return skip(f"신호가 {age:.0f}분 지남")
        if c["SKIP_NOFUT"] and "NOFUT" in (r.get("liq_risk") or ""):
            return skip("바이낸스 선물 미상장(NOFUT)")
        if time.time() - self._last_symbols_load > 6 * 3600:
            try:
                self.bn.load_symbols()
                self._last_symbols_load = time.time()
            except Exception:
                pass
        symbol = self.to_symbol(ticker)
        if not symbol:
            return skip("바이낸스 USDT 무기한 심볼 없음")
        opens = self.bot_open_trades()
        if len(opens) >= int(c["MAX_POSITIONS"]):
            return skip(f"동시 포지션 한도 {c['MAX_POSITIONS']}개")
        if any(t["symbol"] == symbol for t in opens.values()):
            return skip("이미 이 종목 포지션 보유(봇)")
        lim = float(c["DAILY_LOSS_LIMIT_USDT"])
        if lim > 0:
            dp = self.daily_pnl()
            if dp <= -lim:
                return skip(f"오늘 손익 {dp:+.2f} ≤ -{lim} 한도 → 신규 진입 정지")

        # 레버리지
        if str(c["LEV_MODE"]).lower() == "rec":
            try:
                lev = int(float(r.get("max_lev") or 0))
            except Exception:
                lev = 0
            if lev <= 0:
                return skip("권장 레버리지(max_lev) 없음")
        else:
            lev = int(float(c["LEV_MODE"]))
        lev = max(1, min(int(c["LEV_CAP"]), lev))
        margin = float(c["MARGIN_USDT"])
        self.open_position(eid, symbol, direction, reason, lev, margin, r)

    def open_position(self, eid, symbol, direction, reason, lev, margin, r):
        info = self.bn.symbols[symbol]
        mark = self.bn.mark_price(symbol)
        mode = "dry" if self.dry else "live"

        if not self.dry:
            avail, _ = self.bn.usdt_balance()
            if avail < margin * 1.05:
                log(f"⏭  {symbol} 건너뜀: 사용가능 잔고 {avail:.2f} < 증거금 {margin:.2f}")
                return
            # 다른 곳(수동)에서 같은 종목 포지션이 있으면 건드리지 않음
            if symbol in self.bn.positions():
                log(f"⏭  {symbol} 건너뜀: 거래소에 이미 이 종목 포지션 있음(수동 포함)")
                return
            self.bn.set_margin_type(symbol, self.cfg["MARGIN_TYPE"])
            real_lev = self.bn.set_leverage(symbol, lev)
            if real_lev != lev:
                log(f"ℹ️ {symbol} 레버리지 {lev}x 불가 → {real_lev}x 적용")
            lev = real_lev

        qty = floor_step(margin * lev / mark, info["step"])
        if info["max_qty"] > 0 and qty > info["max_qty"]:
            qty = floor_step(info["max_qty"], info["step"])
        if qty <= 0 or qty < info["min_qty"] or float(qty) * mark < info["min_notional"]:
            log(f"⏭  {symbol} 건너뜀: 수량 {fmt_qty(qty)} 이 최소주문(명목 {info['min_notional']} USDT) 미달 "
                f"— 진입금액을 올리세요")
            return

        side = "BUY" if direction == "LONG" else "SELL"
        entry_price = mark
        if not self.dry:
            try:
                res = self.bn.market_order(symbol, side, fmt_qty(qty), position_side=direction)
            except BinanceError as e:
                log(f"❌ {symbol} 진입 주문 실패: {e}")
                return
            entry_price = float(res.get("avgPrice") or 0) or mark
            qty = Decimal(str(res.get("executedQty") or qty))

        t = {
            "event_id": eid, "symbol": symbol, "direction": direction, "reason": reason, "mode": mode,
            "leverage": lev, "margin": margin, "qty": fmt_qty(qty),
            "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "entry_price": entry_price,
            "hold_hours": float(self.cfg["HOLD_HOURS"]), "status": "open",
        }
        with self.lock:
            self.state["trades"][eid] = t
        self.save_state()
        liq = entry_price * (1 - 0.9 / lev) if direction == "LONG" else entry_price * (1 + 0.9 / lev)
        log(f"✅ {'[DRY] ' if self.dry else ''}진입 {symbol} {direction} {lev}x 증거금 ${margin:.2f} "
            f"수량 {fmt_qty(qty)} @ {entry_price:g} (대략 청산가 {liq:g}) — {reason}")

    # --- 보유 포지션 관리 ---
    def manage(self):
        opens = self.bot_open_trades()
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
                symbol, direction = t["symbol"], t["direction"]
                if not self.dry:
                    lst = [p for p in pos.get(symbol, []) if p["side"] == direction]
                    if not lst:
                        log(f"ℹ️ {symbol} {direction} 포지션이 거래소에 없음 → 강제청산/수동청산으로 기록")
                        self._finish(eid, t, None, "거래소에서 사라짐(수동 또는 강제청산)")
                        continue
                    mark = lst[0]["mark"] or self.bn.mark_price(symbol)
                else:
                    mark = self.bn.mark_price(symbol)
                sign = 1 if direction == "LONG" else -1
                margin_ret = (mark / float(t["entry_price"]) - 1) * 100 * sign * float(t["leverage"])
                held_h = (now - datetime.strptime(t["entry_time"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
                sl, tp = float(self.cfg["SL_MARGIN_PCT"]), float(self.cfg["TP_MARGIN_PCT"])
                why = None
                if held_h >= float(t.get("hold_hours", self.cfg["HOLD_HOURS"])):
                    why = f"{t.get('hold_hours')}h 보유 만료"
                elif sl > 0 and margin_ret <= -sl:
                    why = f"손절 (증거금 {margin_ret:.1f}%)"
                elif tp > 0 and margin_ret >= tp:
                    why = f"익절 (증거금 {margin_ret:.1f}%)"
                elif self.dry and margin_ret <= -90:
                    why = "강제청산(가상)"
                if why:
                    self.close_trade(eid, t, why, mark)
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
                log(f"❌ {symbol} 청산 전 포지션 조회 실패: {e}")
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
                log(f"❌ {symbol} 청산 주문 실패: {e} — 다음 주기에 재시도")
                return
        elif exit_price is None:
            exit_price = self.bn.mark_price(symbol)
        self._finish(eid, t, exit_price, why)

    def _finish(self, eid, t, exit_price, why):
        pnl = ""
        if exit_price:
            sign = 1 if t["direction"] == "LONG" else -1
            notional = float(t["qty"]) * float(t["entry_price"])
            gross = (exit_price / float(t["entry_price"]) - 1) * sign * notional
            fee = notional * 0.0005 * 2   # 시장가 테이커 대략 0.05%×2 (BNB 할인 등 미반영)
            pnl = round(gross - fee, 4)
            if self.dry:
                today = datetime.now().strftime("%Y-%m-%d")
                if self.state.get("dry_pnl_date") != today:
                    self.state["dry_pnl_date"], self.state["closed_today_pnl_dry"] = today, 0.0
                self.state["closed_today_pnl_dry"] += pnl
        t.update({"status": "closed", "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                  "exit_price": exit_price or "", "close_reason": why, "pnl_usdt_est": pnl})
        self.write_trade(t)
        with self.lock:
            self.state["trades"].pop(eid, None)
        self._daily_pnl_cache = (0.0, 0)
        log(f"🔚 {'[DRY] ' if self.dry else ''}청산 {t['symbol']} {t['direction']} — {why} "
            f"{'추정손익 ' + format(pnl, '+.2f') + ' USDT' if pnl != '' else ''}")

    # --- 상태 출력 ---
    def print_status(self):
        c = self.cfg
        lev = "권장(max_lev)" if str(c["LEV_MODE"]).lower() == "rec" else f"고정 {c['LEV_MODE']}x"
        lines = [
            "──────── 현재 설정 ────────",
            f" 모드        : {'DRY(가상)' if self.dry else '실거래'}{'  [일시정지]' if self.paused else ''}",
            f" 진입 증거금 : {c['MARGIN_USDT']} USDT",
            f" 레버리지    : {lev} / 상한 {c['LEV_CAP']}x / {c['MARGIN_TYPE']}",
            f" 방향        : {','.join(c['ALLOW_DIRECTIONS'])}",
            f" 신호종류    : {','.join(c['ALLOW_REASONS'])}",
            f" 동시 최대   : {c['MAX_POSITIONS']}개 / 보유 {c['HOLD_HOURS']}h 후 청산",
            f" 손절/익절   : {c['SL_MARGIN_PCT'] or '끔'} / {c['TP_MARGIN_PCT'] or '끔'} (증거금 %)",
            f" 일 손실한도 : {c['DAILY_LOSS_LIMIT_USDT'] or '끔'} USDT  (오늘 {self.daily_pnl():+.2f})",
        ]
        opens = self.bot_open_trades()
        lines.append(f"──────── 봇 포지션 {len(opens)}개 ────────")
        for t in opens.values():
            try:
                mark = self.bn.mark_price(t["symbol"])
                sign = 1 if t["direction"] == "LONG" else -1
                mr = (mark / float(t["entry_price"]) - 1) * 100 * sign * float(t["leverage"])
                held = (datetime.now() - datetime.strptime(t["entry_time"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
                lines.append(f" {t['symbol']:<14}{t['direction']:<6}{t['leverage']}x ${t['margin']} "
                             f"진입 {t['entry_price']:g} 현재 {mark:g} 증거금 {mr:+.1f}% 보유 {held:.1f}h")
            except Exception:
                lines.append(f" {t['symbol']} {t['direction']} (가격 조회 실패)")
        with _print_lock:
            print("\n".join(lines), flush=True)

    # --- 메인 루프 ---
    def run(self):
        last_sync = time.time()
        while not self.stop:
            try:
                if time.time() - last_sync > 3600:
                    self.bn.sync_time()
                    last_sync = time.time()
                self.manage()
                self.check_signals()
            except Exception as e:
                log(f"⚠️ 루프 오류: {e}")
            for _ in range(int(self.cfg["POLL_SEC"])):
                if self.stop:
                    break
                time.sleep(1)


# ------------------------------------------------------------------
# 콘솔 명령어
# ------------------------------------------------------------------
HELP = """
명령어 (입력 후 Enter):
  status            현재 설정·봇 포지션 보기
  amt 30            1회 진입 증거금(USDT) 변경
  lev rec           레버리지 = 신호 권장값(max_lev)
  lev 5             레버리지 고정 5x
  cap 10            레버리지 상한
  max 3             동시 최대 포지션 수
  hold 12           보유 시간(h) — 새로 여는 포지션부터 적용
  sl 50 / sl 0      손절: 증거금 -50% 도달 시 청산 / 끔
  tp 100 / tp 0     익절: 증거금 +100% 도달 시 청산 / 끔
  loss 60 / loss 0  일 손실한도(USDT) / 끔
  dir long|short|both          허용 방향
  reason 롱점수,숏점수          허용 신호종류 (매집, 모델, 분산 추가 가능)
  pause / resume    신규 진입 일시정지 / 재개 (보유 포지션 청산은 계속 진행)
  close BTCUSDT     봇 포지션 즉시 청산
  closeall          봇 포지션 전부 즉시 청산
  quit              종료 (보유 포지션은 그대로 두고, 다시 켜면 이어서 관리)
"""


def command_loop(ex):
    c = ex.cfg
    while not ex.stop:
        try:
            line = input().strip()
        except (EOFError, KeyboardInterrupt):
            ex.stop = True
            break
        if not line:
            continue
        parts = line.split()
        cmd, args = parts[0].lower(), parts[1:]
        try:
            changed = True
            if cmd in ("help", "h", "?"):
                print(HELP); changed = False
            elif cmd in ("status", "s", "pos"):
                ex.print_status(); changed = False
            elif cmd == "amt":
                v = float(args[0])
                if v <= 0:
                    raise ValueError
                c["MARGIN_USDT"] = v
            elif cmd == "lev":
                a = args[0].lower()
                c["LEV_MODE"] = "rec" if a == "rec" else int(float(a))
            elif cmd == "cap":
                c["LEV_CAP"] = max(1, int(float(args[0])))
            elif cmd == "max":
                c["MAX_POSITIONS"] = max(0, int(args[0]))
            elif cmd == "hold":
                c["HOLD_HOURS"] = max(0.1, float(args[0]))
            elif cmd == "sl":
                c["SL_MARGIN_PCT"] = max(0.0, float(args[0]))
            elif cmd == "tp":
                c["TP_MARGIN_PCT"] = max(0.0, float(args[0]))
            elif cmd == "loss":
                c["DAILY_LOSS_LIMIT_USDT"] = max(0.0, float(args[0]))
            elif cmd == "dir":
                a = args[0].lower()
                c["ALLOW_DIRECTIONS"] = {"long": ["LONG"], "short": ["SHORT"]}.get(a, ["LONG", "SHORT"])
            elif cmd == "reason":
                c["ALLOW_REASONS"] = [x.strip() for x in " ".join(args).split(",") if x.strip()]
            elif cmd == "pause":
                ex.paused = True; changed = False; log("⏸  신규 진입 일시정지")
            elif cmd == "resume":
                ex.paused = False; changed = False; log("▶️  신규 진입 재개")
            elif cmd == "close":
                sym = args[0].upper()
                hit = [(k, t) for k, t in ex.bot_open_trades().items() if t["symbol"] == sym]
                if not hit:
                    print(f"봇 포지션에 {sym} 없음")
                for k, t in hit:
                    ex.close_trade(k, t, "수동 명령 청산")
                ex.save_state(); changed = False
            elif cmd == "closeall":
                for k, t in list(ex.bot_open_trades().items()):
                    ex.close_trade(k, t, "수동 명령 전체청산")
                ex.save_state(); changed = False
            elif cmd in ("quit", "exit", "q"):
                ex.stop = True; changed = False
            else:
                print("모르는 명령입니다. help 입력"); changed = False
            if changed:
                save_config(c)
                log(f"⚙️  설정 변경: {line}")
        except (IndexError, ValueError):
            print("값이 잘못됐습니다. help 참고")
        except Exception as e:
            log(f"⚠️ 명령 처리 오류: {e}")


def main():
    ap = argparse.ArgumentParser(description="Pyramide → 바이낸스 선물 자동 주문")
    ap.add_argument("--dry", action="store_true", help="주문 없이 가상으로만 기록")
    ap.add_argument("--amt", type=float, help="1회 진입 증거금(USDT)")
    ap.add_argument("--yes", action="store_true", help="시작 확인 질문 생략")
    a = ap.parse_args()

    cfg = load_config()
    if a.amt and a.amt > 0:
        cfg["MARGIN_USDT"] = a.amt
    elif not a.yes:
        try:
            s = input(f"1회 진입 증거금(USDT) [Enter = {cfg['MARGIN_USDT']}]: ").strip()
            if s:
                cfg["MARGIN_USDT"] = float(s)
        except ValueError:
            print("숫자가 아니라서 기존 값 유지")
    save_config(cfg)

    # .env 가 깃에 안 올라가는지 확인
    gi = os.path.join(SCRIPT_DIR, ".gitignore")
    try:
        gi_txt = open(gi, encoding="utf-8").read() if os.path.exists(gi) else ""
    except Exception:
        gi_txt = ""
    if ".env" not in gi_txt.split():
        print("⚠️  .gitignore 에 '.env' 가 없습니다! auto_git_push 가 API 키를 깃허브에 올릴 수 있어요.")
        print("    .gitignore 에 '.env' 와 'executor_*.json' 두 줄을 추가하세요.")

    acquire_lock()
    try:
        ex = Executor(cfg, dry=a.dry)
        ex.startup()
        ex.print_status()
        if not a.dry and not a.yes:
            ok = input("⚠️  실거래 모드입니다. 시작하려면 yes 입력: ").strip().lower()
            if ok != "yes":
                print("취소")
                return
        log(f"🚀 시작 ({'DRY' if a.dry else '실거래'}) — 명령어는 help")
        threading.Thread(target=command_loop, args=(ex,), daemon=True).start()
        try:
            ex.run()
        except KeyboardInterrupt:
            pass
        ex.save_state()
        log("종료 — 보유 포지션은 그대로 남아 있고, 다시 켜면 이어서 관리합니다.")
    finally:
        release_lock()


if __name__ == "__main__":
    main()
