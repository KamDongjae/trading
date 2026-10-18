# -*- coding: utf-8 -*-
"""Pyramide 차트 뷰어 (Pydroid 3 단독 실행용)
- 서버 없이 빗썸/바이낸스 공개 API 직접 조회
- 차트(캔들+BB+EMA20/60/120, RSI, RSI Δ) + 가격/지표 카드만 표시 (점수·판정 없음)
- 필요: requests (Pydroid: pip install requests)"""
import tkinter as tk
import threading, queue, json, os, time
import requests

INTERVALS = ["10m", "30m", "1h", "2h", "6h", "12h", "24h", "48h"]
SHOW_N = 100           # 화면에 그릴 캔들 수
AUTO_SEC = 60          # 자동 새로고침 주기(초)
BG, FG, DIM = "#111111", "#e8e8e8", "#9aa0a6"
CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "viewer_cfg.json")
BH = "https://api.bithumb.com/public"
BN = "https://fapi.binance.com"


# ---------------- 데이터 ----------------
def bithumb_candles(t, iv):
    """[(ts_ms, o, h, l, c, v)] 시간순. 2h는 1h, 48h는 24h를 묶어 합성."""
    base = {"2h": "1h", "48h": "24h"}.get(iv, iv)
    r = requests.get(f"{BH}/candlestick/{t}_KRW/{base}", timeout=10).json()
    if r.get("status") != "0000":
        raise ValueError(f"{t} 캔들 없음 (빗썸 미상장?)")
    rows = [(int(d[0]), float(d[1]), float(d[3]), float(d[4]), float(d[2]), float(d[5])) for d in r["data"]]
    if iv not in ("2h", "48h"):
        return rows
    step = 2 * 3600e3 if iv == "2h" else 2 * 86400e3
    out, kst = [], 9 * 3600e3
    for row in rows:
        key = int((row[0] + kst) // step)
        if out and out[-1][0] == key:
            k, o, h, l, c, v = out[-1]
            out[-1] = (k, o, max(h, row[2]), min(l, row[3]), row[4], v + row[5])
        else:
            out.append((key, row[1], row[2], row[3], row[4], row[5]))
    return [(int(o[0] * step - kst),) + o[1:] for o in out]


def ema(vals, span):
    a, out, e = 2 / (span + 1), [], None
    for v in vals:
        e = v if e is None else a * v + (1 - a) * e
        out.append(e)
    return out


def rsi(closes, p=14):
    out, g, l = [None], None, None
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        up, dn = max(d, 0), max(-d, 0)
        g = up if g is None else g + (up - g) / p
        l = dn if l is None else l + (dn - l) / p
        out.append(100.0 if l == 0 else 100 - 100 / (1 + g / l))
    return out


def bollinger(closes, n=20, k=2):
    up, mid, lo = [], [], []
    for i in range(len(closes)):
        if i < n - 1:
            up.append(None); mid.append(None); lo.append(None); continue
        w = closes[i - n + 1:i + 1]
        m = sum(w) / n
        sd = (sum((x - m) ** 2 for x in w) / (n - 1)) ** 0.5
        up.append(m + k * sd); mid.append(m); lo.append(m - k * sd)
    return up, mid, lo


def build_series(rows):
    c = [r[4] for r in rows]
    rs = rsi(c)
    up, mid, lo = bollinger(c)
    return {"rows": rows, "rsi": rs,
            "rd": [None if i == 0 or rs[i - 1] is None or rs[i] is None else rs[i] - rs[i - 1] for i in range(len(rs))],
            "ema20": ema(c, 20), "ema60": ema(c, 60), "ema120": ema(c, 120) if len(c) >= 30 else [None] * len(c),
            "bbu": up, "bbm": mid, "bbl": lo}


def card_metrics(t, h1, m10):
    """카드 지표(서버와 같은 1h 기준 계산)."""
    out = {}
    c = [r[4] for r in h1]
    s = build_series(h1)
    out["rsi"], out["rd"] = s["rsi"][-1], s["rd"][-1]
    u, l = s["bbu"][-1], s["bbl"][-1]
    out["bb"] = (c[-1] - l) / (u - l) * 100 if u and l and u != l else 50.0
    vols = [r[5] for r in h1[-20:]]
    vm = sum(vols) / len(vols)
    vsd = (sum((v - vm) ** 2 for v in vols) / (len(vols) - 1)) ** 0.5
    out["volz"] = (vols[-1] - vm) / vsd if vsd > 0 else 0.0
    cvd = 0.0
    for _, o, h, lo, cl, v in h1[-2:]:
        cvd += (((cl - lo) - (h - cl)) / (h - lo) * v) if h != lo else 0
    out["cvd"] = cvd
    trs = [max(h1[i][2] - h1[i][3], abs(h1[i][2] - h1[i - 1][4]), abs(h1[i][3] - h1[i - 1][4])) for i in range(len(h1) - 14, len(h1))]
    out["atr"] = sum(trs) / 14 / c[-1] * 100
    out["chg30"] = (m10[-1][4] / m10[-4][4] - 1) * 100 if len(m10) >= 4 else 0.0
    out["ema20"], out["ema60"] = s["ema20"][-1], s["ema60"][-1]
    try:
        tk_ = requests.get(f"{BH}/ticker/{t}_KRW", timeout=8).json()["data"]
        out["price"] = float(tk_["closing_price"])
        out["chg24"] = float(tk_["fluctate_rate_24H"])
        out["vol24"] = float(tk_["acc_trade_value_24H"]) / 1e6
    except Exception:
        out["price"], out["chg24"], out["vol24"] = c[-1], None, None
    sym = f"{t}USDT"
    try:
        p = requests.get(f"{BN}/fapi/v1/premiumIndex", params={"symbol": sym}, timeout=8).json()
        out["usd"], out["fund"] = float(p["markPrice"]), float(p["lastFundingRate"]) * 100
        oi = requests.get(f"{BN}/futures/data/openInterestHist", params={"symbol": sym, "period": "5m", "limit": 13}, timeout=8).json()
        a, b = float(oi[0]["sumOpenInterest"]), float(oi[-1]["sumOpenInterest"])
        out["oi"] = (b - a) / a * 100 if a else 0.0
        ls = requests.get(f"{BN}/futures/data/globalLongShortAccountRatio", params={"symbol": sym, "period": "5m", "limit": 1}, timeout=8).json()
        out["ls"] = float(ls[-1]["longShortRatio"])
    except Exception:
        pass  # 바이낸스 선물 미상장
    return out


def fmt_p(v):
    if v is None:
        return "N/A"
    return f"{v:,.0f}" if v >= 100 else f"{v:,.2f}" if v >= 1 else f"{v:,.4f}"


def fmt_s(v, f="+.2f", unit=""):
    return "N/A" if v is None else f"{v:{f}}{unit}"


# ---------------- 화면 ----------------
class Viewer:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Pyramide 차트")
        self.root.configure(bg=BG)
        self.q = queue.Queue()
        self.data = None
        self.busy = False
        cfg = self._load_cfg()
        self.ticker, self.iv = cfg.get("ticker", "BTC"), cfg.get("iv", "1h")

        top = tk.Frame(self.root, bg=BG); top.pack(fill="x", padx=6, pady=(6, 2))
        self.ent = tk.Entry(top, font=("Arial", 14), width=8, bg="#22262b", fg="white", insertbackground="white")
        self.ent.insert(0, self.ticker)
        self.ent.pack(side="left", padx=(0, 4))
        self.ent.bind("<Return>", lambda e: self.search())
        tk.Button(top, text="조회", font=("Arial", 12, "bold"), bg="#1a7abf", fg="white", command=self.search).pack(side="left")
        tk.Button(top, text="↻", font=("Arial", 12, "bold"), bg="#2b2f36", fg="white", command=self.load).pack(side="left", padx=4)
        self.title = tk.Label(top, font=("Arial", 11, "bold"), bg=BG, fg="white", anchor="e")
        self.title.pack(side="right", fill="x", expand=True)

        bar = tk.Frame(self.root, bg=BG); bar.pack(fill="x", padx=6)
        self.btns = {}
        for i, iv in enumerate(INTERVALS):
            b = tk.Label(bar, text=iv, font=("Arial", 11, "bold"), pady=4, relief="raised", bd=1)
            b.grid(row=0, column=i, sticky="ew", padx=1)
            bar.columnconfigure(i, weight=1)
            b.bind("<ButtonRelease-1>", lambda e, v=iv: self.set_iv(v))
            self.btns[iv] = b

        self.cv_p = tk.Canvas(self.root, bg=BG, highlightthickness=0, height=420)
        self.cv_p.pack(fill="both", expand=True, padx=4, pady=(4, 0))
        self.cv_r = tk.Canvas(self.root, bg=BG, highlightthickness=0, height=120)
        self.cv_r.pack(fill="x", padx=4, pady=(4, 0))
        self.cv_d = tk.Canvas(self.root, bg=BG, highlightthickness=0, height=90)
        self.cv_d.pack(fill="x", padx=4, pady=(4, 0))
        card = tk.Frame(self.root, bg="#1a1d21", bd=1, relief="solid"); card.pack(fill="x", padx=4, pady=6)
        self.info = tk.Label(card, font=("monospace", 11), bg="#1a1d21", fg=FG, justify="left", anchor="w", text="불러오는 중…")
        self.info.pack(fill="x", padx=8, pady=6)
        card.bind("<Configure>", lambda e: self.info.config(wraplength=e.width - 16))
        for cv in (self.cv_p, self.cv_r, self.cv_d):
            cv.bind("<Configure>", lambda e: self.draw())

        self._hl()
        self.load()
        self.root.after(200, self._poll)
        self.root.after(AUTO_SEC * 1000, self._auto)

    def _load_cfg(self):
        try:
            with open(CFG, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_cfg(self):
        try:
            with open(CFG, "w", encoding="utf-8") as f:
                json.dump({"ticker": self.ticker, "iv": self.iv}, f)
        except Exception:
            pass

    def _hl(self):
        for iv, b in self.btns.items():
            on = iv == self.iv
            b.config(bg="#1a7abf" if on else "#2b2f36", fg="white" if on else DIM)

    def search(self):
        t = self.ent.get().strip().upper().replace("KRW-", "").replace("_KRW", "")
        if t:
            self.ticker = t
            self.root.focus_set()   # 키보드 내리기
            self.load()

    def set_iv(self, iv):
        self.iv = iv
        self._hl()
        self.load()

    def _auto(self):
        self.load(quiet=True)
        self.root.after(AUTO_SEC * 1000, self._auto)

    def load(self, quiet=False):
        if self.busy:
            return
        self.busy = True
        t, iv = self.ticker, self.iv
        if not quiet:
            self.title.config(text=f"{t} {iv} 조회 중…")
        threading.Thread(target=self._work, args=(t, iv), daemon=True).start()

    def _work(self, t, iv):
        try:
            h1 = bithumb_candles(t, "1h")
            rows = h1 if iv == "1h" else bithumb_candles(t, iv)
            m10 = rows if iv == "10m" else bithumb_candles(t, "10m")
            s = build_series(rows)
            s = {k: v[-SHOW_N:] for k, v in s.items()}
            self.q.put(("ok", t, iv, s, card_metrics(t, h1, m10)))
        except Exception as e:
            self.q.put(("err", t, iv, str(e), None))

    def _poll(self):
        try:
            while True:
                kind, t, iv, s, m = self.q.get_nowait()
                self.busy = False
                if kind == "err":
                    self.title.config(text=f"{t} 오류")
                    self.info.config(text=f"조회 실패: {s}")
                    continue
                if (t, iv) != (self.ticker, self.iv):
                    self.load(); continue   # 조회 중에 바뀐 경우 다시
                self.data = s
                self.title.config(text=f"{t}  {iv}  {time.strftime('%H:%M:%S')}")
                self.info.config(text=self.card_text(m))
                self._save_cfg()
                self.draw()
        except queue.Empty:
            pass
        self.root.after(200, self._poll)

    def card_text(self, m):
        usd = m.get("usd")
        usd_s = "" if usd is None else f" (USD {usd:,.4f})" if usd < 1 else f" (USD {usd:,.2f})"
        cvd = m["cvd"]
        cvd_s = f"{cvd:+,.0f}" if abs(cvd) >= 100 else f"{cvd:+.2f}"
        e20 = (m["price"] / m["ema20"] - 1) * 100 if m.get("ema20") else None
        return "\n".join([
            f"[가격] {fmt_p(m['price'])}원{usd_s}",
            f"       24h {fmt_s(m.get('chg24'), unit='%')}   30m {m['chg30']:+.2f}%",
            f"[위치] RSI {m['rsi']:.1f}({fmt_s(m['rd'], '+.1f')})   BB% {m['bb']:.0f}%   EMA20比 {fmt_s(e20, unit='%')}",
            f"[수급] VolZ {m['volz']:+.1f}   CVD {cvd_s}   OI {fmt_s(m.get('oi'), unit='%')}",
            f"       L/S {fmt_s(m.get('ls'), '.2f')}   Fund {fmt_s(m.get('fund'), '+.3f', '%')}",
            f"[변동] ATR {m['atr']:.2f}%   거래대금 {fmt_s(m.get('vol24'), ',.0f', 'M')}",
        ])

    # ---- 그리기 ----
    def draw(self):
        if not self.data:
            return
        self._draw_price(); self._draw_rsi(); self._draw_rd()

    def _frame(self, cv, pb=6):
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        n = len(self.data["rows"])
        pl, pr, pt = 6, 70, 6
        pw, ph = w - pl - pr, h - pt - pb
        if n == 0 or pw <= 0 or ph <= 0:
            return None
        cw = pw / n
        return w, pl, pr, pt, ph, cw, (lambda i: pl + i * cw + cw / 2)

    def _line(self, cv, vals, y, xf, color, width=1):
        pts = []
        for i, v in enumerate(vals):
            if v is not None:
                pts += [xf(i), y(v)]
        if len(pts) >= 4:
            cv.create_line(*pts, fill=color, width=width)

    def _draw_price(self):
        cv, d = self.cv_p, self.data
        f = self._frame(cv, pb=20)
        if not f:
            return
        w, pl, pr, pt, ph, cw, xf = f
        rows = d["rows"]
        vals = [x for r in rows for x in (r[2], r[3])] + [v for v in d["bbu"] + d["bbl"] if v is not None]
        hi, lo = max(vals), min(vals)
        rg = (hi - lo) or 1
        y = lambda p: pt + (hi - p) / rg * ph
        for fr in (0, .25, .5, .75, 1):
            yy = pt + fr * ph
            cv.create_line(pl, yy, w - pr, yy, fill="#2a2a2a")
            cv.create_text(w - pr + 4, yy, text=fmt_p(hi - fr * rg), fill="#cccccc", font=("Arial", 8), anchor="w")
        for k in ("bbu", "bbm", "bbl"):
            self._line(cv, d[k], y, xf, "#5a6a7a")
        for i, (_, o, h, l, c, _) in enumerate(rows):
            x, col = xf(i), "#00ff88" if c >= o else "#ff3838"
            cv.create_line(x, y(h), x, y(l), fill=col)
            t, b = y(max(o, c)), y(min(o, c))
            bw = max(cw * .6, 1)
            cv.create_rectangle(x - bw / 2, t, x + bw / 2, max(b, t + 1), fill=col, outline=col)
        for k, col in (("ema20", "#ffff00"), ("ema60", "#00ffff"), ("ema120", "#ff00ff")):
            self._line(cv, d[k], y, xf, col)
        last = rows[-1][4]
        cv.create_line(pl, y(last), w - pr, y(last), fill="#888888", dash=(2, 3))
        cv.create_text(w - pr + 4, y(last), text=fmt_p(last), fill="white", font=("Arial", 8, "bold"), anchor="w")
        ly = pt + 2
        for txt, col in (("EMA20", "#ffff00"), ("EMA60", "#00ffff"), ("EMA120", "#ff00ff"), ("BB", "#5a6a7a")):
            cv.create_text(pl + 4, ly, text=txt, fill=col, font=("Arial", 8, "bold"), anchor="nw"); ly += 12
        ay = pt + ph
        cv.create_line(pl, ay, w - pr, ay, fill="#444444")
        intra = self.iv in ("10m", "30m", "1h", "2h")
        fmt = "%H:%M" if self.iv in ("10m", "30m") else "%m.%d %H시" if intra else "%m.%d"
        step = max(len(rows) // 4, 1)
        for i in range(0, len(rows), step):
            lab = time.strftime(fmt, time.gmtime(rows[i][0] / 1000 + 9 * 3600))
            cv.create_text(xf(i), ay + 4, text=lab, fill="#999999", font=("Arial", 8), anchor="n")

    def _draw_rsi(self):
        cv, f = self.cv_r, self._frame(self.cv_r)
        if not f:
            return
        w, pl, pr, pt, ph, cw, xf = f
        y = lambda v: pt + (100 - max(0, min(100, v))) / 100 * ph
        for lv in (30, 50, 70):
            cv.create_line(pl, y(lv), w - pr, y(lv), fill="#3a3a3a")
            cv.create_text(w - pr + 4, y(lv), text=str(lv), fill="#999999", font=("Arial", 7), anchor="w")
        cv.create_text(pl + 4, pt + 2, text="RSI(14)", fill="#ffa500", font=("Arial", 8, "bold"), anchor="nw")
        self._line(cv, self.data["rsi"], y, xf, "#ffa500", 1.3)

    def _draw_rd(self):
        cv, f = self.cv_d, self._frame(self.cv_d)
        if not f:
            return
        w, pl, pr, pt, ph, cw, xf = f
        ds = [v for v in self.data["rd"] if v is not None]
        if not ds:
            return
        m = max(max(ds), -min(ds), 1)
        y = lambda v: pt + (m - v) / (2 * m) * ph
        cv.create_line(pl, y(0), w - pr, y(0), fill="#555555")
        cv.create_text(w - pr + 4, y(0), text="0", fill="#999999", font=("Arial", 7), anchor="w")
        cv.create_text(pl + 4, pt + 2, text="RSI Δ", fill="#00ccff", font=("Arial", 8, "bold"), anchor="nw")
        self._line(cv, self.data["rd"], y, xf, "#00ccff", 1.3)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    Viewer().run()
