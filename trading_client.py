# -*- coding: utf-8 -*-
"""
주문용 클라이언트 (trading_client.py)
- 포지션 개수 많아도 짤리지 않도록 높이 크게 조정
"""
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import os
import csv
import time
import random
from datetime import datetime
from collections import deque

# ============================================================
# 저장 경로
# ============================================================
DEFAULT_LEVERAGE = 5
FALLBACK_MIN_SCORE = 75
FALLBACK_PP_MIN_SCORE = 80
FALLBACK_WATCH_MIN_SCORE = 65
STALE_SEC = 25  # 서버 SCORE_INTERVAL(10초)의 약 2.5배. 너무 빡빡하면(15초) 정상적인
                 # 사이클 지연(네트워크 지연 등)에도 '연결 끊김'이 깜빡여서 여유를 더 뒀다.

try:
    _fallback_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _fallback_dir = os.getcwd()

# 스크립트 폴더의 path_config.txt가 있으면 최우선으로 그 경로를 쓴다 — 아래
# "경로 변경" 버튼이 이 파일을 쓴다. trading_server.py도 같은 파일을 확인하므로
# server/client를 같은 폴더에 두고 쓰면 둘 다 같은 경로를 보게 된다.
SCRIPT_DIR = None
_PATH_CONFIG_FILE = os.path.join(_fallback_dir, "path_config.txt")
if os.path.exists(_PATH_CONFIG_FILE):
    try:
        with open(_PATH_CONFIG_FILE, "r", encoding="utf-8") as _f:
            _custom_path = _f.read().strip()
        if _custom_path:
            os.makedirs(_custom_path, exist_ok=True)
            _t = os.path.join(_custom_path, ".write_test")
            with open(_t, "w") as _f:
                _f.write("ok")
            os.remove(_t)
            SCRIPT_DIR = _custom_path
    except Exception:
        SCRIPT_DIR = None

if SCRIPT_DIR is None:
    _ANDROID_PUBLIC_DIR = "/storage/emulated/0/Documents"
    try:
        os.makedirs(_ANDROID_PUBLIC_DIR, exist_ok=True)
        _t = os.path.join(_ANDROID_PUBLIC_DIR, ".write_test")
        with open(_t, "w") as _f:
            _f.write("ok")
        os.remove(_t)
        SCRIPT_DIR = _ANDROID_PUBLIC_DIR
    except Exception:
        SCRIPT_DIR = _fallback_dir

MARKET_SNAPSHOT = os.path.join(SCRIPT_DIR, "server_market.csv")
ACCOUNT_SNAPSHOT = os.path.join(SCRIPT_DIR, "server_account.csv")
CMD_DIR = os.path.join(SCRIPT_DIR, "server_cmds")
RESULTS_FILE = os.path.join(SCRIPT_DIR, "server_results.csv")
HISTORY_FILE = os.path.join(SCRIPT_DIR, "trade_history.csv")
os.makedirs(CMD_DIR, exist_ok=True)

current_min_score = FALLBACK_MIN_SCORE
pp_current_min_score = FALLBACK_PP_MIN_SCORE
watch_current_min_score = FALLBACK_WATCH_MIN_SCORE
current_interval = "1h"
macro_state = {"btc_ema_l": 0, "btc_ema_s": 0, "fng_value": None, "fng_class": ""}
current_margin_mode = "cross"  # 서버 기본값과 동일. read_account_snapshot이 실제 값으로 갱신함
bank_balance = 0.0
bank_total_deposit = 0.0
bank_total_spent = 0.0
ALLOWED_INTERVALS = ["1h", "2h", "6h", "12h"]

