# -*- coding: utf-8 -*-
"""
live_trader_gui.py — live_trader.py 의 창(GUI) 버전
==================================================
[2026-11-21 신규]  매매 규칙·주문·기록은 live_trader.py 를 그대로 가져다 쓴다(같은 파일들에 기록).
  - 창에서 레버리지 / 보유 시간 / 1회 진입 금액 / 강한 숏 2배 / 모의 모드를 고르고 [시작]
  - 보유 포지션 표(10초마다 갱신), 로그 창, 잔고 표시
  - [새 진입 멈춤/재개], [선택 정리], [전부 정리], [종료]
  - 수동 주문: 코인·롱/숏·금액·레버리지 → '봇이 시간 되면 정리' 또는 '내가 직접 정리'
  - 수동 포지션(직접 연 것) 표: 보기만 하고, [선택 수동 포지션 정리]를 눌러 확인할 때만 정리
실행:  python live_trader_gui.py   (또는 파일 더블클릭)
※ live_trader.py(콘솔판)와 동시에 켜지 마세요 — 같은 잠금 파일로 막혀 있음.
"""
import os
import sys
import queue
import threading
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox

import live_trader as L

APP_TITLE = "Pyramide 실전 자동매매"
REFRESH_MS = 10000

_logq = queue.Queue()
_orig_log = L.log


def _gui_log(msg):
    _orig_log(msg)                       # 콘솔·live_log.txt 에도 그대로
    _logq.put(f"[{datetime.now():%m-%d %H:%M:%S}] {msg}")


L.log = _gui_log


