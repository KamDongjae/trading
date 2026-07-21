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
from collections import deque

# ============================================================
# 저장 경로
# ============================================================
DEFAULT_LEVERAGE = 10
FALLBACK_MIN_SCORE = 75
FALLBACK_PP_MIN_SCORE = 80
FALLBACK_WATCH_MIN_SCORE = 65
STALE_SEC = 15

try:
    _fallback_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _fallback_dir = os.getcwd()

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
MARKET_SNAPSHOT_UPBIT = os.path.join(SCRIPT_DIR, "server_market_upbit.csv")
ACCOUNT_SNAPSHOT = os.path.join(SCRIPT_DIR, "server_account.csv")
CMD_DIR = os.path.join(SCRIPT_DIR, "server_cmds")
RESULTS_FILE = os.path.join(SCRIPT_DIR, "server_results.csv")
HISTORY_FILE = os.path.join(SCRIPT_DIR, "trade_history_usd.csv")  # 서버가 실제로 쓰는 파일명과 일치시킴(기존엔 이름이 달라서 청산기록이 항상 비어 보였음)
os.makedirs(CMD_DIR, exist_ok=True)

current_min_score = FALLBACK_MIN_SCORE
pp_current_min_score = FALLBACK_PP_MIN_SCORE
watch_current_min_score = FALLBACK_WATCH_MIN_SCORE
current_interval = "1h"
current_margin_mode = "cross"  # 서버 기본값과 동일. read_account_snapshot이 실제 값으로 갱신함
bank_balance = 0.0
bank_total_deposit = 0.0
bank_total_spent = 0.0
fng_value = None
fng_class = ""
ALLOWED_INTERVALS = ["1h", "2h", "6h", "12h"]
CHART_INTERVALS = ["10m", "30m", "1h", "2h", "6h", "12h"]  # 차트 팝업 전용(기준봉 전환 버튼과는 별개)