def _f(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default

def read_market_snapshot():
    global current_min_score, pp_current_min_score, watch_current_min_score, current_interval, macro_state
    try:
        with open(MARKET_SNAPSHOT, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = []
            score_time = price_time = ""
            for r in reader:
                rows.append({
                    'ticker': r.get('ticker', ''),
                    'price': _f(r.get('price')),
                    'price_usd': _f(r.get('price_usd')) if r.get('price_usd') not in (None, '',) else None,
                    'long_score': int(_f(r.get('long_score'))),
                    'short_score': int(_f(r.get('short_score'))),
                    # 신설: 출발 전 매집 구간 탐지 점수 (없는 구버전 CSV라도 안전하게 0 처리)
                    'prepump_score': int(_f(r.get('prepump_score'), 0)),
                    'preshort_score': int(_f(r.get('preshort_score'), 0)),
                    # v5 하드필터 통과 여부 — 구버전 서버가 보낸 CSV(컬럼 없음)는
                    # 기본값 1(통과)로 처리해서, 필터 체크박스를 켜도 예전처럼
                    # 전부 안 보이는 일이 없게 한다.
                    'filters_ok_long': bool(int(_f(r.get('filters_ok_long'), 1))),
                    'filters_ok_short': bool(int(_f(r.get('filters_ok_short'), 1))),
                    'rsi': _f(r.get('rsi')),
                    'rsi_delta': _f(r.get('rsi_delta')),
                    'vol_z': _f(r.get('vol_z')),
                    'bb_percent': _f(r.get('bb_percent')),
                    'cvd': _f(r.get('cvd')),
                    'cvd_diff': _f(r.get('cvd_diff'), 0),
                    'funding': _f(r.get('funding')),
                    'vol_24h_m': int(_f(r.get('vol_24h_m'))),
                    'atr_pct': _f(r.get('atr_pct')),
                    'oi_change_pct': _f(r.get('oi_change_pct')),
                    'chg_30m': _f(r.get('chg_30m')),
                    'ls_ratio': _f(r.get('ls_ratio')) if r.get('ls_ratio') not in (None, '',) else None,
                    'ema20': _f(r.get('ema20')) if r.get('ema20') not in (None, '',) else None,
                    'ema60': _f(r.get('ema60')) if r.get('ema60') not in (None, '',) else None,
                    'ema120': _f(r.get('ema120')) if r.get('ema120') not in (None, '',) else None,
                    'extension_pct': _f(r.get('extension_pct'), 0),
                    # v6 세부 컴포넌트 (구버전 CSV엔 없을 수 있어 기본값 0)
                    'ema_l': int(_f(r.get('ema_l'), 0)), 'ema_s': int(_f(r.get('ema_s'), 0)),
                    'pp_l': int(_f(r.get('pp_l'), 0)), 'pp_s': int(_f(r.get('pp_s'), 0)),
                    'cvd_l': int(_f(r.get('cvd_l'), 0)), 'cvd_s': int(_f(r.get('cvd_s'), 0)),
                    'oi_l': int(_f(r.get('oi_l'), 0)), 'oi_s': int(_f(r.get('oi_s'), 0)),
                    'm30_l': int(_f(r.get('m30_l'), 0)), 'm30_s': int(_f(r.get('m30_s'), 0)),
                    'volz_sc': int(_f(r.get('volz_sc'), 0)), 'liquidity_sc': int(_f(r.get('liquidity_sc'), 0)),
                    # Pre-Pump/Pre-Short 세부 컴포넌트
                    'div_l': int(_f(r.get('div_l'), 0)), 'div_s': int(_f(r.get('div_s'), 0)),
                    'lsx_l': int(_f(r.get('lsx_l'), 0)), 'lsx_s': int(_f(r.get('lsx_s'), 0)),
                    'bb_comp_sc': int(_f(r.get('bb_comp_sc'), 0)), 'stealth_sc': int(_f(r.get('stealth_sc'), 0)),
                    # v5 하드필터 개별 통과여부(1/0) — 구버전 CSV는 기본값 1(통과)
                    'filt_ema_oi_l': int(_f(r.get('filt_ema_oi_l'), 1)), 'filt_ema_oi_s': int(_f(r.get('filt_ema_oi_s'), 1)),
                    'filt_cvd_l': int(_f(r.get('filt_cvd_l'), 1)), 'filt_cvd_s': int(_f(r.get('filt_cvd_s'), 1)),
                    'filt_volz_ok': int(_f(r.get('filt_volz_ok'), 1)),
                    'filt_m30_l': int(_f(r.get('filt_m30_l'), 1)), 'filt_m30_s': int(_f(r.get('filt_m30_s'), 1)),
                    'filt_liquidity_ok': int(_f(r.get('filt_liquidity_ok'), 1)),
                    'passes_all_l': int(_f(r.get('passes_all_l'), 1)), 'passes_all_s': int(_f(r.get('passes_all_s'), 1)),
                })
                cut = r.get('min_cut')
                if cut:
                    current_min_score = int(_f(cut, FALLBACK_MIN_SCORE))
                watch_cut = r.get('watch_cut')
                if watch_cut:
                    watch_current_min_score = int(_f(watch_cut, FALLBACK_WATCH_MIN_SCORE))
                pp_cut = r.get('pp_min_cut')
                if pp_cut:
                    pp_current_min_score = int(_f(pp_cut, FALLBACK_PP_MIN_SCORE))
                iv = r.get('interval')
                if iv:
                    current_interval = iv
                macro_state['btc_ema_l'] = int(_f(r.get('btc_ema_l'), 0))
                macro_state['btc_ema_s'] = int(_f(r.get('btc_ema_s'), 0))
                fng_raw = r.get('fng_value')
                macro_state['fng_value'] = int(_f(fng_raw)) if fng_raw not in (None, '') else None
                macro_state['fng_class'] = r.get('fng_class', '') or ''
                score_time = r.get('score_time', '') or score_time
                price_time = r.get('price_time', '') or price_time
        return rows, score_time, price_time
    except Exception:
        return None

def read_account_snapshot():
    global current_margin_mode, bank_balance, bank_total_deposit, bank_total_spent
    try:
        balance = 0
        ts = ""
        pos_list = []
        with open(ACCOUNT_SNAPSHOT, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            parse_mode = None
            for row in reader:
                if not row: continue
                if row[0] == 'balance':
                    balance = round(_f(row[1]), 2)
                elif row[0] == 'ts':
                    ts = row[1] if len(row) > 1 else ""
                elif row[0] == 'margin_mode':
                    if len(row) > 1 and row[1] in ('cross', 'isolated'):
                        current_margin_mode = row[1]
                elif row[0] == 'bank_balance':
                    bank_balance = round(_f(row[1]), 2)
                elif row[0] == 'bank_total_deposit':
                    bank_total_deposit = round(_f(row[1]), 2)
                elif row[0] == 'bank_total_spent':
                    bank_total_spent = round(_f(row[1]), 2)
                elif row[0] == 'positions':
                    parse_mode = 'positions'
                    continue
                elif parse_mode == 'positions' and len(row) >= 10:
                    pos_list.append({
                        'ticker': row[0],
                        'entry_price': _f(row[1]),
                        'amount': _f(row[2]),
                        'leverage': int(_f(row[3], 1)),
                        'position_type': row[4],
                        'entry_fee': _f(row[5]),
                        'entry_time': row[6],
                        'current_price': _f(row[7]),
                        'pnl': _f(row[8]),
                        'pnl_rate_pct': _f(row[9]),
                        'entry_score': _f(row[10], 0) if len(row) >= 11 else 0,
                    })
        return balance, ts, pos_list
    except Exception:
        return None

def send_command(action, ticker='', amount=0, leverage=0, position_type=''):
    cmd_id = f"{int(time.time()*1000)}_{random.randint(1000, 9999)}"
    fname = f"cmd_{cmd_id}.csv"
    tmp = os.path.join(CMD_DIR, "." + fname)
    final = os.path.join(CMD_DIR, fname)
    with open(tmp, 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerow([cmd_id, action, ticker, amount, leverage, position_type])
    os.replace(tmp, final)
    return cmd_id

def find_result(cmd_id):
    try:
        with open(RESULTS_FILE, 'r', newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                if row.get('cmd_id') == cmd_id:
                    return row.get('status', ''), row.get('message', '')
    except Exception:
        pass
    return None

def read_history_csv():
    out = []
    if not os.path.exists(HISTORY_FILE):
        return out
    try:
        with open(HISTORY_FILE, 'r', newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                out.append({
                    'type': row.get('type', ''),
                    'ticker': row.get('ticker', ''),
                    'direction': row.get('direction', ''),
                    'amount': _f(row.get('amount')),
                    'leverage': int(_f(row.get('leverage'))),
                    'entry_price': _f(row.get('entry_price')),
                    'exit_price': _f(row.get('exit_price')),
                    'pnl': _f(row.get('pnl')),
                    'pnl_rate_pct': _f(row.get('pnl_rate_pct')),
                    'entry_time': row.get('entry_time', ''),
                    'exit_time': row.get('exit_time', ''),
                })
    except Exception:
        pass
    return out

# ============================================================
# GUI
# ============================================================
class TradingClient:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("모의투자 주문용 (서버 연동)")
        self.root.update_idletasks()
        w = self.root.winfo_screenwidth()
        h = self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}")
        self.root.resizable(True, True)

        self.history_win = None
        self.pinned_tickers = set()
        self.custom_filter_rules = [None, None, None]  # 각 원소: None 또는 {'field','cmp','value'}
        self._last_render_data = []
        self._tap_state = {"last_time": 0.0, "last_ticker": None}
        self.DOUBLE_TAP_MS = 450
        self._account = (0, "", [])
        self._market_mtime = 0.0

        self.is_mobile = w <= 1280
        if self.is_mobile:
            FONT_BASE, FONT_BOLD_LABEL, FONT_BTN, FONT_INPUT, FONT_SMALL = 5, 5, 5, 5, 4
            LABEL_PADX, BTN_PADX, BTN_IPADY = 5, 6, 4
            ENTRY_W_TICKER, ENTRY_W_AMOUNT, ENTRY_W_LEV = 7, 7, 4
            BTN_WIDTH, BTN_EXIT_WIDTH = 12, 14
        else:
            FONT_BASE, FONT_BOLD_LABEL, FONT_BTN, FONT_INPUT, FONT_SMALL = 11, 11, 11, 12, 10
            LABEL_PADX, BTN_PADX, BTN_IPADY = 10, 15, 6
            ENTRY_W_TICKER, ENTRY_W_AMOUNT, ENTRY_W_LEV = 10, 10, 6
            BTN_WIDTH, BTN_EXIT_WIDTH = 10, 12
        self.ui_font_base = FONT_BASE
        self.FONT_SMALL = FONT_SMALL

        # 상단 정보
        info_outer = tk.Frame(self.root, bd=1, relief="groove")
        info_outer.pack(side="top", fill="x", padx=6, pady=(6, 2))

        # 외부 통장 (거래소 밖의 내 진짜 지갑) — 입금은 새 돈이 생기는 것, 출금은 실생활로 빠져나가 사라지는 것
        row0 = tk.Frame(info_outer)
        row0.pack(fill="x", padx=4, pady=(3, 1))
        self.bank_label = tk.Label(row0, text="외부통장: $-", font=("Arial", FONT_BOLD_LABEL, "bold"), fg="#5a3d99")
        self.bank_label.pack(side="left", padx=LABEL_PADX)
        btn_bank_deposit = tk.Label(row0, text="통장입금", bg="#5a3d99", fg="white", font=("Arial", FONT_BTN, "bold"),
                                    relief="raised", bd=1, cursor="hand2", padx=6, pady=1)
        btn_bank_deposit.pack(side="left", padx=4)
        btn_bank_deposit.bind("<ButtonRelease-1>", lambda e: self.bank_deposit())
        btn_bank_withdraw = tk.Label(row0, text="통장출금", bg="#8a6dc9", fg="white", font=("Arial", FONT_BTN, "bold"),
                                     relief="raised", bd=1, cursor="hand2", padx=6, pady=1)
        btn_bank_withdraw.pack(side="left", padx=4)
        btn_bank_withdraw.bind("<ButtonRelease-1>", lambda e: self.bank_withdraw())

        row1 = tk.Frame(info_outer)
        row1.pack(fill="x", padx=4, pady=(3, 1))
        self.cash_label = tk.Label(row1, text="현금: $-", font=("Arial", FONT_BOLD_LABEL, "bold"), fg="blue")
        self.cash_label.pack(side="left", padx=LABEL_PADX)
        self.invested_label = tk.Label(row1, text="투입: $-", font=("Arial", FONT_BOLD_LABEL, "bold"), fg="darkorange")
        self.invested_label.pack(side="left", padx=LABEL_PADX)
        self.pnl_label = tk.Label(row1, text="수익: $-", font=("Arial", FONT_BOLD_LABEL, "bold"), fg="green")
        self.pnl_label.pack(side="left", padx=LABEL_PADX)
        btn_charge = tk.Label(row1, text="거래소충전", bg="#1a7abf", fg="white", font=("Arial", FONT_BTN, "bold"),
                              relief="raised", bd=1, cursor="hand2", padx=6, pady=1)
        btn_charge.pack(side="left", padx=4)
        btn_charge.bind("<ButtonRelease-1>", lambda e: self.add_funds())
        btn_withdraw = tk.Label(row1, text="거래소출금", bg="#0f5c8a", fg="white", font=("Arial", FONT_BTN, "bold"),
                                relief="raised", bd=1, cursor="hand2", padx=6, pady=1)
        btn_withdraw.pack(side="left", padx=4)
        btn_withdraw.bind("<ButtonRelease-1>", lambda e: self.withdraw_funds())
        btn_help = tk.Label(row1, text="설명", bg="#888888", fg="white", font=("Arial", FONT_BTN, "bold"),
                            relief="raised", bd=1, cursor="hand2", padx=6, pady=1)
        btn_help.pack(side="left", padx=4)
        btn_help.bind("<ButtonRelease-1>", lambda e: self.show_help())
        btn_reset = tk.Label(row1, text="리셋", bg="#aa3333", fg="white", font=("Arial", FONT_BTN, "bold"),
                             relief="raised", bd=1, cursor="hand2", padx=6, pady=1)
        btn_reset.pack(side="left", padx=4)
        btn_reset.bind("<ButtonRelease-1>", lambda e: self.reset_balance())
        self.btn_margin_mode = tk.Label(row1, text="크로스", bg="#1a7abf", fg="white",
                                         font=("Arial", FONT_BTN, "bold"),
                                         relief="raised", bd=1, cursor="hand2", padx=6, pady=1)
        self.btn_margin_mode.pack(side="left", padx=4)
        self.btn_margin_mode.bind("<ButtonRelease-1>", lambda e: self.toggle_margin_mode())
        self._last_margin_mode_shown = None

        row2 = tk.Frame(info_outer)
        row2.pack(fill="x", padx=4, pady=(1, 3))
        self.total_label = tk.Label(row2, text="총자산: $-", font=("Arial", FONT_BOLD_LABEL, "bold"), fg="black")
        self.total_label.pack(side="left", padx=LABEL_PADX)
        self.server_label = tk.Label(row2, text="서버: 연결 대기...", font=("Arial", FONT_SMALL), fg="gray")
        self.server_label.pack(side="left", padx=LABEL_PADX)
        self.macro_label = tk.Label(row2, text="", font=("Arial", FONT_SMALL, "bold"), fg="gray")
        self.macro_label.pack(side="right", padx=LABEL_PADX)

        # 버튼
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(side="bottom", fill="x", padx=4, pady=(2, 8))
        lbl_font = ("Arial", FONT_BTN, "bold")
        for text, color, cmd in [
            ("롱 진입", "#44aa44", lambda e: self.open_position("long")),
            ("숏 진입", "#dd3333", lambda e: self.open_position("short")),
            ("청산", "#555555", lambda e: self.close_position()),
            ("기록 보기", "#eeeeee", lambda e: self.show_history()),
        ]:
            btn = tk.Label(btn_frame, text=text, bg=color, fg="white" if color != "#eeeeee" else "black",
                           font=lbl_font, width=BTN_WIDTH, relief="raised", bd=1, cursor="hand2")
            btn.pack(side="left", padx=BTN_PADX, ipady=BTN_IPADY)
            btn.bind("<ButtonRelease-1>", cmd)
        btn_exit = tk.Label(btn_frame, text="종료", bg="#cc0000", fg="white", font=lbl_font,
                            width=BTN_EXIT_WIDTH, relief="raised", bd=1, cursor="hand2")
        btn_exit.pack(side="right", padx=BTN_PADX, ipady=BTN_IPADY)
        btn_exit.bind("<ButtonRelease-1>", lambda e: self.safe_exit())
        # 종료 버튼 바로 왼쪽: 포지션 카드 패널 수동 보이기/숨기기 토글.
        # 포지션이 없으면 패널은 원래 자동으로 숨겨지므로, 이 토글은 "포지션이 있을 때
        # 일부러 숨겨서 마켓 리스트를 더 넓게 보고 싶은" 경우를 위한 것이다.
        self.btn_toggle_pos = tk.Label(btn_frame, text="포지션 숨기기", bg="#444444", fg="white",
                                        font=lbl_font, relief="raised", bd=1, cursor="hand2")
        self.btn_toggle_pos.pack(side="right", padx=BTN_PADX, ipady=BTN_IPADY)
        self.btn_toggle_pos.bind("<ButtonRelease-1>", lambda e: self._toggle_pos_panel())

        # 입력
        input_frame = tk.Frame(self.root, bd=1, relief="groove")
        input_frame.pack(side="bottom", fill="x", padx=6, pady=(2, 1))
        inp = tk.Frame(input_frame)
        inp.pack(padx=4, pady=4)
        tk.Label(inp, text="티커:", font=("Arial", FONT_INPUT)).grid(row=0, column=0, padx=(0, 2))
        self.ticker_entry = tk.Entry(inp, width=ENTRY_W_TICKER, font=("Arial", FONT_INPUT))
        self.ticker_entry.grid(row=0, column=1, padx=(0, 8))
        tk.Label(inp, text="금액($):", font=("Arial", FONT_INPUT)).grid(row=0, column=2, padx=(0, 2))
        self.amount_entry = tk.Entry(inp, width=ENTRY_W_AMOUNT, font=("Arial", FONT_INPUT))
        self.amount_entry.grid(row=0, column=3, padx=(0, 8))
        tk.Label(inp, text="배율(x):", font=("Arial", FONT_INPUT)).grid(row=0, column=4, padx=(0, 2))
        self.leverage_entry = tk.Entry(inp, width=ENTRY_W_LEV, font=("Arial", FONT_INPUT))
        self.leverage_entry.insert(0, str(DEFAULT_LEVERAGE))
        self.leverage_entry.grid(row=0, column=5, padx=(0, 2))

        # 포지션 패널
        self.pos_panel = tk.Frame(self.root, bd=1, relief="groove", bg="#16181d")
        self.pos_panel.pack(side="bottom", fill="x", padx=6, pady=(2, 1))
        self.pos_canvas = tk.Canvas(self.pos_panel, height=280, bg="#16181d", highlightthickness=0)
        self.pos_canvas.pack(side="left", fill="both", expand=True)
        self.pos_inner = tk.Frame(self.pos_canvas, bg="#16181d")
        self._pos_window = self.pos_canvas.create_window((0, 0), window=self.pos_inner, anchor="nw")
        self.pos_canvas.bind("<Configure>", lambda e: self.pos_canvas.itemconfig(self._pos_window, width=e.width))
        self.pos_inner.bind("<Configure>", lambda e: self.pos_canvas.configure(scrollregion=self.pos_canvas.bbox("all")))
        self._pos_drag = {"y": 0, "view": 0.0}
        self.pos_canvas.bind("<ButtonPress-1>", self._pos_press)
        self.pos_canvas.bind("<B1-Motion>", self._pos_motion)
        self.pos_canvas.bind("<MouseWheel>", lambda e: self.pos_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.pos_cards = {}
        self._score_hist = {}  # ticker -> {'long': deque, 'short': deque} — 포지션 신호등(추세) 판단용
        self._price_hist = {}  # ticker -> deque(price) — Predict Score의 가격 기울기(Slope) 계산용
        self._latest_row_by_ticker = {}
        self._last_pos_order = None
        self._pos_panel_visible = True
        self._pos_panel_user_hidden = False  # 종료 왼쪽 토글 버튼으로 사용자가 직접 숨긴 상태
        self._hide_pos_panel()

        # 타임프레임(계산 기준 캔들) 전환 버튼 — 테이블 바로 위
        tf_bar = tk.Frame(self.root)
        tf_bar.pack(side="top", fill="x", padx=4, pady=(2, 0))
        tk.Label(tf_bar, text="기준봉:", font=("Arial", self.FONT_SMALL, "bold")).pack(side="left", padx=(2, 4))
        self._tf_btn_widgets = {}
        for iv in ALLOWED_INTERVALS:
            b = tk.Label(tf_bar, text=iv, font=("Arial", self.FONT_SMALL, "bold"), bg="#eeeeee", fg="black",
                         relief="raised", bd=1, cursor="hand2", padx=8, pady=2)
            b.pack(side="left", padx=2)
            b.bind("<ButtonRelease-1>", lambda e, i=iv: self.set_interval(i))
            self._tf_btn_widgets[iv] = b
        self._last_interval_shown = None

        tk.Label(tf_bar, text="검색:", font=("Arial", self.FONT_SMALL, "bold")).pack(side="left", padx=(10, 3))
        self.search_entry = tk.Entry(tf_bar, font=("Arial", self.FONT_SMALL), width=10)
        self.search_entry.pack(side="left", padx=(0, 2))
        self.search_entry.bind("<KeyRelease>", self._on_search_change)
        btn_search_clear = tk.Label(tf_bar, text="✕", font=("Arial", self.FONT_SMALL, "bold"),
                                     bg="#dddddd", fg="black", relief="raised", bd=1, cursor="hand2", padx=5, pady=1)
        btn_search_clear.pack(side="left", padx=2)
        btn_search_clear.bind("<ButtonRelease-1>", self._clear_search)

        # v5 하드필터(EMA/OI방향·CVD·VolZ·30분모멘텀·유동성) 적용 여부를 직접 고를 수 있는
        # 체크박스. 서버는 필터로 점수를 강제 0점 처리하지 않고 통과여부만 같이 내려주므로,
        # 여기서 체크를 껐다 켰다 하면서 필터가 실제로 도움되는지 바로 비교해볼 수 있다.
        self.filter_enabled = tk.BooleanVar(value=True)
        filter_chk = tk.Checkbutton(tf_bar, text="필터 적용", variable=self.filter_enabled,
                                     font=("Arial", self.FONT_SMALL, "bold"),
                                     command=self._on_filter_toggle)
        filter_chk.pack(side="left", padx=(10, 2))

        btn_pdf = tk.Label(tf_bar, text="PDF 리포트", font=("Arial", self.FONT_SMALL, "bold"),
                            bg="#3a6fd8", fg="white", relief="raised", bd=1, cursor="hand2", padx=5, pady=1)
        btn_pdf.pack(side="left", padx=(6, 2))
        btn_pdf.bind("<ButtonRelease-1>", lambda e: self.export_indicator_report_pdf())

        btn_custom_filter = tk.Label(tf_bar, text="조건필터", font=("Arial", self.FONT_SMALL, "bold"),
                                      bg="#7a4fc9", fg="white", relief="raised", bd=1, cursor="hand2", padx=5, pady=1)
        btn_custom_filter.pack(side="left", padx=(6, 2))
        btn_custom_filter.bind("<ButtonRelease-1>", lambda e: self.show_custom_filter_dialog())

        btn_change_path = tk.Label(tf_bar, text="경로 변경", font=("Arial", self.FONT_SMALL, "bold"),
                                    bg="#555555", fg="white", relief="raised", bd=1, cursor="hand2", padx=5, pady=1)
        btn_change_path.pack(side="left", padx=(6, 2))
        btn_change_path.bind("<ButtonRelease-1>", lambda e: self.show_change_path_dialog())

        # 정렬 버튼
        sortbar = tk.Frame(self.root)
        sortbar.pack(side="top", fill="x", padx=4, pady=(0, 2))
        sort_btn_font = ("Arial", self.FONT_SMALL)
        sort_buttons = [
            ("기본", None), ("코인", "코인"), ("현재가", "현재가"), ("RSI", "RSI"),
            ("VolZ", "VolZ"), ("BB%", "BB%"), ("CVD", "CVD(1h)"),
            ("롱", "롱Score"), ("숏", "숏Score"), ("매집", "선매집"), ("분산", "선분산"),
            ("Fund", "Funding"), ("Vol24h", "24h Vol(M)"),
            ("ATR", "ATR%(1h)"), ("OI", "OI%(1h)"), ("30m%", "30m%"), ("L/S", "L/S"),
        ]
        self._sort_reverse = {col: False for col in [b[1] for b in sort_buttons if b[1] is not None]}
        self._sort_col = None
        self._sort_btn_widgets = {}
        self._sort_btn_labels = {}
        ncols_per_row = 6 if self.is_mobile else 11
        for i, (label, key) in enumerate(sort_buttons):
            b = tk.Label(sortbar, text=label, font=sort_btn_font, bg="#eeeeee", fg="black",
                         relief="raised", bd=1, cursor="hand2", padx=4, pady=1)
            b.grid(row=i // ncols_per_row, column=i % ncols_per_row, padx=1, pady=1, sticky="ew")
            b.bind("<ButtonRelease-1>", lambda e, k=key: self.set_sort(k))
            self._sort_btn_widgets[key] = b
            self._sort_btn_labels[key] = label
        for c in range(ncols_per_row):
            sortbar.grid_columnconfigure(c, weight=1)

        # 메인 카드
        tree_container = tk.Frame(self.root)
        tree_container.pack(fill="both", expand=True, padx=4, pady=2)
        self.card_canvas = tk.Canvas(tree_container, highlightthickness=0)
        self.card_canvas.grid(row=0, column=0, sticky="nsew")
        tree_container.grid_rowconfigure(0, weight=1)
        tree_container.grid_columnconfigure(0, weight=1)
        self.card_inner = tk.Frame(self.card_canvas)
        self._card_window = self.card_canvas.create_window((0, 0), window=self.card_inner, anchor="nw")
        self.card_inner.bind("<Configure>", lambda e: self.card_canvas.configure(scrollregion=self.card_canvas.bbox("all")))
        self.card_wraplength = max(w - 40, 100)
        self.card_canvas.bind("<Configure>", self._on_card_canvas_configure)
        self._card_drag_state = {"y": 0, "view_top": 0.0, "dragged": False}
        self.card_canvas.bind("<MouseWheel>", lambda e: self.card_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"), add="+")
        self.card_widgets = {}
        self._last_table_order = []
        self._last_reorder_time = 0.0
        self._reorder_interval = 3.0  # 순서 재배치는 3초마다만 (매 폴링마다 하면 깜빡임 심함)
        self._force_resort = True
        self.set_sort(None)

        self.poll_files()

    def show_history(self):
        if self.history_win and self.history_win.winfo_exists():
            self.history_win.lift()
            return

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        win_w = int(screen_w * 0.9)
        win_h = int(screen_h * 0.85)
        win_x = (screen_w - win_w) // 2
        win_y = (screen_h - win_h) // 2

        self.history_win = tk.Toplevel(self.root)
        self.history_win.title("거래 기록 (진행중 + 청산)")
        self.history_win.geometry(f"{win_w}x{win_h}+{win_x}+{win_y}")

        cols = ("status", "type", "ticker", "direction", "amount", "leverage", 
                "entry_price", "current_exit_price", "pnl", "pnl_rate_pct", "time")
        display_names = ("상태", "유형", "티커", "방향", "금액($)", "배율", 
                        "진입가", "현재/청산가", "손익($)", "수익률", "시간")

        tree = ttk.Treeview(self.history_win, columns=cols, show="headings", height=28)
        
        for col, name in zip(cols, display_names):
            tree.heading(col, text=name)
            tree.column(col, width=100, anchor="center")
        
        tree.column("ticker", width=85)
        tree.column("amount", width=120)
        tree.column("pnl", width=125)
        tree.column("pnl_rate_pct", width=85)
        tree.column("time", width=160)

        _, _, open_positions = self._account
        for p in open_positions:
            values = (
                "진행중",
                "포지션",
                p['ticker'],
                p['position_type'].upper(),
                f"{p['amount']:,.2f}",
                p['leverage'],
                f"{p['entry_price']:,.4f}" if p['entry_price'] < 1 else f"{p['entry_price']:,.2f}",
                f"{p['current_price']:,.4f}" if p['current_price'] < 1 else f"{p['current_price']:,.2f}",
                f"{p['pnl']:+,.2f}",
                f"{p['pnl_rate_pct']:+.2f}%",
                str(p.get('entry_time', ''))[:19]
            )
            tree.insert("", "end", values=values)

        history = read_history_csv()
        for rec in reversed(history):
            values = (
                "청산",
                rec['type'],
                rec['ticker'],
                rec['direction'],
                f"{rec['amount']:,.2f}",
                rec['leverage'],
                f"{rec['entry_price']:,.4f}" if rec['entry_price'] < 1 else f"{rec['entry_price']:,.2f}",
                f"{rec['exit_price']:,.4f}" if rec['exit_price'] < 1 else f"{rec['exit_price']:,.2f}",
                f"{rec['pnl']:+,.2f}",
                f"{rec['pnl_rate_pct']:+.2f}%",
                rec['entry_time'][:19]
            )
            tree.insert("", "end", values=values)

        if not open_positions and not history:
            tree.insert("", "end", values=("아직 기록이 없습니다.", "", "", "", "", "", "", "", "", "", ""))

        tree.pack(fill="both", expand=True, padx=10, pady=10)

        scrollbar = ttk.Scrollbar(self.history_win, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        tk.Button(self.history_win, text="닫기", command=self.history_win.destroy, 
                  font=("Arial", 11, "bold"), padx=30, pady=8).pack(pady=10)

    def _on_card_canvas_configure(self, event):
        self.card_canvas.itemconfig(self._card_window, width=event.width)
        new_wrap = max(event.width - 16, 100)
        if new_wrap != self.card_wraplength:
            self.card_wraplength = new_wrap
            for card in self.card_widgets.values():
                card._lbl1.config(wraplength=new_wrap)
                card._lbl2.config(wraplength=new_wrap)

    def _pos_press(self, event):
        self._pos_drag["y"] = event.y_root
        self._pos_drag["view"] = self.pos_canvas.yview()[0]
        self._pos_drag["dragged"] = False

    def _pos_motion(self, event):
        dy = event.y_root - self._pos_drag["y"]
        if abs(dy) > 4:
            self._pos_drag["dragged"] = True
        h = max(self.pos_canvas.winfo_height(), 1)
        ylo, yhi = self.pos_canvas.yview()
        yspan = max(yhi - ylo, 0.0001)
        new_y = self._pos_drag["view"] - (dy / h) * yspan
        self.pos_canvas.yview_moveto(max(0.0, min(1.0, new_y)))

    def _hide_pos_panel(self):
        if self._pos_panel_visible:
            self.pos_panel.pack_forget()
            self._pos_panel_visible = False

    def _show_pos_panel(self):
        if not self._pos_panel_visible:
            self.pos_panel.pack(side="bottom", fill="x", padx=6, pady=(2, 1))
            self._pos_panel_visible = True

    def _update_score_history(self, data_list):
        """매 스냅샷마다 티커별 '유효 점수'를 기록해둔다(값이 실제로 바뀔 때만 추가).
        유효 점수 = max(추세추종 점수, 매집/분산 점수) — 롱은 max(long_score, prepump_score),
        숏은 max(short_score, preshort_score). 두 점수 체계는 서로 다른 걸 보는 신호라서
        (숏=이미 하락전환, 분산=아직 안 터진 고점 매물대), 분산 신호 보고 들어갔는데
        Predict Score가 숏 점수만 보고 낮게 나오는 불일치를 막기 위함.
        Predict Score의 기울기(Slope)/가속도(Acceleration) 계산에 이 히스토리를 쓴다.
        동시에 티커별 '가장 최근 행'도 캐시해둬서 Predict Score의 나머지 항목
        (EMA/CVD/OI/VolZ)을 조회할 때 쓴다."""
        self._latest_row_by_ticker = {r['ticker']: r for r in data_list if r.get('ticker')}
        for r in data_list:
            t = r.get('ticker')
            if not t:
                continue
            hist = self._score_hist.setdefault(t, {'long': deque(maxlen=6), 'short': deque(maxlen=6)})
            eff_long = max(r.get('long_score', 0), r.get('prepump_score', 0))
            eff_short = max(r.get('short_score', 0), r.get('preshort_score', 0))
            for val, dq_name in ((eff_long, 'long'), (eff_short, 'short')):
                dq = hist[dq_name]
                if not dq or dq[-1] != val:
                    dq.append(val)
            price = r.get('price_usd') or r.get('price')
            if price:
                pdq = self._price_hist.setdefault(t, deque(maxlen=10))
                if not pdq or pdq[-1] != price:
                    pdq.append(price)

    # ============================================================
    # Predict Score (100점, v2 — 개편안 반영) — 진입 후 "이 방향이 계속 이어질지" 예측용.
    # v1은 '점수'의 기울기를 봤는데, v2는 문서 취지대로 '가격'의 기울기를 직접 본다.
    #   Slope(30) + Acceleration(25) + CVD방향일치(20) + EMA방향일치(15) + Level(10) = 100
    #
    # [원안 대비 구현 메모]
    #   - Slope/Accel: 가격 스냅샷 이력(최대 10개, 값이 바뀔 때만 저장)으로 계산.
    #     문서의 "직전 4회 가격 변화량의 SMA"를 '보유 방향으로 유리한 쪽 부호로 뒤집은
    #     % 변화량'의 평균으로 구현 (숏이면 하락이 양수가 되게 부호 반전).
    #   - EMA 방향일치: 문서는 "5분봉 EMA9"인데 서버가 5분봉을 안 갖고 있어서,
    #     이미 갖고 있는 EMA20(기준봉 단위)과 현재가 비교로 근사했다.
    #   - Level: "진입 당시 원본 스코어"를 그대로 써야 해서, 서버가 진입 시점에
    #     저장해둔 entry_score(포지션에 저장됨)를 넘겨받아 사용한다. 없으면(구버전
    #     포지션 등) 중간값으로 처리.
    # ============================================================
    def _predict_slope(self, ticker, direction):
        """
        직전 가격 변화량들의 평균(SMA)과, 한 칸 앞선 구간의 평균을 비교해서
        '기울기가 유지/완만해짐/누움'을 판단한다. 보유 방향에 유리한 쪽이 양수가
        되도록 부호를 맞춘다(숏이면 하락이 +).
        반환: (current_slope, previous_slope, points)
        """
        pdq = self._price_hist.get(ticker)
        if not pdq or len(pdq) < 3:
            return 0.0, 0.0, 15  # 데이터 부족 — 중간값으로 보수적 처리
        prices = list(pdq)
        pct_diffs = []
        for i in range(1, len(prices)):
            d = (prices[i] - prices[i - 1]) / prices[i - 1] * 100
            pct_diffs.append(d if direction == 'long' else -d)
        cur_win = pct_diffs[-4:]
        prev_win = pct_diffs[-8:-4] if len(pct_diffs) >= 5 else pct_diffs[:-1]
        current_slope = sum(cur_win) / len(cur_win) if cur_win else 0.0
        previous_slope = sum(prev_win) / len(prev_win) if prev_win else current_slope

        if current_slope <= 0:
            pts = 0
        elif previous_slope <= 0:
            pts = 30  # 이전엔 죽어있다가 지금 살아남 — 유지력 양호
        elif current_slope <= previous_slope * 0.7:
            pts = 10  # 직전 대비 30% 이상 완만해짐
        else:
            pts = 30
        return current_slope, previous_slope, pts

    def _predict_accel(self, current_slope, previous_slope):
        """A = Slope_t - Slope_(t-1). A>0이면 가속 중(25점), 그 외 둔화(5점)."""
        a = current_slope - previous_slope
        return a, (25 if a > 0 else 5)

    def _predict_cvd_v2(self, row, direction):
        """CVD 방향일치(최대 20점, 이분법). 방향과 cvd_diff 부호가 맞으면 20, 아니면 0."""
        cvd_diff = row.get('cvd_diff', 0) or 0
        want_positive = (direction == 'long')
        aligned = (cvd_diff > 0) == want_positive
        return 20 if aligned else 0

    def _predict_ema_v2(self, row, direction):
        """
        EMA 방향일치(최대 15점, 이분법). 문서는 5분봉 EMA9 위/아래인데 서버엔 5분봉이
        없어 EMA20(기준봉 단위)과 현재가 비교로 근사했다.
        """
        price = row.get('price_usd') or row.get('price')
        ema20 = row.get('ema20')
        if not price or not ema20:
            return 0
        above = price > ema20
        return 15 if (above if direction == 'long' else not above) else 0

    def _predict_level_v2(self, entry_score):
        """Level(최대 10점). 진입 당시 원본 스코어 85점 이상→10, 70~75점 턱걸이→5, 그 외 0."""
        if entry_score >= 85:
            return 10
        elif 70 <= entry_score <= 75:
            return 5
        return 0

    def predict_score(self, ticker, direction, entry_score=0):
        """
        Predict Score(0~100) + 등급/트렌드/위험도를 한번에 계산해서 돌려준다.
        entry_score는 포지션의 '진입 당시 유효 점수'(서버가 진입 시점에 저장해둔 값).
        반환: dict(score, tier_label, tier_color, trend, risk)
        """
        row = self._latest_row_by_ticker.get(ticker)
        if row is None:
            return {'score': 0, 'tier_label': '데이터 없음', 'tier_color': '#5a5f66',
                    'trend': 'flat', 'risk': 'MID'}

        cur_slope, prev_slope, slope_pts = self._predict_slope(ticker, direction)
        accel, accel_pts = self._predict_accel(cur_slope, prev_slope)
        cvd_pts = self._predict_cvd_v2(row, direction)
        ema_pts = self._predict_ema_v2(row, direction)
        level_pts = self._predict_level_v2(entry_score)

        total = slope_pts + accel_pts + cvd_pts + ema_pts + level_pts
        total = max(0, min(100, total))

        if total >= 90: tier_label, tier_color = "🔥 매우 강함", "#0ecb81"
        elif total >= 80: tier_label, tier_color = "🟢 지속 가능성 높음", "#3ddc84"
        elif total >= 70: tier_label, tier_color = "🟡 일부 경계", "#f0b90b"
        elif total >= 60: tier_label, tier_color = "🟠 힘 약해짐", "#ff9500"
        elif total >= 50: tier_label, tier_color = "🔴 손절 준비", "#f6465a"
        else: tier_label, tier_color = "⚫ 추세 붕괴 위험", "#8b0000"

        trend = 'up' if cur_slope > 0 else ('down' if cur_slope < 0 else 'flat')
        risk_flags = sum([accel <= 0, cvd_pts == 0, total < 60])
        risk = 'HIGH' if risk_flags >= 2 or total < 50 else ('MID' if risk_flags == 1 or total < 75 else 'LOW')

        return {'score': total, 'tier_label': tier_label, 'tier_color': tier_color,
                'trend': trend, 'risk': risk}

    def _toggle_pos_panel(self):
        self._pos_panel_user_hidden = not self._pos_panel_user_hidden
        if self._pos_panel_user_hidden:
            self.btn_toggle_pos.config(text="포지션 보이기")
            self._hide_pos_panel()
        else:
            self.btn_toggle_pos.config(text="포지션 숨기기")
            self._show_pos_panel()  # 실제 포지션이 없으면 다음 자동 갱신 때 다시 숨겨짐

    def _render_pos_panel(self, pos_list):
        if not pos_list:
            self._hide_pos_panel()
            for t in list(self.pos_cards.keys()):
                self.pos_cards[t].destroy()
                del self.pos_cards[t]
            self._last_pos_order = None
            return

        # 사용자가 수동으로 숨긴 상태면 패널 자체는 안 띄우지만, 카드 내용은 계속
        # 최신으로 갱신해둔다 — 나중에 "포지션 보이기"를 눌렀을 때 바로 정상 표시되도록.
        if not self._pos_panel_user_hidden:
            self._show_pos_panel()

        # 높이는 아래에서 실제 카드 높이를 측정해 '최대 2개'에 딱 맞게 설정한다.
        # (고정 픽셀 추정은 고밀도 화면에서 카드가 잘리는 원인이었음)

        fs = self.ui_font_base
        fs_small = max(fs - 1, 4)
        DARK_BG, FG, DIM = "#16181d", "#e8e8e8", "#9aa0a6"

        for t in list(self.pos_cards.keys()):
            if t not in [p['ticker'] for p in pos_list]:
                self.pos_cards[t].destroy()
                del self.pos_cards[t]

        for p in pos_list:
            t = p['ticker']
            entry = p['entry_price']
            cur = p['current_price']
            lev = p['leverage']
            amt = p['amount']
            ptype = p['position_type']
            pnl = p['pnl']
            roe = p['pnl_rate_pct']
            is_long = (ptype == 'long')
            LIQ_RATIO = 0.9  # 실제 청산은 수수료/유지증거금 때문에 이론값(1/배율)보다 일찍(약 90%) 발생
            liq_dist = (1.0 / lev) * LIQ_RATIO
            liq = entry * (1 - liq_dist) if is_long else entry * (1 + liq_dist)
            # Margin Ratio: 실제 강제청산 기준(증거금의 90% 손실)을 100%로 스케일링
            # → 서버의 강제청산 발동 시점과 정확히 일치하는 청산 위험도 게이지
            risk = min(100.0, max(0.0, -pnl / (amt * LIQ_RATIO) * 100)) if amt > 0 else 0.0
            size = amt * lev + pnl  # Size = 진입 시 명목가치 + 손익 (손실이면 줄고 이익이면 늘어남)
            pnl_color = "#0ecb81" if pnl >= 0 else "#f6465a"
            risk_color = "#f6465a" if risk >= 70 else ("#f0b90b" if risk >= 30 else DIM)
            side_color = "#0ecb81" if is_long else "#f6465a"
            side_txt = "L" if is_long else "S"
            direction = "롱" if is_long else "숏"

            card = self.pos_cards.get(t)
            if card is None:
                card = tk.Frame(self.pos_inner, bg=DARK_BG, bd=1, relief="solid",
                                 highlightbackground="#2b2f36", highlightthickness=1)

                # --- 헤더: 배지 + 티커 + Perp/Cross 태그 + 경고 ---
                hdr = tk.Frame(card, bg=DARK_BG)
                hdr.pack(fill="x", padx=8, pady=(8, 4))
                card._badge = tk.Label(hdr, font=("Arial", fs, "bold"), width=2, fg="white")
                card._badge.pack(side="left")
                card._title = tk.Label(hdr, font=("Arial", fs + 1, "bold"), bg=DARK_BG, fg=FG)
                card._title.pack(side="left", padx=(6, 6))
                card._tag_perp = tk.Label(hdr, text="Perp", font=("Arial", fs_small, "bold"),
                                           bg="#2b2f36", fg=DIM, padx=6, pady=1)
                card._tag_perp.pack(side="left", padx=(0, 4))
                card._tag_cross = tk.Label(hdr, font=("Arial", fs_small, "bold"),
                                            bg="#2b2f36", fg=DIM, padx=6, pady=1)
                card._tag_cross.pack(side="left")
                card._trend_light = tk.Label(hdr, font=("Arial", fs_small, "bold"), bg=DARK_BG, fg=DIM)
                card._trend_light.pack(side="left", padx=(5, 0))
                card._warn = tk.Label(hdr, font=("Arial", fs_small, "bold"), bg=DARK_BG, fg="#f6465a")
                card._warn.pack(side="right")

                tk.Frame(card, bg="#2b2f36", height=1).pack(fill="x", padx=8)

                # --- Unrealized PNL / ROI ---
                pnl_row = tk.Frame(card, bg=DARK_BG)
                pnl_row.pack(fill="x", padx=8, pady=(6, 4))
                pnl_col = tk.Frame(pnl_row, bg=DARK_BG)
                pnl_col.pack(side="left", anchor="w")
                tk.Label(pnl_col, text="Unrealized PNL ($)", font=("Arial", fs_small),
                         bg=DARK_BG, fg=DIM, anchor="w").pack(anchor="w")
                card._pnl = tk.Label(pnl_col, font=("Arial", fs + 4, "bold"), bg=DARK_BG, anchor="w")
                card._pnl.pack(anchor="w")
                roi_col = tk.Frame(pnl_row, bg=DARK_BG)
                roi_col.pack(side="right", anchor="e")
                tk.Label(roi_col, text="ROI", font=("Arial", fs_small),
                         bg=DARK_BG, fg=DIM, anchor="e").pack(anchor="e")
                card._roe = tk.Label(roi_col, font=("Arial", fs + 2, "bold"), bg=DARK_BG, anchor="e")
                card._roe.pack(anchor="e")

                def _cell(parent, col, label_text):
                    anchor = "w" if col == 0 else ("center" if col == 1 else "e")
                    justify = {"w": "left", "e": "right", "center": "center"}[anchor]
                    c = tk.Frame(parent, bg=DARK_BG)
                    c.grid(row=0, column=col, sticky="w" if col == 0 else ("we" if col == 1 else "e"))
                    tk.Label(c, text=label_text, font=("Arial", fs_small), bg=DARK_BG, fg=DIM,
                             anchor=anchor, justify=justify).pack(fill="x")
                    val = tk.Label(c, font=("Arial", fs, "bold"), bg=DARK_BG, fg=FG,
                                    anchor=anchor, justify=justify)
                    val.pack(fill="x")
                    return val

                # --- Size / Margin / Margin Ratio ---
                row2 = tk.Frame(card, bg=DARK_BG)
                row2.pack(fill="x", padx=8, pady=(2, 4))
                for i in range(3):
                    row2.grid_columnconfigure(i, weight=1, uniform="row2")
                card._size_val = _cell(row2, 0, "Size ($)")
                card._margin_val = _cell(row2, 1, "Margin ($)")
                card._mratio_val = _cell(row2, 2, "Margin Ratio")

                # --- Entry Price / Mark Price / Liq. Price ---
                row3 = tk.Frame(card, bg=DARK_BG)
                row3.pack(fill="x", padx=8, pady=(2, 8))
                for i in range(3):
                    row3.grid_columnconfigure(i, weight=1, uniform="row3")
                card._entry_val = _cell(row3, 0, "Entry Price")
                card._mark_val = _cell(row3, 1, "Mark Price")
                card._liq_val = _cell(row3, 2, "Liq. Price")

                # --- 버튼: Leverage / Close ---
                btn_row = tk.Frame(card, bg=DARK_BG)
                btn_row.pack(fill="x", padx=8, pady=(0, 8))
                for i in range(2):
                    btn_row.grid_columnconfigure(i, weight=1)
                btn_lev = tk.Label(btn_row, text="Leverage", font=("Arial", fs_small, "bold"),
                                    bg="#2b2f36", fg=FG, relief="raised", bd=1, cursor="hand2", pady=3)
                btn_lev.grid(row=0, column=0, sticky="ew", padx=(0, 3))
                btn_close = tk.Label(btn_row, text="Close", font=("Arial", fs_small, "bold"),
                                      bg="#3a3f47", fg=FG, relief="raised", bd=1, cursor="hand2", pady=3)
                btn_close.grid(row=0, column=1, sticky="ew", padx=(3, 0))
                btn_lev.bind("<ButtonRelease-1>", lambda e, tk_=t: self._show_leverage_info(tk_))
                btn_close.bind("<ButtonRelease-1>", lambda e, tk_=t: self.close_ticker(tk_))

                def _fill(e, tk_=t):
                    # 드래그(스크롤)였다면 티커 채우기로 처리하지 않음
                    if self._pos_drag.get("dragged"):
                        return
                    self.ticker_entry.delete(0, tk.END)
                    self.ticker_entry.insert(0, tk_)
                for wdg in (card, hdr, card._badge, card._title, card._tag_perp, card._tag_cross,
                            card._trend_light, pnl_row, pnl_col, card._pnl, roi_col, card._roe, row2, row3):
                    wdg.bind("<ButtonPress-1>", self._pos_press, add="+")
                    wdg.bind("<B1-Motion>", self._pos_motion, add="+")
                    wdg.bind("<ButtonRelease-1>", _fill, add="+")
                self.pos_cards[t] = card

            self._cfg(card._badge, text=side_txt, bg=side_color)
            self._cfg(card._title, text=t)
            self._cfg(card._tag_cross, text=f"Cross {lev}x")
            pred = self.predict_score(t, ptype, p.get('entry_score', 0))  # Predict Score(0~100)
            arrow = {"up": "▲", "down": "▼", "flat": "‒"}[pred['trend']]
            self._cfg(card._trend_light, text=f"P{pred['score']}{arrow}", fg=pred['tier_color'])
            warn_txt = "!!!!" if risk >= 70 else ("PRED⚠" if pred['risk'] == 'HIGH' else "")
            self._cfg(card._warn, text=warn_txt)
            self._cfg(card._pnl, text=f"{pnl:+,.2f}", fg=pnl_color)
            self._cfg(card._roe, text=f"{roe:+.2f}%", fg=pnl_color)
            self._cfg(card._size_val, text=f"{size:,.2f}")
            self._cfg(card._margin_val, text=f"{amt:,.2f}")
            self._cfg(card._mratio_val, text=f"{risk:.1f}%", fg=risk_color)
            fmt = (lambda v: f"{v:,.2f}") if entry >= 1 else (lambda v: f"{v:,.4f}")
            self._cfg(card._entry_val, text=fmt(entry))
            self._cfg(card._mark_val, text=fmt(cur))
            self._cfg(card._liq_val, text=fmt(liq))

        new_pos_order = [p['ticker'] for p in pos_list]
        if new_pos_order != getattr(self, '_last_pos_order', None):
            for t in new_pos_order:
                self.pos_cards[t].pack_forget()
            for t in new_pos_order:
                self.pos_cards[t].pack(fill="x", padx=4, pady=4)
            self._last_pos_order = new_pos_order

        self.pos_inner.update_idletasks()
        self.pos_canvas.configure(scrollregion=self.pos_canvas.bbox("all"))
        # 실제 렌더링된 카드 높이를 측정해 '최대 2개'가 딱 보이는 높이로 설정.
        # pack(pady=3)의 상하 여백(6px)도 포함해야 잘리지 않는다.
        try:
            cards = [self.pos_cards[p['ticker']] for p in pos_list if p['ticker'] in self.pos_cards]
            show_n = min(2, len(cards))
            need = sum(c.winfo_reqheight() + 6 for c in cards[:show_n])
            if need > 0 and need != self.pos_canvas.winfo_height():
                self.pos_canvas.config(height=need)
        except Exception:
            pass

    def poll_files(self):
        try:
            mt = os.path.getmtime(MARKET_SNAPSHOT)
        except Exception:
            mt = 0
        if mt and mt != self._market_mtime:
            res = read_market_snapshot()
            if res:
                rows, score_time, price_time = res
                self._market_mtime = mt
                self._render_table(rows)
                if current_interval != self._last_interval_shown:
                    self._highlight_interval_buttons(current_interval)
        age = time.time() - mt if mt else 1e9
        if age > STALE_SEC:
            self.server_label.config(text="서버: 연결 끊김 (trading_server.py 실행 확인)", fg="red")
        else:
            self.server_label.config(
                text=f"서버: 정상 (기준봉 {current_interval} / 관심 {watch_current_min_score}·컷 {current_min_score}점 / "
                     f"매집·분산 컷 {pp_current_min_score}점, {age:.0f}초 전 갱신)",
                fg="gray")
        btc_dir = ("BTC↓추세" if macro_state.get('btc_ema_s') == 30
                   else "BTC↑추세" if macro_state.get('btc_ema_l') == 30 else "BTC중립")
        fng_v = macro_state.get('fng_value')
        fng_txt = f"공포탐욕 {fng_v}({macro_state.get('fng_class','')})" if fng_v is not None else "공포탐욕 N/A"
        is_risky = (macro_state.get('btc_ema_s') == 30 or macro_state.get('btc_ema_l') == 30
                    or (fng_v is not None and (fng_v >= 80 or fng_v <= 20)))
        self.macro_label.config(text=f"{'⚠️ ' if is_risky else ''}{btc_dir} | {fng_txt}",
                                 fg="#cc4400" if is_risky else "gray")
        acc = read_account_snapshot()
        if acc:
            self._account = acc
            self._update_account_labels()
            if current_margin_mode != self._last_margin_mode_shown:
                self._highlight_margin_mode(current_margin_mode)
        self.root.after(700, self.poll_files)

    def _update_account_labels(self):
        balance, ts, pos_list = self._account
        invested = sum(p['amount'] for p in pos_list)
        total_pnl = sum(p['pnl'] for p in pos_list)
        total = balance + invested + total_pnl
        self.cash_label.config(text=f"현금: ${balance:,.2f}")
        self.invested_label.config(text=f"투입: ${invested:,.2f}")
        self.pnl_label.config(text=f"수익: ${total_pnl:+,.2f}", fg="red" if total_pnl < 0 else "green")
        self.total_label.config(text=f"총자산: ${total:,.2f}")
        # 외부통장 잔액 + (지금까지 실생활로 빠져나간 돈) - (지금까지 넣은 돈) = 진짜 순수익(트레이딩 성과)
        net_profit = (total + bank_balance) + bank_total_spent - bank_total_deposit
        self.bank_label.config(
            text=f"외부통장: ${bank_balance:,.2f}  (순수익 {net_profit:+,.2f})",
            fg="green" if net_profit >= 0 else "red"
        )
        self._render_pos_panel(pos_list)

    def _sort_key_fn(self):
        if self._sort_col is None:
            return lambda x: x.get('long_score', 0) + x.get('short_score', 0)
        key_map = {
            "코인": lambda r: r['ticker'],
            "현재가": lambda r: r.get('price_usd') or 0,
            "RSI": lambda r: r['rsi'],
            "VolZ": lambda r: r['vol_z'],
            "BB%": lambda r: r['bb_percent'],
            "CVD(1h)": lambda r: r.get('cvd', 0),
            "롱Score": lambda r: r['long_score'],
            "숏Score": lambda r: r['short_score'],
            "선매집": lambda r: r.get('prepump_score', 0),
            "선분산": lambda r: r.get('preshort_score', 0),
            "Funding": lambda r: r.get('funding', 0),
            "24h Vol(M)": lambda r: r.get('vol_24h_m', 0),
            "ATR%(1h)": lambda r: r.get('atr_pct', 0),
            "OI%(1h)": lambda r: r.get('oi_change_pct', 0),
            "30m%": lambda r: r.get('chg_30m', 0),
            "L/S": lambda r: r.get('ls_ratio') or 0,
        }
        return key_map.get(self._sort_col, lambda r: 0)

    def _apply_search_filter(self, data_list):
        """검색창에 입력한 문자열로 티커를 필터링. 고정(pin)된 코인은 검색어와
        안 맞아도 항상 목록에 남는다 — 필터 때문에 안 보이면 안 되니까."""
        try:
            text = self.search_entry.get().strip().upper()
        except Exception:
            text = ""
        if not text:
            return data_list
        return [r for r in data_list
                if text in r['ticker'].upper() or r['ticker'] in self.pinned_tickers]

    def _on_search_change(self, event=None):
        # 입력할 때마다 마지막으로 받은 데이터로 즉시 다시 그린다 (다음 서버 갱신을 안 기다림)
        if self._last_render_data:
            self._render_table(self._last_render_data)

    def _clear_search(self, event=None):
        self.search_entry.delete(0, tk.END)
        self._on_search_change()

    # 조건필터에서 고를 수 있는 항목들 (표시이름 -> row 딕셔너리 키)
    CUSTOM_FILTER_FIELDS = [
        ("(선택안함)", None),
        ("롱스코어", "long_score"), ("숏스코어", "short_score"),
        ("매집", "prepump_score"), ("분산", "preshort_score"),
        ("RSI", "rsi"), ("RSI Delta", "rsi_delta"), ("VolZ", "vol_z"), ("BB%", "bb_percent"),
        ("CVD Delta", "cvd_diff"), ("ATR%", "atr_pct"), ("OI변화%", "oi_change_pct"),
        ("30분변동%", "chg_30m"), ("L/S비율", "ls_ratio"), ("Extension%", "extension_pct"),
        ("Funding%", "funding"), ("24h거래대금(M)", "vol_24h_m"),
        ("ema_l", "ema_l"), ("ema_s", "ema_s"), ("pp_l", "pp_l"), ("pp_s", "pp_s"),
        ("cvd_l", "cvd_l"), ("cvd_s", "cvd_s"), ("oi_l", "oi_l"), ("oi_s", "oi_s"),
        ("m30_l", "m30_l"), ("m30_s", "m30_s"), ("volz_sc", "volz_sc"), ("liquidity_sc", "liquidity_sc"),
    ]
    CUSTOM_FILTER_FIELD_MAP = dict(CUSTOM_FILTER_FIELDS)

    def _row_passes_custom_filters(self, row):
        """조건필터 3줄 중 값이 채워진 것만 전부 AND로 통과해야 True. 하나도 안 채워져 있으면
        항상 True(필터 없음과 동일)."""
        for rule in self.custom_filter_rules:
            if not rule:
                continue
            field, cmp_op, value = rule['field'], rule['cmp'], rule['value']
            row_val = row.get(field)
            if row_val is None:
                return False  # 값 자체가 없는 코인(N/A)은 조건 비교 불가 → 탈락
            try:
                row_val = float(row_val)
            except (TypeError, ValueError):
                return False
            if cmp_op == '>' and not (row_val > value):
                return False
            elif cmp_op == '=' and not (abs(row_val - value) < 1e-9):
                return False
            elif cmp_op == '<' and not (row_val < value):
                return False
            elif cmp_op == '구간':
                lo, hi = (value, rule.get('value2', value))
                if lo > hi:
                    lo, hi = hi, lo
                if not (lo <= row_val <= hi):
                    return False
        return True

    def _custom_filter_rule_text(self, rule):
        name = next((n for n, k in self.CUSTOM_FILTER_FIELDS if k == rule['field']), rule['field'])
        if rule['cmp'] == '구간':
            return f"{name} 구간 {rule['value']}~{rule.get('value2', rule['value'])}"
        return f"{name} {rule['cmp']} {rule['value']}"

    def _apply_custom_filter(self, data_list):
        if not any(self.custom_filter_rules):
            return data_list
        return [r for r in data_list
                if self._row_passes_custom_filters(r) or r['ticker'] in self.pinned_tickers]

    def show_custom_filter_dialog(self):
        """리셋 버튼 + (조건/부등호/값) 3줄 + 오른쪽에 현재 필터 상태/통과 현황 패널."""
        if getattr(self, 'custom_filter_win', None) and self.custom_filter_win.winfo_exists():
            self.custom_filter_win.lift()
            return
        win = tk.Toplevel(self.root)
        self.custom_filter_win = win
        win.title("조건필터")
        sw = self.root.winfo_screenwidth()
        win.geometry(f"{sw}x{360}")

        top_bar = tk.Frame(win)
        top_bar.pack(fill="x", padx=6, pady=6)
        tk.Label(top_bar, text="조건필터 (최대 3개, 전부 만족해야 통과 / AND)",
                 font=("Arial", self.FONT_SMALL, "bold")).pack(side="left")
        btn_reset = tk.Label(top_bar, text="리셋", bg="#cc4444", fg="white", relief="raised", bd=1,
                              cursor="hand2", padx=8, pady=2, font=("Arial", self.FONT_SMALL, "bold"))
        btn_reset.pack(side="right")

        # 본문을 좌(입력 3줄) / 우(상태 패널)로 나눈다 — "옆에 창으로 상태 보여달라"는 요청 반영
        body = tk.Frame(win)
        body.pack(fill="both", expand=True, padx=6, pady=4)
        left = tk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        right = tk.Frame(body, bd=1, relief="sunken", bg="#f5f5f5")
        right.pack(side="right", fill="both", padx=(8, 0))

        tk.Label(right, text="현재 필터 상태", font=("Arial", self.FONT_SMALL, "bold"),
                 bg="#f5f5f5").pack(anchor="w", padx=6, pady=(6, 2))
        status_label = tk.Label(right, text="", font=("Arial", self.FONT_SMALL), bg="#f5f5f5",
                                 justify="left", anchor="nw", wraplength=int(sw * 0.42))
        status_label.pack(anchor="nw", padx=6, pady=2, fill="both", expand=True)

        field_names = [name for name, _ in self.CUSTOM_FILTER_FIELDS]
        row_widgets = []  # [(field_var, cmp_var, value_entry, value2_entry, value2_row), ...]

        def make_row(i):
            row = tk.Frame(left)
            row.pack(fill="x", pady=4)
            cur = self.custom_filter_rules[i]
            cur_field_name = "(선택안함)"
            if cur:
                for name, key in self.CUSTOM_FILTER_FIELDS:
                    if key == cur['field']:
                        cur_field_name = name
                        break

            field_var = tk.StringVar(value=cur_field_name)
            field_menu = tk.OptionMenu(row, field_var, *field_names)
            field_menu.config(width=12, font=("Arial", self.FONT_SMALL))
            field_menu.pack(side="left", padx=(0, 4))

            cmp_var = tk.StringVar(value=(cur['cmp'] if cur else '>'))
            cmp_menu = tk.OptionMenu(row, cmp_var, '>', '=', '<', '구간')
            cmp_menu.config(width=3, font=("Arial", self.FONT_SMALL))
            cmp_menu.pack(side="left", padx=(0, 4))

            value_entry = tk.Entry(row, width=8, font=("Arial", self.FONT_SMALL))
            if cur:
                value_entry.insert(0, str(cur['value']))
            value_entry.pack(side="left")

            tilde_label = tk.Label(row, text="~", font=("Arial", self.FONT_SMALL))
            value2_entry = tk.Entry(row, width=8, font=("Arial", self.FONT_SMALL))
            if cur and cur['cmp'] == '구간':
                value2_entry.insert(0, str(cur.get('value2', '')))

            def update_value2_visibility(*_args):
                if cmp_var.get() == '구간':
                    tilde_label.pack(side="left", padx=(2, 2))
                    value2_entry.pack(side="left")
                else:
                    tilde_label.pack_forget()
                    value2_entry.pack_forget()

            cmp_var.trace_add('write', update_value2_visibility)
            update_value2_visibility()

            row_widgets.append((field_var, cmp_var, value_entry, value2_entry))

        for i in range(3):
            make_row(i)

        def build_rules_or_none():
            new_rules = []
            for field_var, cmp_var, value_entry, value2_entry in row_widgets:
                field_key = self.CUSTOM_FILTER_FIELD_MAP.get(field_var.get())
                val_txt = value_entry.get().strip()
                if not field_key or not val_txt:
                    new_rules.append(None)
                    continue
                try:
                    val = float(val_txt)
                except ValueError:
                    return None, f"'{val_txt}'은(는) 숫자가 아닙니다."
                rule = {'field': field_key, 'cmp': cmp_var.get(), 'value': val}
                if cmp_var.get() == '구간':
                    val2_txt = value2_entry.get().strip()
                    if not val2_txt:
                        return None, "구간을 선택했으면 두 번째 값(~까지)도 입력하세요."
                    try:
                        rule['value2'] = float(val2_txt)
                    except ValueError:
                        return None, f"'{val2_txt}'은(는) 숫자가 아닙니다."
                new_rules.append(rule)
            return new_rules, None

        def refresh_status_panel():
            active = [r for r in self.custom_filter_rules if r]
            lines = []
            if not active:
                lines.append("적용된 조건 없음 (전체 표시)")
            else:
                for r in active:
                    lines.append(f"• {self._custom_filter_rule_text(r)}")
            data = self._last_render_data or []
            total = len(data)
            passed = sum(1 for row in data if self._row_passes_custom_filters(row))
            lines.append("")
            lines.append(f"통과: {passed} / 전체 {total}개 코인")
            status_label.config(text="\n".join(lines))

        def apply_rules():
            new_rules, err = build_rules_or_none()
            if err:
                messagebox.showwarning("입력 오류", err)
                return
            self.custom_filter_rules = new_rules
            if self._last_render_data:
                self._render_table(self._last_render_data)
            refresh_status_panel()

        def reset_rules():
            self.custom_filter_rules = [None, None, None]
            win.destroy()
            self.custom_filter_win = None
            self.show_custom_filter_dialog()
            if self._last_render_data:
                self._render_table(self._last_render_data)

        btn_reset.bind("<ButtonRelease-1>", lambda e: reset_rules())

        btn_apply = tk.Label(win, text="적용", bg="#2f9e44", fg="white", relief="raised", bd=1,
                              cursor="hand2", padx=8, pady=4, font=("Arial", self.FONT_SMALL, "bold"))
        btn_apply.pack(pady=10)
        btn_apply.bind("<ButtonRelease-1>", lambda e: apply_rules())

        refresh_status_panel()

    def show_change_path_dialog(self):
        """데이터 저장 경로를 사용자가 직접 지정할 수 있는 창. path_config.txt에 써서
        trading_server.py/data_collector.py/simulation_cli.py가 재시작 시 같이 따라가게 한다.
        (같은 폴더에 이 스크립트들을 두고 쓰는 걸 전제로 함)"""
        if getattr(self, 'change_path_win', None) and self.change_path_win.winfo_exists():
            self.change_path_win.lift()
            return
        win = tk.Toplevel(self.root)
        self.change_path_win = win
        win.title("데이터 저장 경로 변경")
        sw = self.root.winfo_screenwidth()
        win.geometry(f"{sw}x220")

        tk.Label(win, text="현재 저장 경로:", font=("Arial", self.FONT_SMALL, "bold")).pack(
            anchor="w", padx=10, pady=(10, 0))
        tk.Label(win, text=SCRIPT_DIR, font=("Arial", self.FONT_SMALL), fg="#555555",
                 wraplength=int(sw - 20), justify="left").pack(anchor="w", padx=10)

        tk.Label(win, text="새 경로 (예: D:\\TradingData):", font=("Arial", self.FONT_SMALL, "bold")).pack(
            anchor="w", padx=10, pady=(14, 0))
        path_entry = tk.Entry(win, font=("Arial", self.FONT_SMALL))
        path_entry.insert(0, SCRIPT_DIR)
        path_entry.pack(fill="x", padx=10, pady=4)

        def browse_folder():
            try:
                from tkinter import filedialog
                chosen = filedialog.askdirectory(initialdir=SCRIPT_DIR)
                if chosen:
                    path_entry.delete(0, tk.END)
                    path_entry.insert(0, chosen)
            except Exception:
                messagebox.showinfo("안내", "이 환경에서는 폴더 선택창을 지원하지 않습니다.\n"
                                     "위 입력칸에 경로를 직접 입력해주세요.")

        btn_browse = tk.Label(win, text="폴더 선택...", bg="#dddddd", fg="black", relief="raised",
                               bd=1, cursor="hand2", padx=6, pady=3, font=("Arial", self.FONT_SMALL))
        btn_browse.pack(anchor="w", padx=10, pady=(0, 8))
        btn_browse.bind("<ButtonRelease-1>", lambda e: browse_folder())

        def save_path():
            new_path = path_entry.get().strip()
            if not new_path:
                messagebox.showwarning("입력 오류", "경로를 입력하세요.")
                return
            try:
                os.makedirs(new_path, exist_ok=True)
                test_file = os.path.join(new_path, ".write_test")
                with open(test_file, "w") as f:
                    f.write("ok")
                os.remove(test_file)
            except Exception as e:
                messagebox.showerror("오류", f"이 경로에 쓸 수 없습니다:\n{e}")
                return
            try:
                with open(_PATH_CONFIG_FILE, "w", encoding="utf-8") as f:
                    f.write(new_path)
            except Exception as e:
                messagebox.showerror("오류", f"설정 파일 저장 실패:\n{e}")
                return
            messagebox.showinfo(
                "저장 완료",
                f"새 경로가 저장됐습니다:\n{new_path}\n\n"
                "⚠️ 지금 실행 중인 trading_client.py와 trading_server.py를 모두 종료했다가 "
                "다시 켜야 반영됩니다. (data_collector.py/simulation_cli.py를 쓰고 있다면 그것들도 "
                "같은 폴더에 있어야 같이 따라갑니다)"
            )
            win.destroy()
            self.change_path_win = None

        btn_save = tk.Label(win, text="저장", bg="#2f9e44", fg="white", relief="raised", bd=1,
                             cursor="hand2", padx=8, pady=4, font=("Arial", self.FONT_SMALL, "bold"))
        btn_save.pack(pady=6)
        btn_save.bind("<ButtonRelease-1>", lambda e: save_path())

    def _on_filter_toggle(self):
        # 다음 서버 갱신을 기다리지 않고, 마지막으로 받은 데이터로 바로 다시 그린다.
        if self._last_render_data:
            self._render_table(self._last_render_data)

    def _apply_sort(self, data_list):
        pinned = [r for r in data_list if r['ticker'] in self.pinned_tickers]
        non_pinned = [r for r in data_list if r['ticker'] not in self.pinned_tickers]
        cut = current_min_score
        pp_cut = pp_current_min_score
        filt_on = self.filter_enabled.get()

        def long_ok(r):
            return r['long_score'] >= cut and (not filt_on or r.get('filters_ok_long', True))

        def short_ok(r):
            return r['short_score'] >= cut and (not filt_on or r.get('filters_ok_short', True))

        colored = [r for r in non_pinned if long_ok(r) or short_ok(r)
                   or r.get('prepump_score', 0) >= pp_cut or r.get('preshort_score', 0) >= pp_cut]
        colored_tickers = {r['ticker'] for r in colored}
        plain = [r for r in non_pinned if r['ticker'] not in colored_tickers]
        keyfn = self._sort_key_fn()
        reverse = True if self._sort_col is None else (not self._sort_reverse[self._sort_col])
        return (sorted(pinned, key=keyfn, reverse=reverse)
                + sorted(colored, key=keyfn, reverse=reverse)
                + sorted(plain, key=keyfn, reverse=reverse))

    def _cfg(self, widget, **kwargs):
        """값이 실제로 바뀐 속성만 config 호출 → 불필요한 재도색(깜빡임) 방지."""
        cache = getattr(widget, '_cfg_cache', None)
        if cache is None:
            cache = {}
            widget._cfg_cache = cache
        changed = {k: v for k, v in kwargs.items() if cache.get(k) != v}
        if changed:
            widget.config(**changed)
            cache.update(changed)

    def _render_table(self, data_list):
        try:
            self._last_render_data = data_list
            self._update_score_history(data_list)
            yview_top = self.card_canvas.yview()[0]
            data_list = self._apply_search_filter(data_list)
            data_list = self._apply_custom_filter(data_list)
            data_list = self._apply_sort(data_list)
            seen = set()
            new_order = []
            for row in data_list:
                ticker = row['ticker']
                seen.add(ticker)
                new_order.append(ticker)
                price = row['price']
                price_usd = row.get('price_usd')

                krw_str = f"{price:,.0f}원" if price >= 1000 else f"{price:,.2f}원"
                if price_usd is not None and price_usd > 0:
                    usd_str = f"USD {price_usd:,.4f}" if price_usd < 1 else f"USD {price_usd:,.2f}"
                else:
                    usd_str = "N/A"

                cvd_val = row.get('cvd', 0)
                cvd_str = f"{cvd_val:+,.0f}" if abs(cvd_val) >= 100 else f"{cvd_val:+.2f}"
                ls, ss = row['long_score'], row['short_score']
                pp, ps = row.get('prepump_score', 0), row.get('preshort_score', 0)
                cut = current_min_score
                wcut = watch_current_min_score
                pp_cut = pp_current_min_score
                filt_on = self.filter_enabled.get()
                long_pass = ls >= cut and (not filt_on or row.get('filters_ok_long', True))
                short_pass = ss >= cut and (not filt_on or row.get('filters_ok_short', True))
                # 컷은 넘었는데 필터에 막힌 경우 — 진짜 "관심(65점대)"과 색을 분리해서
                # 65점짜리 애매한 코인과 76점인데 필터만 막힌 코인이 똑같은 노랑으로
                # 뭉뚱그려지지 않게 한다.
                filt_blocked = filt_on and (
                    (ls >= cut and not row.get('filters_ok_long', True)) or
                    (ss >= cut and not row.get('filters_ok_short', True))
                )
                if long_pass and ls >= ss:
                    bg = "#d8f5d8"
                elif short_pass:
                    bg = "#fbdada"
                elif pp >= pp_cut and pp >= ps:
                    bg = "#dbe9fb"   # 파랑 계열: 아직 안 터진 매집 구간(prepump) 대기
                elif ps >= pp_cut:
                    bg = "#f1ddf5"   # 보라 계열: 고점 분산(preshort) 대기
                elif filt_blocked:
                    bg = "#fbdfc0"   # 살구색: 컷은 넘었지만 v5 필터에 막힘 (관심과 별개 색)
                elif ls >= wcut or ss >= wcut:
                    bg = "#fdf6d8"   # 연노랑: 필터와 무관하게 진짜 "관심" 구간(65점대)
                else:
                    bg = "white"
                display_ticker = f"[{ticker}]" if ticker in self.pinned_tickers else ticker
                # 컷은 넘었지만 v5 하드필터에 걸려서(체크박스 켜짐 기준) 색칠 안 된
                # 경우를 표시 — 필터를 껐을 때와 비교하며 필터 효과를 직접 확인할 수 있다.
                filt_warn = ""
                if ls >= cut and not row.get('filters_ok_long', True):
                    filt_warn += " ⚠️롱필터"
                if ss >= cut and not row.get('filters_ok_short', True):
                    filt_warn += " ⚠️숏필터"
                line1 = f"{display_ticker}  {usd_str} ({krw_str})  롱{ls} 숏{ss}  매집{pp} 분산{ps}{filt_warn}"
                lsr = row.get('ls_ratio')
                ls_str = f"{lsr:.2f}" if lsr is not None else "N/A"
                line2 = (f"RSI {row['rsi']}({row['rsi_delta']:+}) BB {row['bb_percent']:.0f}% "
                         f"VolZ {row.get('vol_z', 0):+.1f} "
                         f"CVD {cvd_str} 30m {row.get('chg_30m', 0):+.2f}% "
                         f"ATR {row.get('atr_pct', 0):.2f}% OI {row.get('oi_change_pct', 0):+.2f}% "
                         f"L/S {ls_str} Fund {row.get('funding', 0):+.3f}% Vol {row.get('vol_24h_m', 0):,}M")
                card = self.card_widgets.get(ticker)
                is_new = card is None
                if is_new:
                    card = tk.Frame(self.card_inner, bd=1, relief="solid")
                    lbl1 = tk.Label(card, font=("Arial", self.ui_font_base, "bold"), anchor="w",
                                    justify="left", wraplength=self.card_wraplength)
                    lbl2 = tk.Label(card, font=("Arial", max(self.ui_font_base - 1, 4)), anchor="w",
                                    justify="left", fg="#444444", wraplength=self.card_wraplength)
                    lbl1.pack(fill="x", padx=4, pady=(2, 0))
                    lbl2.pack(fill="x", padx=4, pady=(0, 2))
                    card._lbl1 = lbl1
                    card._lbl2 = lbl2
                    for wd in (card, lbl1, lbl2):
                        wd.bind("<ButtonPress-1>", lambda e, t=ticker: self._card_press(e, t), add="+")
                        wd.bind("<B1-Motion>", self._card_motion, add="+")
                        wd.bind("<ButtonRelease-1>", lambda e, t=ticker: self._card_release(e, t), add="+")
                    self.card_widgets[ticker] = card
                self._cfg(card._lbl1, text=line1, bg=bg)
                self._cfg(card._lbl2, text=line2, bg=bg)
                self._cfg(card, bg=bg)
                if is_new:
                    card.pack(fill="x", padx=2, pady=1)

            for ticker in list(self.card_widgets.keys()):
                if ticker not in seen:
                    self.card_widgets[ticker].destroy()
                    del self.card_widgets[ticker]

            # 순서 재배치는 실제로 순서가 바뀌었고, 마지막 재배치 후 일정 시간(또는 정렬버튼 클릭)이
            # 지났을 때만 수행한다. 매 폴링(0.7초)마다 전부 다시 pack하면 화면이 계속 깜빡인다.
            now = time.time()
            order_changed = new_order != self._last_table_order
            due = (now - self._last_reorder_time) >= self._reorder_interval
            if order_changed and (self._force_resort or due or not self._last_table_order):
                for ticker in new_order:
                    self.card_widgets[ticker].pack_forget()
                for ticker in new_order:
                    self.card_widgets[ticker].pack(fill="x", padx=2, pady=1)
                self._last_table_order = new_order
                self._last_reorder_time = now
                self._force_resort = False

            self.card_inner.update_idletasks()
            self.card_canvas.configure(scrollregion=self.card_canvas.bbox("all"))
            self.card_canvas.yview_moveto(yview_top)
        except Exception as e:
            print(f"렌더링 오류: {e}")

    def set_sort(self, col):
        if col is None:
            self._sort_col = None
        else:
            if self._sort_col == col:
                self._sort_reverse[col] = not self._sort_reverse[col]
            self._sort_col = col
        for key, btn in self._sort_btn_widgets.items():
            base_label = self._sort_btn_labels[key]
            if key == self._sort_col:
                arrow = "" if key is None else (" ▲" if self._sort_reverse.get(key, False) else " ▼")
                btn.config(text=base_label + arrow, bg="#4a90d9", fg="white")
            else:
                btn.config(text=base_label, bg="#eeeeee", fg="black")
        self._force_resort = True
        if self._last_render_data:
            self._render_table(self._last_render_data)

    def _card_press(self, event, ticker):
        self._card_drag_state["y"] = event.y_root
        self._card_drag_state["dragged"] = False
        self._card_drag_state["view_top"] = self.card_canvas.yview()[0]

    def _card_motion(self, event):
        dy = event.y_root - self._card_drag_state["y"]
        if abs(dy) > 4:
            self._card_drag_state["dragged"] = True
        h = max(self.card_canvas.winfo_height(), 1)
        ylo, yhi = self.card_canvas.yview()
        yspan = max(yhi - ylo, 0.0001)
        new_y = self._card_drag_state["view_top"] - (dy / h) * yspan
        self.card_canvas.yview_moveto(max(0.0, min(1.0, new_y)))

    def _card_release(self, event, ticker):
        if self._card_drag_state["dragged"]:
            return
        now = time.time() * 1000
        if self._tap_state["last_ticker"] == ticker and (now - self._tap_state["last_time"]) < self.DOUBLE_TAP_MS:
            self.toggle_pin(ticker)
            self._tap_state["last_time"] = 0
            self._tap_state["last_ticker"] = None
        else:
            self._tap_state["last_time"] = now
            self._tap_state["last_ticker"] = ticker
            self.ticker_entry.delete(0, tk.END)
            self.ticker_entry.insert(0, ticker)

    def toggle_pin(self, ticker):
        if ticker in self.pinned_tickers:
            self.pinned_tickers.discard(ticker)
        else:
            self.pinned_tickers.add(ticker)
        if self._last_render_data:
            self._render_table(self._last_render_data)

    def _send_and_wait(self, action, ticker='', amount=0, leverage=0, position_type='', label=''):
        try:
            cmd_id = send_command(action, ticker, amount, leverage, position_type)
        except Exception as e:
            messagebox.showerror("오류", f"명령 전송 실패: {e}")
            return
        self._wait_result(cmd_id, label or action, tries=16)

    def _wait_result(self, cmd_id, label, tries):
        res = find_result(cmd_id)
        if res:
            status, msg = res
            if status == 'ok':
                messagebox.showinfo(f"{label} 완료", msg)
            else:
                messagebox.showwarning(f"{label} 실패", msg)
            return
        if tries <= 0:
            messagebox.showwarning("응답 없음", "서버 응답이 없습니다.\ntrading_server.py 실행 여부를 확인하세요.")
            return
        self.root.after(500, lambda: self._wait_result(cmd_id, label, tries - 1))

    def add_funds(self):
        amount = simpledialog.askfloat(
            "거래소 충전", f"거래소로 충전할 금액 ($):\n(외부통장 → 거래소로 이체됩니다. 현재 외부통장 ${bank_balance:,.2f})",
            minvalue=0
        )
        if amount and amount > 0:
            self._send_and_wait('charge', amount=amount, label="거래소 충전")

    def withdraw_funds(self):
        cash = self._account[0] if self._account else 0
        amount = simpledialog.askfloat(
            "거래소 출금", f"거래소에서 출금할 금액 ($):\n(포지션/그 손익은 안 건드리고, 여유 현금 ${cash:,.2f}에서만 빠져 외부통장으로 들어갑니다)",
            minvalue=0
        )
        if amount and amount > 0:
            self._send_and_wait('withdraw', amount=amount, label="거래소 출금")

    def bank_deposit(self):
        amount = simpledialog.askfloat("외부통장 입금", "외부통장에 입금할 금액 ($):\n(월급 등 새로 들어오는 돈)", minvalue=0)
        if amount and amount > 0:
            self._send_and_wait('bank_deposit', amount=amount, label="외부통장 입금")

    def bank_withdraw(self):
        amount = simpledialog.askfloat(
            "외부통장 출금", f"외부통장에서 출금할 금액 ($):\n(실생활 지출 등 — 그냥 시스템 밖으로 사라집니다. 현재 외부통장 ${bank_balance:,.2f})",
            minvalue=0
        )
        if amount and amount > 0:
            self._send_and_wait('bank_withdraw', amount=amount, label="외부통장 출금")

    def set_interval(self, interval):
        # 즉시 버튼 색을 눌린 것처럼 바꿔 반응성을 주고(서버 응답은 비동기),
        # 실제 확정 색상은 다음 poll_files에서 서버가 돌려준 interval로 재반영된다.
        self._highlight_interval_buttons(interval)
        self._send_and_wait('set_interval', ticker=interval, label=f"기준봉 {interval} 전환")

    def _highlight_interval_buttons(self, active_interval):
        for iv, btn in self._tf_btn_widgets.items():
            if iv == active_interval:
                self._cfg(btn, bg="#1a7abf", fg="white")
            else:
                self._cfg(btn, bg="#eeeeee", fg="black")
        self._last_interval_shown = active_interval

    def reset_balance(self):
        if not messagebox.askyesno(
            "잔고 리셋",
            "거래소 잔고와 외부통장(누적 입출금 포함)을 모두 0원으로 리셋하시겠습니까?\n(보유 포지션이 있으면 리셋되지 않습니다)"
        ):
            return
        self._send_and_wait('reset', label="리셋")

    def toggle_margin_mode(self):
        new_mode = 'isolated' if current_margin_mode == 'cross' else 'cross'
        label = "크로스 마진" if new_mode == 'cross' else "격리 마진"
        if not messagebox.askyesno(
            "마진 모드 전환",
            f"{label} 모드로 전환하시겠습니까?\n"
            + ("크로스: 포지션 손실이 -100%를 넘어도 계좌 잔고가 버티면 청산 안 함"
               if new_mode == 'cross' else
               "격리: 포지션별 배정 증거금의 90% 손실 시 그 포지션만 즉시 강제청산")
        ):
            return
        self._highlight_margin_mode(new_mode)
        self._send_and_wait('set_margin_mode', ticker=new_mode, label=f"{label} 전환")

    def _highlight_margin_mode(self, mode):
        if mode == 'cross':
            self._cfg(self.btn_margin_mode, text="크로스", bg="#1a7abf", fg="white")
        else:
            self._cfg(self.btn_margin_mode, text="격리", bg="#996600", fg="white")
        self._last_margin_mode_shown = mode

    def open_position(self, position_type):
        ticker = self.ticker_entry.get().strip().upper()
        if not ticker:
            messagebox.showwarning("경고", "티커를 입력하세요.")
            return
        try:
            amount_won = round(float(self.amount_entry.get() or 0), 2)  # 달러
            leverage = int(self.leverage_entry.get() or DEFAULT_LEVERAGE)
        except Exception:
            messagebox.showwarning("경고", "금액/배율을 올바르게 입력하세요.")
            return
        if amount_won <= 0:
            messagebox.showwarning("경고", "금액을 올바르게 입력하세요.")
            return
        direction = "롱" if position_type == "long" else "숏"
        self._send_and_wait('open', ticker=ticker, amount=amount_won, leverage=leverage,
                            position_type=position_type, label=f"{direction} 진입")

    def close_position(self):
        ticker = self.ticker_entry.get().strip().upper()
        if not ticker:
            messagebox.showwarning("경고", "티커를 입력하세요.")
            return
        self._send_and_wait('close', ticker=ticker, label="청산")

    def close_ticker(self, ticker):
        if not messagebox.askyesno("청산 확인", f"{ticker} 포지션을 청산하시겠습니까?"):
            return
        self._send_and_wait('close', ticker=ticker, label="청산")

    def _show_leverage_info(self, ticker):
        _, _, pos_list = self._account
        for p in pos_list:
            if p['ticker'] == ticker:
                messagebox.showinfo("Leverage", f"{ticker} 현재 배율: {p['leverage']}x\n(배율 변경은 신규 진입 시 설정하세요.)")
                return

    def export_indicator_report_pdf(self):
        """
        버튼 클릭 시 PDF 리포트 생성:
          1페이지 = 롱스코어 상위 10개 코인(가로) × 지표(세로) 표, 5pt
          2페이지 = 숏스코어 상위 10개 코인(가로) × 지표(세로) 표, 5pt
          3페이지 이후 = 지표 설명(show_help()와 동일 내용), 10pt
        안드로이드 다운로드 폴더에 저장.
        """
        try:
            from fpdf import FPDF
        except ImportError:
            messagebox.showerror("오류", "fpdf2가 설치되어 있지 않습니다.\n"
                                  "Termux에서 설치: pip install fpdf2 --break-system-packages")
            return
        try:
            from fpdf.enums import XPos, YPos
        except ImportError:
            # 구버전 fpdf2는 fpdf.enums가 없다 — XPos.LMARGIN/YPos.NEXT를 그냥 문자열로
            # 흉내 낸 자리표시자를 써서, 아래 cell(new_x=..., new_y=...) 호출이 죽지 않게 한다.
            class _XPosShim:
                LMARGIN = "LMARGIN"
            class _YPosShim:
                NEXT = "NEXT"
            XPos, YPos = _XPosShim, _YPosShim

        data = list(self._last_render_data or [])
        if not data:
            messagebox.showwarning("경고", "아직 표시할 마켓 데이터가 없습니다.")
            return

        try:
            self._build_and_save_pdf(FPDF, XPos, YPos, data)
        except Exception as e:
            import traceback
            traceback.print_exc()
            messagebox.showerror("PDF 생성 실패", f"{type(e).__name__}: {e}")

    def _build_and_save_pdf(self, FPDF, XPos, YPos, data):
        # PDF 내용을 전부 영어로 바꾼 뒤로는 fpdf2 기본 내장 폰트(Helvetica)만으로 충분해서,
        # 한글 TTF 폰트를 따로 찾아 설치할 필요가 없어졌다.

        top_long = sorted(data, key=lambda r: r.get('long_score', 0), reverse=True)[:10]
        top_short = sorted(data, key=lambda r: r.get('short_score', 0), reverse=True)[:10]

        def fmt_price(r):
            v = r.get('price_usd')
            if v is None:
                return "N/A"
            return f"{v:,.2f}" if v >= 1 else f"{v:,.4f}"

        # (표시 이름, row -> 문자열 값 변환 함수, 설명). 세로 인덱스(행)로 쓰인다.
        # 51개 컬럼 중 기본정보(timestamp/ticker)·원화가격(price_krw)·최종점수
        # (long/short/prepump/preshort_score)만 빼고 나머지 세부항목을 전부 넣는다.
        # 3번째 값(설명)은 3페이지 이후 "지표 설명"에 그대로 쓰여서, 표에 실제로
        # 나오는 44개 행과 설명이 1:1로 절대 안 어긋나게 한다.
        indicator_rows = [
            ("Price(USD)", fmt_price, "Binance futures last traded price (USD)."),
            # --- raw indicators ---
            ("RSI", lambda r: f"{r.get('rsi', 0):.1f}",
             "Relative Strength Index (0-100). 70+ overbought, 30- oversold."),
            ("RSI Delta", lambda r: f"{r.get('rsi_delta', 0):+.1f}",
             "RSI change vs. 5 candles ago. A large value means a sharp move just happened."),
            ("VolZ", lambda r: f"{r.get('vol_z', 0):+.1f}",
             "Volume Z-score: how much current volume deviates from the last 20-candle average. "
             "Higher means volume has 'already spiked' (chasing risk)."),
            ("BB%", lambda r: f"{r.get('bb_percent', 0):.0f}",
             "Price position within Bollinger Bands (0-100%). 0=lower band, 100=upper band."),
            ("CVD Delta", lambda r: f"{r.get('cvd_diff', 0):+.2f}",
             "Cumulative Volume Delta change over the last 2 candles. + means buyers dominant, - means sellers."),
            ("ATR%", lambda r: f"{r.get('atr_pct', 0):.2f}",
             "Average True Range on the 1h candle (% of price). Used for the volatility/liquidity filter."),
            ("OI Delta%", lambda r: f"{r.get('oi_change_pct', 0):+.2f}",
             "Open Interest 1h change rate. Combined with price direction to judge 'genuine new inflow'."),
            ("30m Delta%", lambda r: f"{r.get('chg_30m', 0):+.2f}",
             "Price change over the last 30 minutes. A large move already (overheated) signals chase risk."),
            ("L/S", lambda r: (f"{r.get('ls_ratio'):.2f}" if r.get('ls_ratio') is not None else "N/A"),
             "Binance-wide account long/short ratio. Used in the 'position contrarian' component of the "
             "accumulation/distribution score."),
            ("Ext%", lambda r: f"{r.get('extension_pct', 0):+.2f}",
             "How far price has already moved over the last 10 candles (%). If large, treated as a late-stage "
             "'EMA alignment already extended' setup and penalized."),
            ("Funding%", lambda r: f"{r.get('funding', 0):+.3f}",
             "Funding rate (%). + means longs are crowded, - means shorts are crowded. Display only, not scored."),
            ("24h Volume(M)", lambda r: f"{r.get('vol_24h_m', 0):,}",
             "24h cumulative trading value (millions KRW). Used as the liquidity filter threshold."),
            ("EMA20", lambda r: (f"{r.get('ema20'):,.4f}" if r.get('ema20') is not None else "N/A"),
             "20-period exponential moving average (short-term trend line)."),
            ("EMA60", lambda r: (f"{r.get('ema60'):,.4f}" if r.get('ema60') is not None else "N/A"),
             "60-period exponential moving average (medium-term trend line)."),
            ("EMA120", lambda r: (f"{r.get('ema120'):,.4f}" if r.get('ema120') is not None else "N/A"),
             "120-period exponential moving average (long-term trend line). EMA20>60>120 aligned = uptrend."),
            # --- v6 sub-components (make up the 85-point long/short score) ---
            ("ema_l", lambda r: str(r.get('ema_l', 0)),
             "EMA+price-position long score (max 30). Early alignment=30 / not-yet-extended alignment=25 / "
             "already overheated chase=12 / pullback zone=18 / reverse alignment=0."),
            ("ema_s", lambda r: str(r.get('ema_s', 0)), "EMA+price-position short score (max 30). Mirrors ema_l."),
            ("pp_l", lambda r: str(r.get('pp_l', 0)),
             "RSI+BB% filter long score (max 10). Penalized if RSI_delta is explosive (prevents chasing a spike)."),
            ("pp_s", lambda r: str(r.get('pp_s', 0)), "RSI+BB% filter short score (max 10). Mirrors pp_l."),
            ("cvd_l", lambda r: str(r.get('cvd_l', 0)),
             "CVD long score (max 10). Reflects both direction match and strength (increase relative to volume)."),
            ("cvd_s", lambda r: str(r.get('cvd_s', 0)), "CVD short score (max 10). Mirrors cvd_l."),
            ("oi_l", lambda r: str(r.get('oi_l', 0)),
             "OI Synergy long score (max 20). Full marks only when price up + OI up together (confirms genuine "
             "new capital inflow)."),
            ("oi_s", lambda r: str(r.get('oi_s', 0)), "OI Synergy short score (max 20). Mirrors oi_l (price down + OI up)."),
            ("m30_l", lambda r: str(r.get('m30_l', 0)),
             "30-minute momentum long score (max 5). Rewards 'not yet moved', zero once it's 'already run'."),
            ("m30_s", lambda r: str(r.get('m30_s', 0)), "30-minute momentum short score (max 5). Mirrors m30_l."),
            ("volz_sc", lambda r: str(r.get('volz_sc', 0)),
             "VolZ score (max 5, shared by long/short). Penalized once volume has already spiked (anti-chase)."),
            ("liquidity_sc", lambda r: str(r.get('liquidity_sc', 0)),
             "ATR/trading-value liquidity filter score (max 5, shared). Penalized if volatility is too dead or too hot."),
            # --- Pre-Pump/Pre-Short sub-components (make up the 100-point accumulation/distribution score) ---
            ("div_l", lambda r: str(r.get('div_l', 0)),
             "Accumulation divergence score (max 35). Detects hidden bullish divergence: price low holds while "
             "RSI/CVD lows rise."),
            ("div_s", lambda r: str(r.get('div_s', 0)), "Distribution divergence score (max 35). Mirrors div_l (based on highs)."),
            ("lsx_l", lambda r: str(r.get('lsx_l', 0)),
             "Position-contrarian long score (max 25). Rewarded more the more retail is crowded short (low L/S) "
             "— short-squeeze potential."),
            ("lsx_s", lambda r: str(r.get('lsx_s', 0)), "Position-contrarian short score (max 25). Mirrors lsx_l (crowded long)."),
            ("bb_comp_sc", lambda r: str(r.get('bb_comp_sc', 0)),
             "Price-stagnation (volatility compression) score (max 20, shared). Rewarded when Bollinger Band "
             "width is narrow relative to recent range."),
            ("stealth_sc", lambda r: str(r.get('stealth_sc', 0)),
             "Stealth-accumulation VolZ score (max 20, shared). Catches the zone where price stays flat but "
             "volume quietly builds."),
            # --- v5 hard filter pass/fail per condition (1=pass, 0=blocked by this condition) ---
            ("filt_ema_oi_l", lambda r: str(r.get('filt_ema_oi_l', 1)),
             "Long filter: EMA aligned AND OI direction matches (both must be nonzero for 1)."),
            ("filt_ema_oi_s", lambda r: str(r.get('filt_ema_oi_s', 1)), "Short filter: short-side version of filt_ema_oi_l."),
            ("filt_cvd_l", lambda r: str(r.get('filt_cvd_l', 1)), "Long filter: CVD direction matches long (cvd_l != 0)."),
            ("filt_cvd_s", lambda r: str(r.get('filt_cvd_s', 1)), "Short filter: CVD direction matches short (cvd_s != 0)."),
            ("filt_volz_ok", lambda r: str(r.get('filt_volz_ok', 1)),
             "Shared filter: VolZ below 2.0 (prevents chasing an already-spiked volume move)."),
            ("filt_m30_l", lambda r: str(r.get('filt_m30_l', 1)), "Long filter: 30-min momentum condition met (m30_l != 0)."),
            ("filt_m30_s", lambda r: str(r.get('filt_m30_s', 1)), "Short filter: 30-min momentum condition met (m30_s != 0)."),
            ("filt_liquidity_ok", lambda r: str(r.get('filt_liquidity_ok', 1)),
             "Shared filter: liquidity filter passed (liquidity_sc != 0)."),
            ("passes_all_l", lambda r: str(r.get('passes_all_l', 1)),
             "Final long verdict: all 5 long filters above passed (1=pass, 0=at least one failed -> excluded "
             "from coloring when the filter toggle is on)."),
            ("passes_all_s", lambda r: str(r.get('passes_all_s', 1)),
             "Final short verdict: all 5 short filters above passed (1=pass, 0=at least one failed -> excluded "
             "from coloring when the filter toggle is on)."),
        ]

        pdf = FPDF(orientation="L", unit="mm", format="A4")
        pdf.set_auto_page_break(False)
        
        def add_table_page(title, rows_data):
            pdf.add_page()
            pdf.set_font("Helvetica", "", 12)
            title_h = 8
            pdf.cell(0, title_h, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font("Helvetica", "", 5)
            n_cols = len(rows_data) + 1  # 지표명 열 + 코인 열들
            page_w = pdf.w - 2 * pdf.l_margin
            col_w = page_w / max(n_cols, 1)
            # 지표(행) 개수가 늘어나도 한 페이지 안에 다 들어가도록 행 높이를 동적으로 계산.
            # 5pt 글자가 읽히는 최소한도(3.0mm)는 지키되, 그 밑으로는 안 내려간다 —
            # 지표가 너무 많아 3.0mm로도 못 채우면 페이지 아래로 넘칠 수 있다(참고용 안전장치).
            n_data_rows = len(indicator_rows) + 1  # +1은 코인 티커 헤더행
            available_h = (pdf.h - pdf.t_margin - pdf.b_margin) - title_h
            row_h = max(3.0, available_h / n_data_rows)

            pdf.cell(col_w, row_h, "Indicator", border=1, align='C')
            for r in rows_data:
                pdf.cell(col_w, row_h, str(r.get('ticker', '')), border=1, align='C')
            pdf.ln(row_h)

            for label, getter, _desc in indicator_rows:
                pdf.cell(col_w, row_h, label, border=1)
                for r in rows_data:
                    try:
                        val = getter(r)
                    except Exception:
                        val = ""
                    pdf.cell(col_w, row_h, str(val), border=1, align='C')
                pdf.ln(row_h)

        add_table_page(f"Top 10 by Long Score  ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})", top_long)
        add_table_page(f"Top 10 by Short Score  ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})", top_short)

        # 3페이지 이후: 표에 나온 44개 지표 항목 설명 (indicator_rows의 3번째 값과 1:1 매칭), 10pt
        # 표 페이지는 한 페이지 안에 다 들어가서 auto_page_break를 꺼놨지만,
        # 설명 텍스트는 길이가 들쭉날쭉해서 fpdf2의 자동 페이지분할에 맡기는 게 안전하다.
        pdf.set_auto_page_break(True, margin=15)
        pdf.add_page()
        pdf.set_font("Helvetica", "", 16)
        pdf.cell(0, 10, "Indicator Descriptions (44 items shown in the tables above)", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 10)
        desc_width = pdf.w - 2 * pdf.l_margin  # w=0 자동계산에 기대지 않고 폭을 직접 지정
        for title, _getter, desc in indicator_rows:
            pdf.set_font("Helvetica", "", 10)
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(desc_width, 6, f"■ {title}")
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(desc_width, 6, desc)
            pdf.ln(2)

        out_dir = "/storage/emulated/0/Download"
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            out_dir = SCRIPT_DIR
        fname = f"indicator_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        out_path = os.path.join(out_dir, fname)
        try:
            pdf.output(out_path)
            messagebox.showinfo("완료", f"PDF 저장 완료:\n{out_path}")
        except Exception as e:
            messagebox.showerror("오류", f"PDF 저장 실패: {e}")


    def _help_entries(self):
        """지표 설명 (title, desc) 목록. show_help() 팝업과 PDF 리포트가 공유해서 쓴다."""
        helps = [
            ("구조", "이 앱은 주문 전용입니다. 실시간 계산(지표/점수)과 계좌(잔고/포지션/강제청산)는 "
                    "trading_server.py 가 담당하고, 이 앱은 서버가 갱신하는 CSV를 읽어 표시하며 "
                    "주문 명령만 보냅니다. 이 앱을 꺼도 포지션은 서버가 계속 감시합니다."),
            ("카드 색깔", "🟢 초록=롱 신호(long_score≥컷) | 🔴 빨강=숏 신호(short_score≥컷) | "
                        "🔵 파랑=매집(prepump_score≥80) | 🟣 보라=분산(preshort_score≥80) | "
                        "🟠 살구=컷은 넘었지만 v5 필터에 막힘(⚠️ 표시 참고) | "
                        "🟡 노랑=관심(65점은 넘었지만 진입 컷 미달, 참고만 할 것) | 흰색=신호 없음. "
                        "롱/숏이 매집/분산보다 우선순위 높음(둘 다 해당하면 롱/숏 색이 뜸)."),
            ("필터 적용 체크박스", "v5 하드필터(EMA/OI 방향 일치, CVD 방향 일치, VolZ<2.0, 30분모멘텀 조건, "
                        "유동성 통과)를 롱/숏 색칠에 적용할지 직접 켜고 끌 수 있다. 서버는 필터로 "
                        "점수를 강제로 0점 처리하지 않고 원점수와 필터 통과여부를 같이 내려주므로, "
                        "체크박스를 껐다 켰다 하면서 필터가 실제로 성능에 도움되는지 바로 비교해볼 수 "
                        "있다. 켜져 있을 때 컷은 넘었지만 필터에 걸려 색이 안 뜬 코인은 카드 첫 줄에 "
                        "⚠️롱필터/⚠️숏필터 표시가 붙는다(체크 여부와 무관하게 항상 표시)."),
            ("거시 필터 (BTC추세/공포탐욕)", "개별 코인 지표만으론 '시장 전체가 한 번에 롤오버하는' 리스크를 "
                        "못 잡아서 추가했다. BTC 자체의 EMA 추세와 alternative.me 공포탐욕지수(0~100, "
                        "하루 1회 갱신)를 화면 상단 오른쪽에 항상 표시한다(위험 상태면 주황색+⚠️). "
                        "롱 진입은 BTC가 완전 하락추세거나 공포탐욕지수≥80(극단적 탐욕, 조정 위험)이면 "
                        "거부되고, 숏 진입은 BTC가 완전 상승추세거나 공포탐욕지수≤20(극단적 공포, 반등 "
                        "위험)이면 거부된다 — 개별 신호가 아무리 좋아도 이 상태면 진입이 막힌다."),
            ("현재가", "바이낸스 선물 최종 체결가(USD) 기준. 괄호 안은 빗썸 원화 실시간 가격. "
                      "바이낸스 미상장 코인은 N/A로 표시. 두 가격 차이는 김치프리미엄."),
            ("RSI", "상대강도지수(0~100). 30 이하 과매도(롱 유리), 70 이상 과매수(숏 유리). "
                    "괄호 안은 5캔들 전 대비 변화량(RSI△, 표시용, v3에서 점수 미반영)."),
            ("BB%", "볼린저밴드 폭 대비 가격 위치. 0%=하단 밴드, 100%=상단 밴드. "
                    "마이너스면 하단 밴드 아래로 이탈(극단적 과매도→롱 유리), "
                    "100% 초과면 상단 이탈(극단적 과매수→숏 유리)."),
            ("VolZ", "거래량 Z-스코어. 최근 20캔들 평균 대비 현재 캔들 거래량이 얼마나 튀는지. "
                     "롱/숏 Score 반영(v3): 2.0 이상→15점, 1.2 이상→10점, 그 외→3점."),
            ("CVD", "누적 볼륨 델타(최근 2시간, OHLCV 추정). +면 매수세 우위, -면 매도세 우위."),
            ("30m%", "최근 30분간 가격 변동률. 롱은 0.5~2.5%가 만점(안정적 양봉), "
                     "4% 초과는 과열(꼬리물림 위험)로 감점. 숏은 대칭(음수 구간)."),
            ("ATR%", "1시간봉 기준 평균 변동폭(현재가 대비 %). v3에서는 등급 점수가 아니라 "
                     "24h거래대금과 묶어 '최소 유동성 필터'로만 쓰인다(통과 시 5점, 미통과 0점). "
                     "전 코인 평균 ATR%로 진입 컷도 자동 조정됨(추세장 70 / 보통 75 / 횡보장 80)."),
            ("OI%", "미체결약정 1시간 변동률. ΔOI≥3%→15점, ≥1%→7점, 그 외 0점(v3, 방향/CVD게이팅 없이 "
                    "OI 자체 증가 강도만 봄)."),
            ("L/S", "바이낸스 전체 계정 롱/숏 비율. 표시용이며 롱/숏 Score엔 미반영(v3에서 제외됨). "
                    "매집/분산(prepump/preshort) 점수의 '포지션 역발상' 25점 항목에서만 쓰인다 — "
                    "롱 쏠림(개미 과열)이면 분산(숏 매집)에 가점, 숏 쏠림이면 매집(롱 매집)에 가점."),
            ("Fund", "펀딩레이트(%). +면 롱 과열, -면 숏 과열. 표시용이며 어떤 점수에도 미반영(v3에서 제외됨)."),
            ("롱/숏 Score", f"85점 만점(v6, 추세추종형): EMA+가격위치 30(정배열 초입=30 / 안 뻗은 정배열=25 / "
                          f"이미 과열 추격=12 / 눌림목=18) + OI Synergy 20(가격·OI 방향 일치) + CVD 10 "
                          f"+ RSI+BB%% 필터 10(RSI_delta 폭발적이면 감점) + VolZ 5 + 30분모멘텀 5 "
                          f"+ ATR/거래대금 유동성필터 5 → /85×100 환산. "
                          f"진입 컷은 시장 상태에 따라 70(추세장)~80(횡보장) 자동 조정(현재 {current_min_score}점, "
                          f"참고용 관심선 {watch_current_min_score}점). 필터 체크박스가 켜져 있으면 EMA/OI 방향일치, "
                          f"CVD 방향일치, VolZ<2.0, 30분모멘텀 조건, 유동성 통과까지 5개를 다 만족해야만 "
                          f"컷 이상이어도 초록/빨강으로 색칠된다(하나라도 걸리면 ⚠️필터 표시만 뜨고 살구색)."),
            ("매집/분산", f"prepump_score(매집)/preshort_score(분산), 100점 만점(v2): 고래매집/분산(CVD+RSI 히든 "
                        f"다이버전스) 35 + 포지션역발상(L/S) 25 + 가격정체(볼린저밴드폭 압축) 20 + 수급선행(VolZ) 20. "
                        f"'아직 안 터졌지만 거래량만 몰래 붙는' 구간을 찾는 신호라 롱/숏 Score와는 배점 구조가 다름. "
                        f"컷 {pp_current_min_score}점 이상이면 카드 색칠(파랑=매집, 보라=분산)."),
            ("Predict Score (P##)", "포지션 카드의 'Cross Nx' 옆 P##▲▼ 표시. 지금 수익(PNL)과는 완전 별개로, "
                        "'보유 방향이 앞으로도 유지될지'만 100점 만점(v2, 차감식)으로 예측한 값이다 — "
                        "가격 기울기(Slope) 30 + 가속도(Acceleration) 25 + CVD 방향일치 20 "
                        "+ EMA 방향일치 15 + Level(진입 당시 원본 점수) 10. "
                        "Slope/Accel은 점수가 아니라 '가격 자체'의 최근 변화 추세를 본다 — "
                        "기울기가 유지/가속되면 고득점, 완만해지거나 CVD가 반대로 틀면 감점. "
                        "화살표는 ▲유리한 방향 유지/▼불리하게 전환/‒횡보. 점수가 낮으면(특히 50 미만) "
                        "지금 수익이 나 있어도 우측에 PRED⚠ 경고가 뜬다 — 이건 오류가 아니라, "
                        "'수익 중일 때일수록 근거가 식어가는 걸 미리 알려주는' 게 원래 목적이다. "
                        "히스토리가 아직 안 쌓인 직후(서버/앱 재시작 직후)엔 중간값으로 보수적 처리된다."),
            ("카드 조작", "탭 1번: 티커 자동 입력. 더블탭: 상단 고정/해제. "
                        "고정 코인은 [코인명]으로 표시되고 항상 맨 위."),
            ("포지션 패널", "화면 하단 검은 패널. 한 번에 최대 2개 표시, 나머지는 위아래 드래그로 스크롤. "
                          "카드 탭 → 티커 자동 입력, Close 버튼 → 즉시 청산, "
                          "청산가는 증거금 소진 지점(진입가 ∓ 진입가/배율)."),
        ]
        return helps

    def show_help(self):
        """각 지표에 대한 설명 팝업 (스크롤 가능)"""
        if getattr(self, 'help_win', None) and self.help_win.winfo_exists():
            self.help_win.lift()
            return
        self.help_win = tk.Toplevel(self.root)
        self.help_win.title("지표 설명")
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.help_win.geometry(f"{sw}x{sh}")
        canvas = tk.Canvas(self.help_win, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        inner = tk.Frame(canvas)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win_id, width=e.width))
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        drag = {"y": 0, "top": 0.0}
        def _press(e):
            drag["y"] = e.y_root
            drag["top"] = canvas.yview()[0]
        def _motion(e):
            dy = e.y_root - drag["y"]
            h = max(canvas.winfo_height(), 1)
            ylo, yhi = canvas.yview()
            yspan = max(yhi - ylo, 0.0001)
            canvas.yview_moveto(max(0.0, min(1.0, drag["top"] - (dy / h) * yspan)))
        wrap = max(sw - 40, 200)
        fs = self.ui_font_base
        helps = self._help_entries()
        for title, desc in helps:
            t = tk.Label(inner, text=f"■ {title}", font=("Arial", fs, "bold"), anchor="w", fg="#1a5fb4")
            t.pack(fill="x", padx=8, pady=(8, 0))
            d = tk.Label(inner, text=desc, font=("Arial", max(fs - 1, 4)), anchor="w", justify="left",
                         wraplength=wrap, fg="#333333")
            d.pack(fill="x", padx=12, pady=(1, 2))
        btn_close = tk.Label(inner, text="닫기", bg="#cc0000", fg="white", font=("Arial", fs, "bold"),
                             relief="raised", bd=1, cursor="hand2", padx=10, pady=4)
        btn_close.pack(pady=12)
        btn_close.bind("<ButtonRelease-1>", lambda e: self.help_win.destroy())
        for wdg in (canvas, inner):
            wdg.bind("<ButtonPress-1>", _press, add="+")
            wdg.bind("<B1-Motion>", _motion, add="+")
        for child in inner.winfo_children():
            if child is not btn_close:
                child.bind("<ButtonPress-1>", _press, add="+")
                child.bind("<B1-Motion>", _motion, add="+")

    def safe_exit(self):
        msg = ("주문용 앱을 종료합니다.\n"
               "포지션과 잔고는 서버(trading_server.py)가 계속 관리합니다.\n\n"
               "종료하시겠습니까?")
        if messagebox.askyesno("종료", msg):
            self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    print(f"주문용 클라이언트 시작 | 데이터 폴더: {SCRIPT_DIR}")
    if not os.path.exists(MARKET_SNAPSHOT):
        print("서버 스냅샷이 아직 없습니다. trading_server.py 를 먼저 실행하세요.")
    app = TradingClient()
    app.run()