class App:
    def __init__(self, root):
        self.root = root
        self.tr = None
        self.th = None
        root.title(APP_TITLE)
        root.geometry("1000x830")
        root.minsize(820, 520)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("Treeview", rowheight=24)
        st.configure("Big.TButton", font=("Malgun Gothic", 10, "bold"))

        # ── 설정 ──
        top = ttk.LabelFrame(root, text=" 설정 (비워두면 일일 리포트 추천값) ", padding=8)
        top.pack(fill="x", padx=10, pady=(10, 4))
        self.v_lev = tk.StringVar(value=str(L.DEFAULT_LEVERAGE))
        self.v_hold = tk.StringVar(value=f"{L.DEFAULT_HOLD_HOURS:g}")
        self.v_amt = tk.StringVar(value=f"{L.DEFAULT_MARGIN_USDT:g}")
        self.v_amt_mode = tk.StringVar(value="usd")   # [2026-11-22] "usd" 고정 / "pct" 현금의 %
        self.v_pct = tk.StringVar(value=f"{L.DEFAULT_MARGIN_PCT:g}")
        self.v_seed = tk.StringVar(value="")   # [2026-11-22] 사용자 정의: 처음 시드(비우면 지난번 값/처음엔 지금 현금)
        self.v_strong = tk.BooleanVar(value=True)
        self.v_dry = tk.BooleanVar(value=True)
        r = ttk.Frame(top)
        r.pack(fill="x")
        for lbl, var, unit, w in (("레버리지", self.v_lev, "배", 5), ("보유 시간", self.v_hold, "시간", 6)):
            ttk.Label(r, text=lbl).pack(side="left")
            e = ttk.Entry(r, textvariable=var, width=w, justify="right")
            e.pack(side="left", padx=(4, 2))
            ttk.Label(r, text=unit).pack(side="left", padx=(0, 16))
        ttk.Label(r, text="1회 진입").pack(side="left")
        self.rb_usd = ttk.Radiobutton(r, text="고정", variable=self.v_amt_mode, value="usd")
        self.rb_usd.pack(side="left", padx=(4, 0))
        ttk.Entry(r, textvariable=self.v_amt, width=7, justify="right").pack(side="left", padx=(2, 2))
        ttk.Label(r, text="USDT").pack(side="left", padx=(0, 6))
        self.rb_pct = ttk.Radiobutton(r, text="현금의", variable=self.v_amt_mode, value="pct")
        self.rb_pct.pack(side="left")
        ttk.Entry(r, textvariable=self.v_pct, width=5, justify="right").pack(side="left", padx=(2, 2))
        ttk.Label(r, text="%").pack(side="left", padx=(0, 8))
        self.rb_rule = ttk.Radiobutton(r, text="사용자 정의", variable=self.v_amt_mode, value="rule")
        self.rb_rule.pack(side="left")
        self.btn_rule = ttk.Button(r, text="규칙 편집·검사", command=self.edit_rule)
        self.btn_rule.pack(side="left", padx=(2, 16))
        self.cb_strong = ttk.Checkbutton(r, text=f"강한 숏(4h예상 {L.STRONG_SHORT_EXP4}% 이하) 진입 금액 {L.STRONG_MULT:g}배",
                                         variable=self.v_strong)
        self.cb_strong.pack(side="left", padx=(0, 16))
        self.cb_dry = ttk.Checkbutton(r, text="모의 모드(주문 안 냄)", variable=self.v_dry)
        self.cb_dry.pack(side="left")
        r2 = ttk.Frame(top)
        r2.pack(fill="x", pady=(8, 0))
        self.btn_start = ttk.Button(r2, text="▶ 시작", style="Big.TButton", command=self.start)
        self.btn_start.pack(side="left")
        self.btn_pause = ttk.Button(r2, text="⏸ 새 진입 멈춤", command=self.toggle_pause, state="disabled")
        self.btn_pause.pack(side="left", padx=6)
        self.btn_close = ttk.Button(r2, text="선택 정리", command=self.close_selected, state="disabled")
        self.btn_close.pack(side="left", padx=6)
        self.btn_closeall = ttk.Button(r2, text="전부 정리", command=self.close_all, state="disabled")
        self.btn_closeall.pack(side="left", padx=6)
        self.lbl_state = ttk.Label(r2, text="대기 중", foreground="gray")
        self.lbl_state.pack(side="right")
        ttk.Label(top, text="조건: 바이낸스 선물 코인만 · 같은 코인 보유 중 건너뜀 · 하락장 롱 제외 · 격리 마진 · "
                            "정한 시간이 지나면 시장가 정리(중간 손절·익절 없음)",
                  foreground="gray").pack(anchor="w", pady=(6, 0))

        # ── 잔고 ──
        mid = ttk.Frame(root)
        mid.pack(fill="x", padx=10)
        self.lbl_bal = ttk.Label(mid, text="잔고: —", font=("Malgun Gothic", 11, "bold"))
        self.lbl_bal.pack(side="left")
        self.lbl_cnt = ttk.Label(mid, text="")
        self.lbl_cnt.pack(side="right")

        # ── 포지션 표 ──
        pf = ttk.LabelFrame(root, text=" 보유 포지션 (봇) ", padding=4)
        pf.pack(fill="both", expand=False, padx=10, pady=4)
        cols = ("sym", "dir", "lev", "margin", "entry", "mark", "ret", "held")
        heads = ("코인", "방향", "레버", "진입금액", "진입가", "현재가", "증거금 수익", "보유/정리")
        widths = (120, 60, 50, 80, 100, 100, 100, 110)
        self.tv = ttk.Treeview(pf, columns=cols, show="headings", height=6, selectmode="extended")
        for c, h, w in zip(cols, heads, widths):
            self.tv.heading(c, text=h)
            self.tv.column(c, width=w, anchor="center")
        self.tv.tag_configure("pos", foreground="#1a8f3c")
        self.tv.tag_configure("neg", foreground="#c0392b")
        sb1 = ttk.Scrollbar(pf, orient="vertical", command=self.tv.yview)   # 목록 많아지면 스크롤
        self.tv.configure(yscrollcommand=sb1.set)
        sb1.pack(side="right", fill="y")
        self.tv.pack(fill="both", expand=True)

        # ── 수동 주문 (바이낸스 USDT-M 무기한 선물) ──
        of = ttk.LabelFrame(root, text=" 수동 주문 (바이낸스 USDT-M 무기한 선물 · 시장가 · 격리 마진) ", padding=6)
        of.pack(fill="x", padx=10, pady=4)
        self.o_coin = tk.StringVar(value="")
        self.o_dir = tk.StringVar(value="SHORT")
        self.o_amt = tk.StringVar(value=f"{L.DEFAULT_MARGIN_USDT:g}")
        self.o_lev = tk.StringVar(value=str(L.DEFAULT_LEVERAGE))
        self.o_mode = tk.StringVar(value="auto")
        ttk.Label(of, text="코인").pack(side="left")
        ttk.Entry(of, textvariable=self.o_coin, width=10).pack(side="left", padx=(4, 2))
        ttk.Label(of, text="USDT", foreground="gray").pack(side="left", padx=(0, 10))
        ttk.Radiobutton(of, text="롱", variable=self.o_dir, value="LONG").pack(side="left")
        ttk.Radiobutton(of, text="숏", variable=self.o_dir, value="SHORT").pack(side="left", padx=(0, 10))
        ttk.Label(of, text="금액").pack(side="left")
        ttk.Entry(of, textvariable=self.o_amt, width=7, justify="right").pack(side="left", padx=(4, 2))
        ttk.Label(of, text="USDT").pack(side="left", padx=(0, 10))
        ttk.Label(of, text="레버리지").pack(side="left")
        ttk.Entry(of, textvariable=self.o_lev, width=4, justify="right").pack(side="left", padx=(4, 2))
        ttk.Label(of, text="배").pack(side="left", padx=(0, 10))
        ttk.Radiobutton(of, text="봇이 시간 되면 정리", variable=self.o_mode, value="auto").pack(side="left")
        ttk.Radiobutton(of, text="내가 직접 정리", variable=self.o_mode, value="keep").pack(side="left", padx=(0, 10))
        self.btn_order = ttk.Button(of, text="주문", style="Big.TButton", command=self.manual_order, state="disabled")
        self.btn_order.pack(side="right")

        # ── 수동 포지션(봇이 안 연 것): 보기만, 버튼 누를 때만 정리 ──
        mf = ttk.LabelFrame(root, text=" 수동 포지션 (바이낸스 앱 등에서 직접 연 것 — 봇이 자동으로 건드리지 않음) ", padding=4)
        mf.pack(fill="both", expand=False, padx=10, pady=4)
        mcols = ("sym", "dir", "qty", "entry", "mark", "upnl")
        mheads = ("코인", "방향", "수량", "진입가", "현재가", "미실현 손익(USDT)")
        mwidths = (130, 70, 110, 110, 110, 140)
        mrow = ttk.Frame(mf)
        mrow.pack(fill="x")
        self.tvm = ttk.Treeview(mrow, columns=mcols, show="headings", height=3, selectmode="browse")
        for c, h, w in zip(mcols, mheads, mwidths):
            self.tvm.heading(c, text=h)
            self.tvm.column(c, width=w, anchor="center")
        self.tvm.tag_configure("pos", foreground="#1a8f3c")
        self.tvm.tag_configure("neg", foreground="#c0392b")
        sb2 = ttk.Scrollbar(mrow, orient="vertical", command=self.tvm.yview)
        self.tvm.configure(yscrollcommand=sb2.set)
        self.tvm.pack(side="left", fill="x", expand=True)
        sb2.pack(side="left", fill="y")
        self.btn_mclose = ttk.Button(mrow, text="선택 수동\n포지션 정리", command=self.close_manual, state="disabled")
        self.btn_mclose.pack(side="left", padx=(8, 0), fill="y")
        self._manual = {}

        # ── 로그 ──
        lf = ttk.LabelFrame(root, text=" 로그 ", padding=4)
        lf.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.txt = tk.Text(lf, height=10, wrap="none", font=("Consolas", 9), bg="#0f1b2b", fg="#e6edf6",
                           insertbackground="#e6edf6")
        sb = ttk.Scrollbar(lf, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(fill="both", expand=True)
        for tag, col in (("ok", "#6fdc8c"), ("skip", "#9aa7b8"), ("err", "#ff7b72"), ("strong", "#ffcc66"),
                         ("close", "#79c0ff")):
            self.txt.tag_configure(tag, foreground=col)

        self.root.after(300, self.pump_log)

    # ---------- 공용 ----------
    def _num(self, var, default, cast, ok, name):
        s = var.get().strip()
        if not s:
            return cast(default)
        try:
            v = cast(float(s))
        except ValueError:
            v = None
        if v is None or not ok(v):
            raise ValueError(name)
        return v

    def _bg(self, fn, *a):
        threading.Thread(target=fn, args=a, daemon=True).start()

    # ---------- [2026-11-22] 사용자 정의 진입 금액 ----------
    def _read_rule(self):
        try:
            with open(L.RULE_FILE, encoding="utf-8") as f:
                return f.read()
        except Exception:
            return L.sizing_rule.EXAMPLE if L.sizing_rule else ""

    def edit_rule(self):
        if not L.sizing_rule:
            messagebox.showerror("사용자 정의", "sizing_rule.py 가 trading 폴더에 없어서 못 씀")
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("사용자 정의 진입 금액 (C언어 형식)")
        dlg.transient(self.root)
        f = ttk.Frame(dlg, padding=10)
        f.pack(fill="both", expand=True)
        r = ttk.Frame(f)
        r.pack(fill="x")
        ttk.Label(r, text="처음 시드 start_seed").pack(side="left")
        ttk.Entry(r, textvariable=self.v_seed, width=10, justify="right").pack(side="left", padx=4)
        ttk.Label(r, text="USDT (비우면 지난번 값, 처음이면 시작할 때 현금)").pack(side="left")
        txt = tk.Text(f, width=70, height=12, font=("Consolas", 10), undo=True)
        txt.pack(fill="both", expand=True, pady=6)
        txt.insert("1.0", self._read_rule())
        ttk.Label(f, foreground="gray", justify="left", text=(
            "변수: start_seed(처음 시드) current_seed(지금 사용 가능 USDT, 모의는 가상 현금) open_positions is_short strong_short leverage\n"
            "결과: position_size = 금액;  (0 이하면 진입 안 함)   함수: int round min max abs   연산: + - * / % == != < <= > >= && || !\n"
            "강한 숏 2배 체크가 켜져 있으면 규칙 결과에도 2배가 곱해짐")).pack(anchor="w")
        res = ttk.Label(f, text="", justify="left")
        res.pack(anchor="w", pady=(6, 0))

        def do_check():
            sv = self.v_seed.get().strip()
            try:
                sd = float(sv) if sv else 1000.0
            except ValueError:
                res.configure(text="❌ 처음 시드는 숫자로", foreground="#e04040")
                return False
            try:
                lv = int(float(self.v_lev.get() or L.DEFAULT_LEVERAGE))
            except ValueError:
                lv = L.DEFAULT_LEVERAGE
            ok, msg, rows = L.sizing_rule.check(txt.get("1.0", "end"), sd, lv)
            detail = "\n".join(f"  {d}: current_seed {cur:,.0f} → position_size {ps:g}" for d, cur, ps in rows)
            res.configure(text=("✅ " if ok else "❌ ") + msg + ("\n" + detail if detail else ""),
                          foreground="#1a8f3c" if ok else "#e04040")
            return ok

        def do_save():
            if not do_check():
                return
            with open(L.RULE_FILE, "w", encoding="utf-8") as fh:
                fh.write(txt.get("1.0", "end").strip() + "\n")
            self.v_amt_mode.set("rule")
            dlg.destroy()

        b = ttk.Frame(f)
        b.pack(fill="x", pady=(8, 0))
        ttk.Button(b, text="저장(검사 통과해야 저장)", command=do_save).pack(side="right")
        ttk.Button(b, text="문법 검사", command=do_check).pack(side="right", padx=6)
        ttk.Button(b, text="예시로 되돌리기",
                   command=lambda: (txt.delete("1.0", "end"), txt.insert("1.0", L.sizing_rule.EXAMPLE))).pack(side="left")

    # ---------- 시작 ----------
    def start(self):
        try:
            lev = self._num(self.v_lev, L.DEFAULT_LEVERAGE, int, lambda v: 1 <= v <= 20, "레버리지(1~20)")
            hold = self._num(self.v_hold, L.DEFAULT_HOLD_HOURS, float, lambda v: 0.1 <= v <= 240, "보유 시간(0.1~240)")
            amt = self._num(self.v_amt, L.DEFAULT_MARGIN_USDT, float, lambda v: v > 0, "진입 금액(0보다 큼)")
            pct = None
            rule = None
            seed = None
            if self.v_amt_mode.get() == "pct":
                pct = self._num(self.v_pct, L.DEFAULT_MARGIN_PCT, float, lambda v: 0 < v <= 100, "현금의 %(0~100)")
            elif self.v_amt_mode.get() == "rule":
                if not L.sizing_rule:
                    raise ValueError("sizing_rule.py 가 없어서 사용자 정의")
                rule = self._read_rule()
                sv = self.v_seed.get().strip()
                seed = float(sv) if sv else None
                if seed is not None and seed <= 0:
                    raise ValueError("처음 시드(0보다 큼)")
                ok, msg, _ = L.sizing_rule.check(rule, seed or 1000, lev)
                if not ok:
                    messagebox.showwarning("사용자 정의 오류", f"규칙이 문법 검사를 통과 못 했어:\n{msg}\n\n'규칙 편집·검사'에서 고쳐줘.")
                    return
        except ValueError as e:
            messagebox.showwarning("입력 오류", f"{e} 값을 확인해 줘.")
            return
        dry, strong = self.v_dry.get(), self.v_strong.get()
        if lev >= 5 and not messagebox.askyesno("레버리지 확인",
                                                f"{lev}배는 1년 백테스트에서 청산이 많았어(5배 44건, 3배 16건).\n그래도 시작할까?"):
            return
        if not dry:
            msg = (f"실거래입니다. 실제 돈으로 주문이 나갑니다.\n\n"
                   f"레버리지 {lev}배 · {hold:g}시간 뒤 정리 · 1회 "
                   f"{'사용자 정의 규칙' if rule else f'현금(사용 가능 USDT)의 {pct:g}%' if pct else f'{amt:g} USDT'}"
                   f"{f' (강한 숏 ×{L.STRONG_MULT:g})' if strong else ''}\n\n시작할까?")
            if not messagebox.askyesno("실거래 시작", msg, icon="warning"):
                return
        try:
            L.check_gitignore()
        except SystemExit:
            messagebox.showerror("중단", ".gitignore 에 binance_api_key.txt 가 없어서 시작하지 않았어.\n"
                                         "(API 키가 깃허브에 올라갈 수 있음) .gitignore 에 한 줄 추가 후 다시 시작해.")
            return
        try:
            L.acquire_lock()
        except SystemExit:
            messagebox.showerror("중단", "live_trader 가 이미 실행 중이야(콘솔판 포함). 두 개 켜면 주문이 두 번 나가.")
            return
        self.set_inputs("disabled")
        self.lbl_state.configure(text="연결 중…", foreground="#c9a800")
        self._bg(self._start_worker, lev, hold, amt, dry, strong, pct, rule, seed)

    def _start_worker(self, lev, hold, amt, dry, strong, pct=None, rule=None, seed=None):
        try:
            tr = L.Trader(lev, hold, amt, dry=dry, strong_x2=strong, margin_pct=pct, margin_rule=rule, start_seed=seed)
            tr.startup()
        except SystemExit:
            L.release_lock()
            self.root.after(0, lambda: self._start_failed("API 키를 못 읽었어.\nD:\\binance_api_key.txt 에 첫 줄 API 키, 둘째 줄 시크릿 키를 넣어 줘."))
            return
        except Exception as e:
            L.release_lock()
            self.root.after(0, lambda: self._start_failed(f"바이낸스 연결 실패: {e}"))
            return
        self.tr = tr
        L.log(f"▶️  시작 — {lev}배 · {hold:g}h · {tr.amount_text()}{' (강한 숏 ×2)' if strong else ''} · {'모의' if dry else '실거래'}")
        self.th = threading.Thread(target=tr.run, daemon=True)
        self.th.start()
        self.root.after(0, self._started)

    def _start_failed(self, msg):
        self.set_inputs("normal")
        self.lbl_state.configure(text="대기 중", foreground="gray")
        messagebox.showerror("시작 실패", msg)

    def _started(self):
        dry = self.tr.dry
        self.lbl_state.configure(text="● 모의 실행 중" if dry else "● 실거래 실행 중",
                                 foreground="#2f7fbf" if dry else "#1a8f3c")
        for b in (self.btn_pause, self.btn_close, self.btn_closeall, self.btn_mclose, self.btn_order):
            b.configure(state="normal")
        self.btn_start.configure(text="■ 종료", command=self.stop)
        self.btn_start.configure(state="normal")
        self.refresh()

    def set_inputs(self, state):
        self.btn_start.configure(state=state)
        for cb in (self.cb_strong, self.cb_dry, self.rb_usd, self.rb_pct, self.rb_rule, self.btn_rule):
            cb.configure(state=state)

    # ---------- 동작 ----------
    def toggle_pause(self):
        if not self.tr:
            return
        self.tr.paused = not self.tr.paused
        L.log("⏸  새 진입 멈춤" if self.tr.paused else "▶️  새 진입 다시 시작")
        self.btn_pause.configure(text="▶ 새 진입 재개" if self.tr.paused else "⏸ 새 진입 멈춤")

    def close_selected(self):
        if not self.tr:
            return
        sel = self.tv.selection()
        if not sel:
            messagebox.showinfo("선택", "정리할 포지션을 표에서 골라줘.")
            return
        if not messagebox.askyesno("정리", f"{len(sel)}개 포지션을 지금 시장가로 정리할까?"):
            return
        self._bg(self._close_ids, list(sel), "수동 정리(창)")

    def close_all(self):
        if not self.tr:
            return
        ids = list(self.tr.open_trades().keys())
        if not ids:
            return
        if not messagebox.askyesno("전부 정리", f"봇 포지션 {len(ids)}개를 전부 시장가로 정리할까?", icon="warning"):
            return
        self._bg(self._close_ids, ids, "수동 전체 정리(창)")

    def _close_ids(self, ids, why):
        for eid in ids:
            t = self.tr.open_trades().get(eid)
            if t:
                try:
                    self.tr.close_trade(eid, t, why)
                except Exception as e:
                    L.log(f"⚠️ {t.get('symbol')} 정리 오류: {e}")
        self.tr.save_state()
        self.root.after(0, self.refresh)

    def manual_order(self):
        if not self.tr:
            return
        coin = self.o_coin.get().strip().upper()
        if not coin:
            messagebox.showinfo("수동 주문", "코인 이름을 넣어줘 (예: ZIL, BTC).")
            return
        try:
            amt = float(self.o_amt.get())
            lev = int(float(self.o_lev.get()))
            if amt <= 0 or not 1 <= lev <= 20:
                raise ValueError
        except ValueError:
            messagebox.showwarning("입력 오류", "금액은 0보다 큰 숫자, 레버리지는 1~20.")
            return
        d, keep = self.o_dir.get(), self.o_mode.get() == "keep"
        how = "내가 직접 정리(수동 포지션 표로 감)" if keep else f"봇이 {self.tr.hold_h:g}시간 뒤 자동 정리"
        msg = (f"{'[모의] ' if self.tr.dry else '실제 주문 — '}바이낸스 USDT 선물 시장가\n\n"
               f"{coin} {'롱' if d == 'LONG' else '숏'} · {lev}배 · 증거금 {amt:g} USDT (명목 {amt * lev:g} USDT)\n{how}\n\n주문할까?")
        if not messagebox.askyesno("수동 주문 확인", msg, icon="warning"):
            return
        self._bg(self._order_worker, coin, d, amt, lev, not keep)

    def _order_worker(self, coin, d, amt, lev, managed):
        try:
            self.tr.manual_open(coin, d, amt, lev, managed=managed)
            self.tr.save_state()
        except Exception as e:
            L.log(f"❌ 수동 주문 오류: {e}")
        self.root.after(0, self.refresh_once)

    def close_manual(self):
        if not self.tr:
            return
        sel = self.tvm.selection()
        if not sel or sel[0] not in self._manual:
            messagebox.showinfo("선택", "정리할 수동 포지션을 표에서 골라줘.")
            return
        p = self._manual[sel[0]]
        msg = (f"직접 연 수동 포지션이야.\n\n{p['symbol']} {p['side']} 수량 {p['qty']:g}\n"
               f"미실현 손익 약 {p['upnl']:+.2f} USDT\n\n지금 시장가로 정리할까?")
        if not messagebox.askyesno("수동 포지션 정리", msg, icon="warning"):
            return
        self._bg(self._close_manual_worker, p["symbol"], p["side"])

    def _close_manual_worker(self, sym, side):
        try:
            self.tr.close_manual(sym, side)
        except Exception as e:
            L.log(f"⚠️ {sym} 수동 정리 오류: {e}")
        self.root.after(0, self.refresh_once)

    def refresh_once(self):
        if self.tr:
            self._bg(self._refresh_worker)

    def stop(self):
        if not messagebox.askyesno("종료", "자동매매를 끌까?\n보유 포지션은 거래소에 그대로 두고, 다시 켜면 이어서 정리 시간을 관리해."):
            return
        self._shutdown()
        self.btn_start.configure(text="▶ 시작", command=self.start)
        self.set_inputs("normal")
        for b in (self.btn_pause, self.btn_close, self.btn_closeall, self.btn_mclose, self.btn_order):
            b.configure(state="disabled")
        self.lbl_state.configure(text="대기 중", foreground="gray")

    def _shutdown(self):
        if self.tr:
            self.tr.stop = True
            if self.th:
                self.th.join(timeout=3)
            self.tr.save_state()
            L.log("⏹  종료 — 보유 포지션은 거래소에 그대로 있고, 다시 켜면 이어서 정리 시간을 관리합니다")
            self.tr = None
            L.release_lock()

    def on_close(self):
        if self.tr:
            if not messagebox.askyesno("닫기", "자동매매가 실행 중이야. 끄고 닫을까?\n(보유 포지션은 거래소에 그대로)"):
                return
            self._shutdown()
        self.root.destroy()

    # ---------- 갱신 ----------
    def refresh(self):
        if not self.tr:
            return
        self._bg(self._refresh_worker)
        self.root.after(REFRESH_MS, self.refresh)

    def _refresh_worker(self):
        tr = self.tr
        if not tr:
            return
        rows = []
        for eid, t in list(tr.open_trades().items()):
            try:
                mark = tr.bn.mark_price(t["symbol"])
            except Exception:
                mark = None
            sign = 1 if t["direction"] == "LONG" else -1
            ret = (mark / float(t["entry_price"]) - 1) * 100 * sign * float(t["leverage"]) if mark else None
            held = (datetime.now() - datetime.strptime(t["entry_time"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
            rows.append((eid, t, mark, ret, held))
        bal, manual = None, []
        if not tr.dry:
            try:
                bal = tr.bn.usdt_balance()
            except Exception:
                pass
            try:
                manual = tr.manual_positions()
            except Exception:
                manual = None
        self.root.after(0, lambda: self._refresh_ui(rows, bal, manual))

    def _refresh_ui(self, rows, bal, manual=None):
        self.tv.delete(*self.tv.get_children())
        used = 0.0
        for eid, t, mark, ret, held in rows:
            used += float(t["margin"])
            tag = () if ret is None else (("pos",) if ret >= 0 else ("neg",))
            self.tv.insert("", "end", iid=eid, tags=tag, values=(
                t["symbol"], t["direction"], f"{t['leverage']}배", f"${float(t['margin']):g}",
                f"{float(t['entry_price']):g}", "—" if mark is None else f"{mark:g}",
                "—" if ret is None else f"{ret:+.1f}%", f"{held:.1f} / {float(t['hold_hours']):g}h"))
        if bal:
            self.lbl_bal.configure(text=f"잔고: 사용 가능 {bal[0]:,.2f} / 총 {bal[1]:,.2f} USDT")
        elif self.tr and self.tr.dry:
            self.lbl_bal.configure(text="잔고: (모의 모드)")
        if manual is not None:
            self.tvm.delete(*self.tvm.get_children())
            self._manual = {}
            for i, p in enumerate(manual):
                iid = f"m{i}"
                self._manual[iid] = p
                self.tvm.insert("", "end", iid=iid, tags=("pos",) if p["upnl"] >= 0 else ("neg",), values=(
                    p["symbol"], p["side"], f"{p['qty']:g}", f"{p['entry']:g}", f"{p['mark']:g}", f"{p['upnl']:+.2f}"))
            if self.tr and self.tr.dry:
                self.tvm.insert("", "end", values=("(모의 모드에선 표시 안 함)", "", "", "", "", ""))
        self.lbl_cnt.configure(text=f"보유 {len(rows)}개 · 증거금 {used:,.0f} USDT · {datetime.now():%H:%M:%S} 갱신")

    def pump_log(self):
        try:
            while True:
                line = _logq.get_nowait()
                tag = ("ok" if "✅" in line else "strong" if "💪" in line else "skip" if "⏭" in line
                       else "close" if "🔚" in line else "err" if ("❌" in line or "⚠️" in line) else None)
                self.txt.insert("end", line + "\n", tag or ())
                self.txt.see("end")
                if int(self.txt.index("end-1c").split(".")[0]) > 2000:
                    self.txt.delete("1.0", "500.0")
        except queue.Empty:
            pass
        self.root.after(300, self.pump_log)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