def _f(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default

def read_market_snapshot(path=None):
    global current_min_score, pp_current_min_score, watch_current_min_score, current_interval
    path = path or MARKET_SNAPSHOT
    try:
        with open(path, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = []
            score_time = price_time = ""
            for r in reader:
                rows.append({
                    'ticker': r.get('ticker', ''),
                    'price': _f(r.get('price')),
                    'price_usd': _f(r.get('price_usd')) if r.get('price_usd') not in (None, '',) else None,
                    'chg_24h': _f(r.get('chg_24h'), 0),
                    'long_score': int(_f(r.get('long_score'))),
                    'short_score': int(_f(r.get('short_score'))),
                    # 신설: 출발 전 매집 구간 탐지 점수 (없는 구버전 CSV라도 안전하게 0 처리)
                    'prepump_score': int(_f(r.get('prepump_score'), 0)),
                    'preshort_score': int(_f(r.get('preshort_score'), 0)),
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
                score_time = r.get('score_time', '') or score_time
                price_time = r.get('price_time', '') or price_time
        return rows, score_time, price_time
    except Exception:
        return None

def read_account_snapshot():
    global current_margin_mode, bank_balance, bank_total_deposit, bank_total_spent, fng_value, fng_class
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
                elif row[0] == 'fng_value':
                    fng_value = int(_f(row[1])) if len(row) > 1 and row[1] != '' else None
                elif row[0] == 'fng_class':
                    fng_class = row[1] if len(row) > 1 else ""
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

def send_command(action, ticker='', amount=0, leverage=0, position_type='', exchange='bithumb', entry_price=0):
    cmd_id = f"{int(time.time()*1000)}_{random.randint(1000, 9999)}"
    fname = f"cmd_{cmd_id}.csv"
    tmp = os.path.join(CMD_DIR, "." + fname)
    final = os.path.join(CMD_DIR, fname)
    with open(tmp, 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerow([cmd_id, action, ticker, amount, leverage, position_type, exchange, entry_price])
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
        self.current_exchange = 'bithumb'  # 코인탭 빗썸/업비트 토글 — open/close/차트 명령에도 그대로 씀
        self._last_render_data = []
        self._tap_state = {"last_time": 0.0, "last_ticker": None}
        self.DOUBLE_TAP_MS = 450
        self._account = (0, "", [])
        self._market_mtime = 0.0

        # 화면 폭만으로 "모바일"을 판단하면 VNC/X11 세션(해상도는 작아도 DPI는 표준
        # 96dpi)까지 모바일로 오판해서 글자가 깨알만해지는 문제가 있었다. 실제 폰
        # 화면은 DPI가 훨씬 높으니(보통 300dpi+) DPI까지 같이 확인한다.
        try:
            dpi = self.root.winfo_fpixels('1i')
        except Exception:
            dpi = 96.0
        self.is_mobile = (w <= 1280) and (dpi > 150)
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
        self.fng_label = tk.Label(row0, text="공포탐욕지수: -", font=("Arial", FONT_BOLD_LABEL, "bold"), fg="gray")
        self.fng_label.pack(side="right", padx=LABEL_PADX)

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
        btn_report = tk.Label(row1, text="리포트", bg="#5a5f66", fg="white", font=("Arial", FONT_BTN, "bold"),
                              relief="raised", bd=1, cursor="hand2", padx=6, pady=1)
        btn_report.pack(side="left", padx=4)
        btn_report.bind("<ButtonRelease-1>", lambda e: self.generate_report())
        btn_report_dir = tk.Label(row1, text="경로수정", bg="#3f4349", fg="white", font=("Arial", FONT_BTN, "bold"),
                                  relief="raised", bd=1, cursor="hand2", padx=6, pady=1)
        btn_report_dir.pack(side="left", padx=4)
        btn_report_dir.bind("<ButtonRelease-1>", lambda e: self.set_report_dir())
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

        # 버튼
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(side="bottom", fill="x", padx=4, pady=(2, 8))
        lbl_font = ("Arial", FONT_BTN, "bold")
        for text, color, cmd in [
            ("롱 진입", "#44aa44", lambda e: self.open_position("long")),
            ("숏 진입", "#dd3333", lambda e: self.open_position("short")),
            ("청산", "#555555", lambda e: self.close_position()),
            ("차트", "#3a6ea5", lambda e: self.open_chart_for_entry()),
            ("빠른입력", "#7a4aa0", lambda e: self.open_quick_entry_dialog()),
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

        # 포지션 패널 (코인/포지션 뷰 전환 버튼이 pack 여부를 제어 — 여기선 만들기만 함)
        self.pos_panel = tk.Frame(self.root, bd=1, relief="groove", bg="#16181d")
        self.pos_canvas = tk.Canvas(self.pos_panel, bg="#16181d", highlightthickness=0)
        self.pos_canvas.pack(side="left", fill="both", expand=True)
        self.pos_scrollbar = ttk.Scrollbar(self.pos_panel, orient="vertical", command=self.pos_canvas.yview)
        self.pos_scrollbar.pack(side="right", fill="y")
        self.pos_canvas.configure(yscrollcommand=self.pos_scrollbar.set)
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
        self._last_pos_reorder_time = 0.0
        self._pos_reorder_interval = 3.0  # 코인 목록과 동일한 이유로 스로틀 (매 폴링마다 재배치하면 깜빡임)
        self._pos_panel_visible = False
        self.view_mode = 'coins'  # 'coins' | 'positions' — 뷰 전환 버튼으로 바뀜

        # 코인 목록 / 포지션 목록 뷰 전환 버튼 — 둘을 아예 별개 화면으로 분리
        view_bar = tk.Frame(self.root)
        view_bar.pack(side="top", fill="x", padx=4, pady=(2, 0))
        self.btn_view_coins = tk.Label(view_bar, text="코인", font=("Arial", self.FONT_SMALL, "bold"),
                                        bg="#1a7abf", fg="white", relief="raised", bd=1, cursor="hand2",
                                        padx=10, pady=3)
        self.btn_view_coins.pack(side="left", padx=(0, 3), fill="x", expand=True)
        self.btn_view_coins.bind("<ButtonRelease-1>", lambda e: self.switch_view('coins'))
        self.btn_view_positions = tk.Label(view_bar, text="포지션 (0)", font=("Arial", self.FONT_SMALL, "bold"),
                                            bg="#eeeeee", fg="black", relief="raised", bd=1, cursor="hand2",
                                            padx=10, pady=3)
        self.btn_view_positions.pack(side="left", padx=(3, 0), fill="x", expand=True)
        self.btn_view_positions.bind("<ButtonRelease-1>", lambda e: self.switch_view('positions'))

        # 거래소 토글 — 코인탭 전용(빗썸/업비트). 여기서 고른 거래소가 코인 목록/차트/
        # 롱숏진입 명령에 그대로 쓰인다(self.current_exchange).
        self.exchange_bar = exchange_bar = tk.Frame(self.root)
        exchange_bar.pack(side="top", fill="x", padx=4, pady=(4, 0))
        tk.Label(exchange_bar, text="거래소:", font=("Arial", self.FONT_SMALL, "bold")).pack(side="left", padx=(2, 4))
        self._exchange_btn_widgets = {}
        for ex, label in [("bithumb", "빗썸"), ("upbit", "업비트")]:
            b = tk.Label(exchange_bar, text=label, font=("Arial", self.FONT_SMALL, "bold"),
                         relief="raised", bd=1, cursor="hand2", padx=10, pady=3)
            b.pack(side="left", padx=2)
            b.bind("<ButtonRelease-1>", lambda e, ex_=ex: self.set_exchange(ex_))
            self._exchange_btn_widgets[ex] = b
        self._highlight_exchange_buttons()

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

        # 정렬 버튼
        self.sortbar = sortbar = tk.Frame(self.root)
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
        self.tree_container = tree_container = tk.Frame(self.root)
        tree_container.pack(fill="both", expand=True, padx=4, pady=2)
        self.card_canvas = tk.Canvas(tree_container, highlightthickness=0)
        self.card_canvas.grid(row=0, column=0, sticky="nsew")
        self.card_scrollbar = ttk.Scrollbar(tree_container, orient="vertical", command=self.card_canvas.yview)
        self.card_scrollbar.grid(row=0, column=1, sticky="ns")
        self.card_canvas.configure(yscrollcommand=self.card_scrollbar.set)
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

        btn_bar = tk.Frame(self.history_win)
        btn_bar.pack(pady=10)
        tk.Button(btn_bar, text="닫기", command=self.history_win.destroy,
                  font=("Arial", 11, "bold"), padx=30, pady=8).pack(side="left", padx=6)
        tk.Button(btn_bar, text="청산 기록 초기화", command=self._reset_history_clicked,
                  bg="#cc4444", fg="white", font=("Arial", 11, "bold"), padx=20, pady=8).pack(side="left", padx=6)

    def _reset_history_clicked(self):
        """'끝난 것만 삭제' — 청산/진입 로그(trade_history_usd.csv)만 지운다.
        보유 중인 포지션은 이 파일과 무관하게 서버 positions에 그대로 남아있어서
        영향받지 않는다(서버 srv_reset_history 참고)."""
        if not messagebox.askyesno("확인", "청산된 거래 기록을 전부 지웁니다.\n(진행중인 포지션은 그대로 유지됩니다)\n계속할까요?"):
            return
        def on_success(msg):
            messagebox.showinfo("완료", msg)
            if self.history_win and self.history_win.winfo_exists():
                self.history_win.destroy()
            self.show_history()
        self._send_and_wait_callback('reset_history', on_success, ticker="", label="청산 기록 초기화")

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
        if abs(dy) > 12:  # 터치스크린은 탭할 때도 손가락이 몇 px씩 흔들려서 4px는 너무 예민했음
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
            self.pos_panel.pack(fill="both", expand=True, padx=6, pady=(2, 1))
            self._pos_panel_visible = True

    def switch_view(self, mode):
        """코인 목록 화면과 포지션 목록 화면을 서로 다른 화면처럼 완전히 분리해서 보여준다."""
        if mode == self.view_mode:
            return
        self.view_mode = mode
        if mode == 'coins':
            self._hide_pos_panel()
            self.exchange_bar.pack(side="top", fill="x", padx=4, pady=(4, 0))
            self.sortbar.pack(side="top", fill="x", padx=4, pady=(0, 2))
            self.tree_container.pack(fill="both", expand=True, padx=4, pady=2)
        else:
            self.tree_container.pack_forget()
            self.sortbar.pack_forget()
            self.exchange_bar.pack_forget()
            self._show_pos_panel()
            _, _, pos_list = self._account
            self._render_pos_panel(pos_list)
        self._highlight_view_buttons()

    def _highlight_view_buttons(self):
        if self.view_mode == 'coins':
            self._cfg(self.btn_view_coins, bg="#1a7abf", fg="white")
            self._cfg(self.btn_view_positions, bg="#eeeeee", fg="black")
        else:
            self._cfg(self.btn_view_coins, bg="#eeeeee", fg="black")
            self._cfg(self.btn_view_positions, bg="#1a7abf", fg="white")

    def set_exchange(self, exchange):
        """코인탭의 빗썸/업비트 토글. 코인 목록/가격/차트/롱숏진입이 전부 이 값을 따라간다."""
        if exchange == self.current_exchange:
            return
        self.current_exchange = exchange
        self._highlight_exchange_buttons()
        self._market_mtime = 0.0  # 다음 poll_files 사이클(늦어도 1초 내)에 무조건 다시 읽게 강제
        self._last_render_data = []

    def _highlight_exchange_buttons(self):
        for ex, w in self._exchange_btn_widgets.items():
            if ex == self.current_exchange:
                self._cfg(w, bg="#1a7abf", fg="white")
            else:
                self._cfg(w, bg="#eeeeee", fg="black")

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

    def _show_empty_pos_message(self):
        if not hasattr(self, '_empty_pos_label') or not self._empty_pos_label.winfo_exists():
            self._empty_pos_label = tk.Label(self.pos_inner, text="보유 중인 포지션이 없습니다",
                                              font=("Arial", self.ui_font_base), bg="#16181d", fg="#888888")
            self._empty_pos_label.pack(pady=30)

    def _clear_empty_pos_message(self):
        if hasattr(self, '_empty_pos_label') and self._empty_pos_label.winfo_exists():
            self._empty_pos_label.destroy()

    def _render_pos_panel(self, pos_list):
        self._cfg(self.btn_view_positions, text=f"포지션 ({len(pos_list)})")
        if not pos_list:
            for t in list(self.pos_cards.keys()):
                self.pos_cards[t].destroy()
                del self.pos_cards[t]
            self._last_pos_order = None
            if self.view_mode == 'positions':
                self._show_empty_pos_message()
            return
        self._clear_empty_pos_message()

        # 뷰 전환 버튼이 패널 표시 여부를 관리한다 — 코인 화면에서는 포지션이 있어도 안 보여준다.
        if self.view_mode != 'positions':
            return

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
            is_new_card = card is None
            if is_new_card:
                card = tk.Frame(self.pos_inner, bg=DARK_BG, bd=1, relief="solid",
                                 highlightbackground="#2b2f36", highlightthickness=1)

                # --- 헤더: 배지 + 티커 + Perp/Cross 태그 + 경고 ---
                hdr = tk.Frame(card, bg=DARK_BG, cursor="hand2")
                hdr.pack(fill="x", padx=8, pady=(8, 4))
                card._badge = tk.Label(hdr, font=("Arial", fs, "bold"), width=2, fg="white", cursor="hand2")
                card._badge.pack(side="left")
                card._title = tk.Label(hdr, font=("Arial", fs + 1, "bold"), bg=DARK_BG, fg=FG, cursor="hand2")
                card._title.pack(side="left", padx=(6, 6))

                def _open_chart(e, tk_=t):
                    # 드래그(스크롤)였다면 팝업 안 띄움 — 나머지 카드 영역의 티커채우기와 동일한 규칙
                    if self._pos_drag.get("dragged"):
                        return
                    self.show_chart_popup(tk_)
                # 이름 글자 하나만으로는 클릭 영역이 너무 좁아서 잘 안 눌렸다는 피드백 반영 —
                # 헤더 줄(배지+이름) 전체를 클릭 영역으로 넓힌다.
                for chart_wdg in (hdr, card._badge, card._title):
                    chart_wdg.bind("<ButtonPress-1>", self._pos_press, add="+")
                    chart_wdg.bind("<B1-Motion>", self._pos_motion, add="+")
                    chart_wdg.bind("<ButtonRelease-1>", _open_chart, add="+")
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
                for wdg in (card, hdr, card._badge, card._tag_perp, card._tag_cross,
                            card._trend_light, pnl_row, pnl_col, card._pnl, roi_col, card._roe, row2, row3):
                    wdg.bind("<ButtonPress-1>", self._pos_press, add="+")
                    wdg.bind("<B1-Motion>", self._pos_motion, add="+")
                    wdg.bind("<ButtonRelease-1>", _fill, add="+")
                self.pos_cards[t] = card
                card.pack(fill="x", padx=4, pady=4)

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
        now = time.time()
        order_changed = new_pos_order != self._last_pos_order
        due = (now - self._last_pos_reorder_time) >= self._pos_reorder_interval
        if order_changed and (due or not self._last_pos_order):
            prev = None
            for t in new_pos_order:
                w = self.pos_cards[t]
                if prev is None:
                    w.pack_configure(fill="x", padx=4, pady=4)
                else:
                    w.pack_configure(fill="x", padx=4, pady=4, after=prev)
                prev = w
            self._last_pos_order = new_pos_order
            self._last_pos_reorder_time = now

        self.pos_inner.update_idletasks()
        self.pos_canvas.configure(scrollregion=self.pos_canvas.bbox("all"))
        # 이제 포지션 화면이 전체를 차지하므로(코인 화면과 분리됨) 높이를 2개로 제한할
        # 필요가 없다 — pos_canvas 자체가 fill=both/expand=True라 알아서 채워지고,
        # 카드가 더 많으면 기존 드래그/휠 스크롤(_pos_press/_pos_motion)로 넘겨본다.

    def poll_files(self):
        snapshot_path = MARKET_SNAPSHOT if self.current_exchange == 'bithumb' else MARKET_SNAPSHOT_UPBIT
        try:
            mt = os.path.getmtime(snapshot_path)
        except Exception:
            mt = 0
        if mt and mt != self._market_mtime:
            res = read_market_snapshot(snapshot_path)
            if res:
                rows, score_time, price_time = res
                self._market_mtime = mt
                self._render_table(rows)
                if current_interval != self._last_interval_shown:
                    self._highlight_interval_buttons(current_interval)
        age = time.time() - mt if mt else 1e9
        ex_label = "빗썸" if self.current_exchange == 'bithumb' else "업비트"
        if age > STALE_SEC:
            self.server_label.config(text=f"서버[{ex_label}]: 연결 끊김 (trading_server.py 실행 확인)", fg="red")
        else:
            self.server_label.config(text=f"서버[{ex_label}]: 정상 (기준봉 {current_interval} / 관심 {watch_current_min_score}·컷 {current_min_score}점 / 매집·분산 컷 {pp_current_min_score}점, {age:.0f}초 전 갱신)", fg="gray")
        acc = read_account_snapshot()
        if acc:
            self._account = acc
            self._update_account_labels()
            if current_margin_mode != self._last_margin_mode_shown:
                self._highlight_margin_mode(current_margin_mode)
        self.root.after(1000, self.poll_files)

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
        if fng_value is not None:
            fng_colors = {
                "Extreme Fear": "#8b0000", "Fear": "#f6465a", "Neutral": "#888888",
                "Greed": "#3ddc84", "Extreme Greed": "#0ecb81",
            }
            self.fng_label.config(text=f"공포탐욕지수: {fng_value} ({fng_class})",
                                   fg=fng_colors.get(fng_class, "gray"))
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

    def _apply_sort(self, data_list):
        pinned = [r for r in data_list if r['ticker'] in self.pinned_tickers]
        non_pinned = [r for r in data_list if r['ticker'] not in self.pinned_tickers]
        cut = current_min_score
        pp_cut = pp_current_min_score
        colored = [r for r in non_pinned if r['long_score'] >= cut or r['short_score'] >= cut
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
                chg24h = row.get('chg_24h', 0)
                chg24h_str = f"{chg24h:+.2f}%"

                cvd_val = row.get('cvd', 0)
                cvd_str = f"{cvd_val:+,.0f}" if abs(cvd_val) >= 100 else f"{cvd_val:+.2f}"
                ls, ss = row['long_score'], row['short_score']
                pp, ps = row.get('prepump_score', 0), row.get('preshort_score', 0)
                cut = current_min_score
                wcut = watch_current_min_score
                pp_cut = pp_current_min_score
                if ls >= cut and ls >= ss:
                    bg = "#6fe08a"
                elif ss >= cut:
                    bg = "#f5807d"
                elif pp >= pp_cut and pp >= ps:
                    bg = "#7fbdf5"   # 파랑 계열: 아직 안 터진 매집 구간(prepump) 대기
                elif ps >= pp_cut:
                    bg = "#d38ff2"   # 보라 계열: 고점 분산(preshort) 대기
                elif ls >= wcut or ss >= wcut:
                    bg = "#f2e070"   # 진노랑: 진입 컷엔 못 미치지만 "관심" 구간(65점대)
                else:
                    bg = "white"
                display_ticker = f"[{ticker}]" if ticker in self.pinned_tickers else ticker
                line1 = f"{display_ticker}  {chg24h_str} ({krw_str})  {usd_str}  롱{ls} 숏{ss}  매집{pp} 분산{ps}"
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
                    lbl2 = tk.Label(card, font=("Arial", max(round((self.ui_font_base - 1) * 1.5), 4)), anchor="w",
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
            # 지났을 때만 수행한다. pack_forget()으로 전부 뗐다 다시 붙이면 그 순간 리스트가
            # 통째로 사라졌다 다시 그려져서 눈에 띄게 번쩍인다 — 대신 pack_configure(after=...)로
            # 카드를 하나씩 '제자리로 슬쩍 이동'만 시키면 화면이 끊기지 않는다.
            now = time.time()
            order_changed = new_order != self._last_table_order
            due = (now - self._last_reorder_time) >= self._reorder_interval
            if order_changed and (self._force_resort or due or not self._last_table_order):
                prev = None
                for ticker in new_order:
                    w = self.card_widgets[ticker]
                    if prev is None:
                        w.pack_configure(fill="x", padx=2, pady=1)
                    else:
                        w.pack_configure(fill="x", padx=2, pady=1, after=prev)
                    prev = w
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
        if abs(dy) > 12:  # 터치스크린은 탭할 때도 손가락이 몇 px씩 흔들려서 4px는 너무 예민했음
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

    def _send_and_wait(self, action, ticker='', amount=0, leverage=0, position_type='', label='', exchange=None, entry_price=0):
        try:
            cmd_id = send_command(action, ticker, amount, leverage, position_type,
                                   exchange=exchange or self.current_exchange, entry_price=entry_price)
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

    def _send_and_wait_callback(self, action, on_success, ticker='', amount=0, leverage=0,
                                 position_type='', label='', tries=16, exchange=None, entry_price=0):
        """_send_and_wait과 달리 성공 시 팝업 대신 on_success(msg) 콜백을 호출한다
        (차트 팝업처럼, 응답 메시지 안의 데이터를 더 써먹어야 할 때 씀)."""
        try:
            cmd_id = send_command(action, ticker, amount, leverage, position_type,
                                   exchange=exchange or self.current_exchange, entry_price=entry_price)
        except Exception as e:
            messagebox.showerror("오류", f"명령 전송 실패: {e}")
            return
        self._wait_result_callback(cmd_id, label or action, tries=tries, on_success=on_success)

    def _wait_result_callback(self, cmd_id, label, tries, on_success):
        res = find_result(cmd_id)
        if res:
            status, msg = res
            if status == 'ok':
                on_success(msg)
            else:
                messagebox.showwarning(f"{label} 실패", msg)
            return
        if tries <= 0:
            messagebox.showwarning("응답 없음", "서버 응답이 없습니다.\ntrading_server.py 실행 여부를 확인하세요.")
            return
        self.root.after(500, lambda: self._wait_result_callback(cmd_id, label, tries - 1, on_success))

    def show_chart_popup(self, ticker, interval="1h"):
        """포지션 카드의 티커 이름을 클릭하면 캔들차트 팝업을 띄운다."""
        def on_success(msg):
            path = msg.split(": ", 1)[1].strip() if ": " in msg else None
            if not path or not os.path.exists(path):
                messagebox.showerror("오류", f"차트 파일을 못 찾음: {msg}")
                return
            self._render_chart_window(ticker, path, interval)
        # 캔들 조회는 빗썸 API를 실제로 호출해야 해서(재시도 포함 최대 20초 넘게 걸릴 수 있음)
        # 다른 즉시 처리되는 명령들보다 훨씬 오래 기다려줘야 한다(60회×0.5초 = 30초).
        # position_type 필드를 시간봉 문자열 전달용으로 재사용 (get_candles 전용 관례)
        self._send_and_wait_callback('get_candles', on_success, ticker=ticker, position_type=interval,
                                      label=f"{ticker} {interval} 차트 조회", tries=60)

    def _render_chart_window(self, ticker, csv_path, interval):
        """서버가 내려준 캔들+지표 CSV를 읽어서 Tkinter Canvas로 직접 그린다
        (matplotlib 없이 — 클라이언트를 계속 무의존성으로 유지하려는 목적).
        가격 패널(캔들+EMA20/60/120+볼린저밴드) + RSI 패널 + RSI Delta 패널, 3단 구성.
        상단 버튼으로 시간봉을 바꾸면 서버에 다시 요청해서 같은 창에 새로 그린다."""
        rows = []
        try:
            with open(csv_path, 'r', newline='', encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    def gf(key):
                        v = row.get(key, '')
                        try:
                            return float(v)
                        except (TypeError, ValueError):
                            return None
                    o, h, l, c = gf('open'), gf('high'), gf('low'), gf('close')
                    if None in (o, h, l, c):
                        continue
                    rows.append({
                        'open': o, 'high': h, 'low': l, 'close': c,
                        'rsi': gf('RSI'), 'rsi_delta': gf('RSI_Delta'),
                        'ema20': gf('EMA20'), 'ema60': gf('EMA60'), 'ema120': gf('EMA120'),
                        'bb_upper': gf('BB_UPPER'), 'bb_mid': gf('BB_MID'), 'bb_lower': gf('BB_LOWER'),
                        'timestamp': row.get('timestamp', ''),
                    })
        except Exception as e:
            messagebox.showerror("오류", f"차트 파일을 못 읽음: {e}")
            return
        if not rows:
            messagebox.showerror("오류", "차트 데이터가 비어있습니다")
            return

        # 기존에 이 티커로 열려있던 차트 창이 있으면 재사용(시간봉 전환용), 없으면 새로 생성
        win = getattr(self, '_chart_windows', {}).get(ticker)
        if not (win and win.winfo_exists()):
            win = tk.Toplevel(self.root)
            if not hasattr(self, '_chart_windows'):
                self._chart_windows = {}
            self._chart_windows[ticker] = win
            win.geometry("900x640")
            win.configure(bg="#111111")

            top = tk.Frame(win, bg="#111111")
            top.pack(fill="x")
            title_label = tk.Label(top, font=("Arial", 11, "bold"), bg="#111111", fg="white")
            title_label.pack(side="left", padx=8, pady=6)
            win._title_label = title_label

            tf_bar = tk.Frame(top, bg="#111111")
            tf_bar.pack(side="right", padx=8)
            win._tf_buttons = {}
            for iv in CHART_INTERVALS:
                b = tk.Label(tf_bar, text=iv, font=("Arial", 9, "bold"), padx=8, pady=2,
                             cursor="hand2", relief="raised", bd=1)
                b.pack(side="left", padx=2)
                b.bind("<ButtonRelease-1>", lambda e, iv_=iv: self.show_chart_popup(ticker, iv_))
                win._tf_buttons[iv] = b

            price_canvas = tk.Canvas(win, bg="#111111", highlightthickness=0, height=340)
            price_canvas.pack(fill="both", expand=True, padx=4)
            rsi_canvas = tk.Canvas(win, bg="#111111", highlightthickness=0, height=110)
            rsi_canvas.pack(fill="x", padx=4, pady=(4, 0))
            rd_canvas = tk.Canvas(win, bg="#111111", highlightthickness=0, height=90)
            rd_canvas.pack(fill="x", padx=4, pady=(4, 8))
            win._price_canvas, win._rsi_canvas, win._rd_canvas = price_canvas, rsi_canvas, rd_canvas
        else:
            win.lift()

        win.title(f"{ticker} {interval} 차트")
        win._title_label.config(text=f"{ticker}  {interval}  (최근 {len(rows)}봉)")
        for iv, b in win._tf_buttons.items():
            active = (iv == interval)
            b.config(bg="#1a7abf" if active else "#2b2f36", fg="white" if active else "#9aa0a6")

        def draw_price(_event=None):
            canvas = win._price_canvas
            canvas.delete("all")
            w, h = canvas.winfo_width(), canvas.winfo_height()
            n = len(rows)
            if n == 0 or w < 50 or h < 50:
                return
            pad_l, pad_r, pad_t, pad_b = 8, 65, 8, 18
            plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
            if plot_w <= 0 or plot_h <= 0:
                return
            candle_w = plot_w / n
            price_vals = [v for r in rows for v in (r['high'], r['low'],
                          r.get('bb_upper') or r['high'], r.get('bb_lower') or r['low'])]
            p_max, p_min = max(price_vals), min(price_vals)
            p_range = (p_max - p_min) or 1

            def y(price):
                return pad_t + (p_max - price) / p_range * plot_h

            for frac in (0, 0.25, 0.5, 0.75, 1.0):
                price = p_max - frac * p_range
                yy = pad_t + frac * plot_h
                canvas.create_line(pad_l, yy, w - pad_r, yy, fill="#2a2a2a")
                pfmt = f"{price:,.4f}" if price < 1 else f"{price:,.2f}"
                canvas.create_text(w - pad_r + 5, yy, text=pfmt, fill="#cccccc",
                                    font=("Arial", 8), anchor="w")

            # 볼린저밴드 (상단/중단/하단 라인)
            def plot_line(key, color):
                pts = []
                for i, r in enumerate(rows):
                    v = r.get(key)
                    if v is None:
                        continue
                    x_center = pad_l + i * candle_w + candle_w / 2
                    pts.extend([x_center, y(v)])
                if len(pts) >= 4:
                    canvas.create_line(*pts, fill=color, width=1)

            plot_line('bb_upper', "#5a6a7a")
            plot_line('bb_mid', "#5a6a7a")
            plot_line('bb_lower', "#5a6a7a")

            # 캔들스틱
            for i, r in enumerate(rows):
                x_center = pad_l + i * candle_w + candle_w / 2
                up = r['close'] >= r['open']
                color = "#00ff88" if up else "#ff3838"
                canvas.create_line(x_center, y(r['high']), x_center, y(r['low']), fill=color, width=1)
                body_top, body_bot = y(max(r['open'], r['close'])), y(min(r['open'], r['close']))
                bw = max(candle_w * 0.6, 1)
                if abs(body_bot - body_top) < 1:
                    body_bot = body_top + 1
                canvas.create_rectangle(x_center - bw / 2, body_top, x_center + bw / 2, body_bot,
                                         fill=color, outline=color)

            # EMA20/60/120
            plot_line('ema20', "#ffff00")
            plot_line('ema60', "#00ffff")
            plot_line('ema120', "#ff00ff")

            legend_y = pad_t + 4
            for text, color in (("EMA20", "#ffff00"), ("EMA60", "#00ffff"), ("EMA120", "#ff00ff"), ("BB", "#5a6a7a")):
                canvas.create_text(pad_l + 4, legend_y, text=text, fill=color, font=("Arial", 8, "bold"), anchor="nw")
                legend_y += 12

            # x축 날짜 라벨(mm.dd) — 회색 구분선 위에 표시. 캔들 몇 개당 하나씩만 찍어서 안 겹치게.
            axis_y = pad_t + plot_h
            canvas.create_line(pad_l, axis_y, w - pad_r, axis_y, fill="#444444")
            label_every = max(n // 6, 1)
            last_label = None
            for i, r in enumerate(rows):
                if i % label_every != 0:
                    continue
                ts = r.get('timestamp', '')
                if not ts or len(ts) < 10:
                    continue
                mmdd = f"{ts[5:7]}.{ts[8:10]}"  # "YYYY-MM-DD HH:MM:SS" -> "MM.DD"
                if mmdd == last_label:
                    continue
                last_label = mmdd
                x_center = pad_l + i * candle_w + candle_w / 2
                canvas.create_line(x_center, axis_y, x_center, axis_y + 3, fill="#666666")
                canvas.create_text(x_center, axis_y + 5, text=mmdd, fill="#999999",
                                    font=("Arial", 8), anchor="n")

        def draw_rsi(_event=None):
            canvas = win._rsi_canvas
            canvas.delete("all")
            w, h = canvas.winfo_width(), canvas.winfo_height()
            n = len(rows)
            if n == 0 or w < 50 or h < 30:
                return
            pad_l, pad_r, pad_t, pad_b = 8, 65, 6, 6
            plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
            if plot_w <= 0 or plot_h <= 0:
                return
            candle_w = plot_w / n

            def y(v):
                return pad_t + (100 - v) / 100 * plot_h

            for level, dash in ((30, True), (50, False), (70, True)):
                yy = y(level)
                canvas.create_line(pad_l, yy, w - pad_r, yy, fill="#3a3a3a" if not dash else "#444444")
                canvas.create_text(w - pad_r + 5, yy, text=str(level), fill="#999999",
                                    font=("Arial", 7), anchor="w")
            canvas.create_text(pad_l + 4, pad_t + 2, text="RSI(14)", fill="#ffa500",
                                font=("Arial", 8, "bold"), anchor="nw")

            pts = []
            for i, r in enumerate(rows):
                if r.get('rsi') is None:
                    continue
                x_center = pad_l + i * candle_w + candle_w / 2
                pts.extend([x_center, y(max(0, min(100, r['rsi'])))])
            if len(pts) >= 4:
                canvas.create_line(*pts, fill="#ffa500", width=1.3)

        def draw_rsi_delta(_event=None):
            canvas = win._rd_canvas
            canvas.delete("all")
            w, h = canvas.winfo_width(), canvas.winfo_height()
            n = len(rows)
            if n == 0 or w < 50 or h < 30:
                return
            pad_l, pad_r, pad_t, pad_b = 8, 65, 6, 6
            plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
            if plot_w <= 0 or plot_h <= 0:
                return
            candle_w = plot_w / n
            deltas = [r['rsi_delta'] for r in rows if r.get('rsi_delta') is not None]
            if not deltas:
                return
            d_max = max(abs(max(deltas)), abs(min(deltas)), 1)

            def y(v):
                return pad_t + (d_max - v) / (2 * d_max) * plot_h

            zero_y = y(0)
            canvas.create_line(pad_l, zero_y, w - pad_r, zero_y, fill="#555555")
            canvas.create_text(w - pad_r + 5, zero_y, text="0", fill="#999999", font=("Arial", 7), anchor="w")
            canvas.create_text(pad_l + 4, pad_t + 2, text="RSI Δ", fill="#00ccff",
                                font=("Arial", 8, "bold"), anchor="nw")

            pts = []
            for i, r in enumerate(rows):
                if r.get('rsi_delta') is None:
                    continue
                x_center = pad_l + i * candle_w + candle_w / 2
                pts.extend([x_center, y(r['rsi_delta'])])
            if len(pts) >= 4:
                canvas.create_line(*pts, fill="#00ccff", width=1.3)

        def draw_all(_event=None):
            draw_price()
            draw_rsi()
            draw_rsi_delta()

        win._price_canvas.bind("<Configure>", draw_all)
        win._rsi_canvas.bind("<Configure>", draw_all)
        win._rd_canvas.bind("<Configure>", draw_all)
        win.after(50, draw_all)

    def generate_report(self):
        self._send_and_wait('generate_report', label="리포트 생성")

    def set_report_dir(self):
        new_dir = simpledialog.askstring(
            "리포트 저장 경로 변경",
            "PDF 리포트를 저장할 폴더 경로를 입력하세요:\n"
            "(기본값은 안드로이드 공용 문서함 — 예: /storage/emulated/0/Documents)"
        )
        if new_dir and new_dir.strip():
            self._send_and_wait('set_report_dir', ticker=new_dir.strip(), label="리포트 경로 변경")

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

    def open_chart_for_entry(self):
        """티커 입력칸에 있는 코인의 차트를 띄운다 — 포지션 카드 클릭 시 뜨는 것과 동일한 팝업."""
        ticker = self.ticker_entry.get().strip().upper()
        if not ticker:
            messagebox.showwarning("경고", "티커를 입력하세요.")
            return
        self.show_chart_popup(ticker)

    def open_quick_entry_dialog(self):
        """[2026-07-21 추가] 빠른 입력 — 실제 거래소(바이낸스 등)에서 이미 체결한 포지션을
        페이퍼 계좌에 그대로 옮겨 등록한다. 코인/방향/레버리지/진입가/투입금액을 입력하면
        수수료·청산가를 미리 계산해서 보여주고, 등록을 누르면 그 진입가 그대로(라이브
        시세 무관) 서버에 포지션이 생성된다(서버 srv_open_manual 참고)."""
        win = tk.Toplevel(self.root)
        win.title("빠른 입력 — 포지션 직접 등록")
        win.geometry("420x460")

        tk.Label(win, text="실제로 체결된 포지션을 그대로 옮겨 등록합니다\n(진입가를 직접 지정 — 라이브 시세와 무관)",
                 font=("Arial", 10), fg="#555555", justify="left").pack(pady=(10, 6), padx=14, anchor="w")

        form = tk.Frame(win)
        form.pack(fill="x", padx=14, pady=4)

        def add_row(label_text, default=""):
            row = tk.Frame(form)
            row.pack(fill="x", pady=4)
            tk.Label(row, text=label_text, width=9, anchor="w", font=("Arial", 11)).pack(side="left")
            ent = tk.Entry(row, font=("Arial", 12))
            ent.insert(0, default)
            ent.pack(side="left", fill="x", expand=True)
            return ent

        ticker_entry = add_row("코인", self.ticker_entry.get().strip().upper())

        dir_var = tk.StringVar(value="long")
        dir_row = tk.Frame(form)
        dir_row.pack(fill="x", pady=4)
        tk.Label(dir_row, text="방향", width=9, anchor="w", font=("Arial", 11)).pack(side="left")
        tk.Radiobutton(dir_row, text="롱", variable=dir_var, value="long", font=("Arial", 11)).pack(side="left")
        tk.Radiobutton(dir_row, text="숏", variable=dir_var, value="short", font=("Arial", 11)).pack(side="left")

        lev_entry = add_row("레버리지", "10")
        entry_price_entry = add_row("진입가")
        amount_entry = add_row("투입금액($)")

        preview_lbl = tk.Label(win, text="값을 입력하면 수수료·청산가 미리보기가 표시됩니다.",
                                font=("Arial", 10), fg="#1a4a7a", justify="left", anchor="w")
        preview_lbl.pack(fill="x", padx=14, pady=(10, 4))

        def update_preview(*_):
            try:
                entry_price = float(entry_price_entry.get())
                amount = float(amount_entry.get())
                lev = float(lev_entry.get() or 10)
                if entry_price <= 0 or amount <= 0 or lev <= 0:
                    raise ValueError
                fee = amount * lev * 0.0004  # FEE_RATE — 서버 상수와 동일 값
                notional = amount * lev
                liq = entry_price * (1 - 0.9 / lev) if dir_var.get() == "long" else entry_price * (1 + 0.9 / lev)
                preview_lbl.config(text=(f"명목가치: ${notional:,.2f}   예상 수수료: ${fee:,.2f}\n"
                                          f"청산가(격리기준 참고값): ${liq:,.4f}\n"
                                          f"※ 실제 계좌가 크로스마진이면 다른 포지션 손익에 따라 달라질 수 있음"))
            except Exception:
                preview_lbl.config(text="값을 정확히 입력하면 미리보기가 표시됩니다.")

        for e in (lev_entry, entry_price_entry, amount_entry):
            e.bind("<KeyRelease>", update_preview)
        dir_var.trace_add("write", lambda *a: update_preview())

        def submit():
            ticker = ticker_entry.get().strip().upper()
            if not ticker:
                messagebox.showwarning("경고", "코인을 입력하세요.", parent=win)
                return
            try:
                entry_price = float(entry_price_entry.get())
                amount = float(amount_entry.get())
                lev = int(float(lev_entry.get() or 10))
                if entry_price <= 0 or amount <= 0 or lev <= 0:
                    raise ValueError
            except Exception:
                messagebox.showwarning("경고", "레버리지/진입가/투입금액을 정확히 입력하세요.", parent=win)
                return

            def on_success(msg):
                messagebox.showinfo("등록 완료", msg)
                if win.winfo_exists():
                    win.destroy()

            self._send_and_wait_callback('open_manual', on_success, ticker=ticker, amount=amount,
                                          leverage=lev, position_type=dir_var.get(), entry_price=entry_price,
                                          label="빠른입력 등록")

        btn_row = tk.Frame(win)
        btn_row.pack(fill="x", padx=14, pady=14)
        tk.Button(btn_row, text="취소", command=win.destroy, font=("Arial", 11), padx=20, pady=8).pack(side="left", padx=6)
        tk.Button(btn_row, text="등록", command=submit, bg="#7a4aa0", fg="white",
                  font=("Arial", 11, "bold"), padx=20, pady=8).pack(side="left", padx=6)

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
        vsb = ttk.Scrollbar(self.help_win, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win_id, width=e.width))
        # 마우스 휠 — 윈도우/맥(<MouseWheel>, e.delta)과 리눅스(X11, <Button-4/5>) 둘 다 지원
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        canvas.bind("<Button-4>", lambda e: canvas.yview_scroll(-2, "units"))
        canvas.bind("<Button-5>", lambda e: canvas.yview_scroll(2, "units"))
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
        helps = [
            ("구조", "이 앱은 주문 전용입니다. 실시간 계산(지표/점수)과 계좌(잔고/포지션/강제청산)는 "
                    "trading_server.py 가 담당하고, 이 앱은 서버가 갱신하는 CSV를 읽어 표시하며 "
                    "주문 명령만 보냅니다. 이 앱을 꺼도 포지션은 서버가 계속 감시합니다. "
                    "서버는 빗썸과 업비트 두 거래소를 동시에 수집·채점합니다(아래 '거래소 토글' 참고)."),
            ("거래소 토글 (빗썸/업비트)", "코인탭 상단의 '빗썸 | 업비트' 버튼으로 어느 거래소 시세/점수를 볼지 "
                    "고를 수 있습니다. 선택한 거래소가 코인 목록·차트·롱숏 진입·청산 명령에 그대로 적용됩니다. "
                    "단, 포지션/잔고/손익은 거래소와 무관하게 하나로 공유됩니다 — 실제 체결가가 바이낸스 "
                    "USD 마크가격 기준이라(빗썸/업비트 KRW가는 표시·채점용), 어느 탭에서 진입하든 같은 계좌를 씁니다."),
            ("카드 색깔", "🟢 초록=롱 신호(long_score≥컷) | 🔴 빨강=숏 신호(short_score≥컷) | "
                        "🔵 파랑=매집(prepump_score≥80) | 🟣 보라=분산(preshort_score≥80) | "
                        "🟡 노랑=관심(65점은 넘었지만 진입 컷 미달, 참고만 할 것) | 흰색=신호 없음. "
                        "롱/숏이 매집/분산보다 우선순위 높음(둘 다 해당하면 롱/숏 색이 뜸)."),
            ("현재가 / 24시간 변동률", "바이낸스 선물 최종 체결가(USD) 기준. 괄호 안은 현재 선택된 거래소의 "
                    "원화 실시간 가격이고, 그 앞의 %는 24시간 변동률입니다(빗썸은 자체 24h 변동률 필드, "
                    "업비트는 전일 종가 대비 변동률). 바이낸스 미상장 코인은 N/A로 표시. 두 가격 차이는 김치프리미엄."),
            ("RSI", "상대강도지수(0~100). 30 이하 과매도(롱 유리), 70 이상 과매수(숏 유리). "
                    "괄호 안은 5캔들 전 대비 변화량(RSI△). 표시용이면서, 이제 '가격위치' 세부점수의 "
                    "반전가드에도 실제로 쓰입니다 — RSI가 이미 반대로 꺾이기 시작한 순간이면 그 구간 "
                    "만점을 안 줍니다(아래 '롱/숏 Score'의 RSI 반전가드 참고, 2026-07-19 추가)."),
            ("BB%", "볼린저밴드 폭 대비 가격 위치. 0%=하단 밴드, 100%=상단 밴드. "
                    "마이너스면 하단 밴드 아래로 이탈(극단적 과매도→롱 유리), "
                    "100% 초과면 상단 이탈(극단적 과매수→숏 유리)."),
            ("VolZ", "거래량 Z-스코어. 최근 20캔들 평균 대비 현재 캔들 거래량이 얼마나 튀는지. "
                     "롱/숏 Score 반영(2026-07-19 6단계 세분화): 3.5↑→15 / 2.5↑→13 / 2.0↑→11 / "
                     "1.5↑→8 / 1.2↑→5 / 그 외→3."),
            ("CVD", "누적 볼륨 델타(최근 2시간, OHLCV 추정). +면 매수세 우위, -면 매도세 우위. "
                    "롱/숏 Score에서는(2026-07-19 세분화) 방향 일치 여부뿐 아니라 거래량 대비 "
                    "강도까지 5단계로 채점됩니다(강한 일치 15 ~ 반대방향 0)."),
            ("30m%", "최근 30분간 가격 변동률(표시값은 순수 30분 기준 그대로). 롱/숏 Score의 "
                     "'모멘텀' 세부점수는(2026-07-19부터) 5분 40%+15분 30%+30분 30% 가중평균을 "
                     "쓰기 시작했습니다 — 5분·30분 단일값보다 신호가 덜 튐. 매집/분산 점수는 "
                     "그대로 순수 30분값을 씁니다."),
            ("ATR%", "1시간봉 기준 평균 변동폭(현재가 대비 %). v3에서는 등급 점수가 아니라 "
                     "24h거래대금과 묶어 '최소 유동성 필터'로만 쓰인다(통과 시 5점, 미통과 0점). "
                     "전 코인 평균 ATR%로 진입 컷과 시장 레짐(아래 참고)도 자동 조정됨(추세장 70 / 보통 75 / 횡보장 80)."),
            ("OI%", "미체결약정 1시간 변동률. 숏: ΔOI≥3%→15점, ≥1%→7점, 그 외 0점. "
                    "롱은 미반영(0점 고정) — 실측상 OI 급증이 롱보다 오히려 하락과 상관관계가 있었음."),
            ("L/S", "바이낸스 전체 계정 롱/숏 비율. 표시용이며 현재 어떤 점수 계산에도 반영되지 않습니다 "
                    "(롱/숏 Score, 매집/분산 점수 모두 미사용 — 참고 지표로만 보세요)."),
            ("Fund", "펀딩레이트(%). +면 롱 과열, -면 숏 과열. 표시용이며 어떤 점수에도 미반영."),
            ("롱/숏 Score", f"105점 만점(v3, 추세추종형): EMA삼중(20/60/120) 20 + 가격위치(RSI+BB% 결합) 20 "
                          f"+ CVD 15 + OI 15(롱은 0 고정) + VolZ 15 + 멀티TF모멘텀 15 + ATR/거래대금 유동성필터 5 "
                          f"→ /105×100 환산. L/S·Funding은 노이즈 유발로 계속 제외. "
                          f"EMA 기울기 반영(2026-07-19): 정배열이어도 EMA20 기울기가 뚜렷해야 만점(20), "
                          f"기울기 미미하면 16, 부분정배열 12, 그 외 낮게(단순 '정배열=거의 만점' 문제 완화). "
                          f"RSI 반전가드(2026-07-19): 가격위치 만점 구간이라도 RSI가 이미 반대로 "
                          f"꺾이는 중이면 그 구간 점수를 깎습니다(THETA/ZIL 실사례로 발견). "
                          f"RSI 극단값 페널티(2026-07-19): RSI 80↑(롱)/20↓(숏)이면 추가로 -5점. "
                          f"추세소진 페널티(2026-07-19): 직전 10개 캔들 동안 가격이 이미 크게 움직였으면 "
                          f"(롱 +10%↑, 숏 -10%↓ 등) 최대 -15점 감점 — 이미 추세가 끝나가는 뒤늦은 진입을 막기 "
                          f"위한 장치인데 계산식에 안 들어가 있던 걸 발견해서 추가함(QTUM이 직전 1시간 "
                          f"+9% 급등 직후 진입컷을 넘긴 실사례로 발견). "
                          f"과열 상한 캡: 7개 항목 중 5개 이상이 90%+ 만점이면 최종점수를 45점으로 "
                          f"강제 제한(전 항목 동시과열=반전 직전 패턴 확인됨). "
                          f"레짐/학습 배수: 시장 상태(상승·하락·횡보·고변동성)에 따른 자동 배수와, "
                          f"거래소별 실측 신호 결과로 자동 재학습되는 배수(v8, 초기값 1.0)가 곱해져 최종 반영됩니다. "
                          f"진입 컷은 시장 상태에 따라 70(추세장)~80(횡보장) 자동 조정(현재 {current_min_score}점, "
                          f"참고용 관심선 {watch_current_min_score}점), 컷 이상이면 카드 색칠 (초록=롱, 빨강=숏). "
                          f"빗썸/업비트는 컷·레짐·학습배수가 각각 독립적으로 계산됩니다."),
            ("매집(Pre-pump)", "prepump_score, 100점 만점, 장기 매집사이클 탐지형(추세추종인 롱/숏Score와 "
                    "완전히 다른 로직). 실측(37,608행) 재조정판: OI지속 8 + CVD누적 10 + EMA압축도 12 "
                    "+ ATR 8 + VolZ 8 + 박스위치(바닥권일수록 고득점) 17 + RSI(45~60구간) 12 "
                    "+ 최근상승패널티 25(이미 급등한 코인일수록 감점, 가장 강력한 신호). "
                    f"EMA가 '정배열'이면 오히려 감점(이미 매집 끝난 상태로 봄). 컷 {pp_current_min_score}점 이상 파랑."),
            ("분산(Pre-short)", "preshort_score, 100점 만점. 실측 재조정판: OI지속 8 + CVD누적 12 "
                    "+ EMA과이격도 8 + ATR 8 + VolZ 8 + 박스위치(신고가권일수록 고득점) 15 "
                    "+ RSI과열(70 이상) 35(단일 신호 중 가장 강력, 60분뒤 평균 −0.25% 실측 확인) "
                    f"+ 최근급등 6. 컷 {pp_current_min_score}점 이상 보라."),
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
            ("차트 보기", "포지션 카드 탭, 또는 코인탭 하단 '차트' 버튼(티커 입력칸 값 기준)으로 차트 팝업이 "
                    "뜹니다. 시간봉 버튼: 10분/30분/1시간/2시간/6시간/12시간(2026-07-19, 10분·30분 추가). "
                    "이 시간봉은 점수계산 기준(화면 상단 '기준봉' 버튼)과는 완전히 별개입니다."),
            ("기록 보기", "청산된 거래 내역을 봅니다. '청산 기록 초기화' 버튼(2026-07-19 추가)으로 이 "
                    "기록만 지울 수 있고, 보유 중인 포지션은 전혀 영향받지 않습니다(별도로 관리됨)."),
            ("카드 조작", "탭 1번: 티커 자동 입력. 더블탭: 상단 고정/해제. "
                        "고정 코인은 [코인명]으로 표시되고 항상 맨 위."),
            ("포지션 패널", "화면 하단 검은 패널. 한 번에 최대 2개 표시, 나머지는 위아래 드래그로 스크롤. "
                          "카드 탭 → 티커 자동 입력, Close 버튼 → 즉시 청산, "
                          "청산가는 증거금 소진 지점(진입가 ∓ 진입가/배율)."),
        ]
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