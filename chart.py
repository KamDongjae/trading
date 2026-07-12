import python_bithumb
import matplotlib
matplotlib.use("Agg")  # 화면 없이 이미지로만 그리는 백엔드 (Tkinter랑 별개, GUI 스레드 안 건드림)
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.patches import Rectangle
import pandas as pd
from datetime import datetime
import os
import io
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk

# ================== 설정 ==================
OUTPUT_DIR = "/storage/emulated/0/Documents/chart"  # 차트 출력 저장 경로. 안드로이드 Termux면 "/storage/emulated/0/Pictures"로 바꿀 것.

intervals = {
    "1분":   "minute1",
    "5분":   "minute5",
    "10분":  "minute10",
    "15분":  "minute15",
    "30분":  "minute30",
    "60분":  "minute60",
    "6시간": "minute360",
    "1일":   "day",
}
DRAG_THRESHOLD_PX = 50  # 이만큼 이상 좌우로 끌어야 다음/이전 차트로 넘어감

# matplotlib 기본 폰트(DejaVu Sans)는 한글이 없어서 네모로 깨진다. 나눔고딕/Noto Sans CJK
# 같은 한글 폰트가 시스템에 있으면 그걸 쓰고, 없으면 라벨 자체를 영문으로 자동 전환한다
# (깨진 글자로 보이는 것보단 영문이 낫다).
def _find_korean_font():
    candidates = ["NanumGothic", "NanumBarunGothic", "Noto Sans CJK KR", "Noto Sans KR", "Malgun Gothic"]
    available = {f.name for f in fm.fontManager.ttflist}
    for c in candidates:
        if c in available:
            return c
    return None

_KO_FONT = _find_korean_font()
if _KO_FONT:
    matplotlib.rcParams['font.family'] = _KO_FONT
    matplotlib.rcParams['axes.unicode_minus'] = False
    print(f"✅ matplotlib 한글 폰트: {_KO_FONT}")
else:
    print("⚠️ 한글 폰트를 못 찾아서 차트 라벨을 영문으로 표시합니다 "
          "(설치하려면: apt install fonts-nanum fonts-noto-cjk && fc-cache -fv)")

LBL = {
    "volume": "거래량" if _KO_FONT else "Volume",
    "rsi": "RSI(14)",
    "rsi_delta": "RSI Δ" if _KO_FONT else "RSI Delta",
    "chart_suffix": "봉 차트" if _KO_FONT else " chart",
}
# ===========================================

root = tk.Tk()
root.title("비트썸 코인 차트 생성기")
root.geometry("1000x760")

coin_var = tk.StringVar(value="BTC")
interval_var = tk.StringVar(value="60분")
count_var = tk.IntVar(value=200)

charts = []          # [{coin, interval_text, df, img(PIL, matplotlib로 그린 미리보기/PNG)}]
current_index = [0]

# ================== 지표 계산 (공통) ==================
def load_data(coin, interval_text, count):
    """빗썸 OHLCV + 지표(MA5/20/60, RSI, RSI Delta)를 붙인 DataFrame. 실패하면 None."""
    ticker = f"KRW-{coin}"
    interval = intervals[interval_text]
    df = python_bithumb.get_ohlcv(ticker=ticker, interval=interval, count=count)
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

# ================== matplotlib: 미리보기 + PNG 전용 ==================
def build_matplotlib_png(df, ticker, interval_text, dpi=130):
    """캔들+MA+거래량+RSI+RSI Delta를 matplotlib으로 그려서 PNG 바이트로 반환."""
    n = len(df)
    x = range(n)  # 캔들 간격을 균일하게 보이려고 정수 인덱스 사용 (거래 없는 구간 안 벌어지게)

    fig, axes = plt.subplots(
        4, 1, figsize=(14, 10), dpi=dpi, sharex=True,
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

    # ── 캔들스틱 ──
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
    ax_price.set_title(f'{ticker} {interval_text}{LBL["chart_suffix"]}', color='white', fontsize=13, pad=10)

    # ── 거래량 ──
    vol_colors = [up_color if r['close'] >= r['open'] else down_color for _, r in df.iterrows()]
    ax_vol.bar(x, df['volume'], color=vol_colors, width=width)
    ax_vol.set_ylabel(LBL["volume"], color='#cccccc', fontsize=9)

    # ── RSI ──
    ax_rsi.plot(x, df['RSI'], color='#ffa500', linewidth=1.3)
    ax_rsi.axhline(30, color='lime', linestyle='--', linewidth=0.8)
    ax_rsi.axhline(70, color='red', linestyle='--', linewidth=0.8)
    ax_rsi.set_ylabel(LBL["rsi"], color='#cccccc', fontsize=9)
    ax_rsi.set_ylim(0, 100)

    # ── RSI Delta ──
    ax_rd.plot(x, df['RSI_Delta'], color='#00ccff', linewidth=1.3)
    ax_rd.axhline(0, color='white', linestyle=':', linewidth=0.8)
    ax_rd.set_ylabel(LBL["rsi_delta"], color='#cccccc', fontsize=9)

    # x축 라벨: 너무 촘촘하면 겹치니 최대 10개 정도만 표시
    step = max(n // 10, 1)
    tick_idx = list(range(0, n, step))
    tick_labels = [df.index[i].strftime('%m-%d %H:%M') for i in tick_idx]
    ax_rd.set_xticks(tick_idx)
    ax_rd.set_xticklabels(tick_labels, rotation=30, ha='right')
    ax_price.set_xlim(-1, n)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', facecolor=fig.get_facecolor(), bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()

# ================== 입력확인: 미리보기만 (저장 안 함) ==================
def on_confirm():
    raw = coin_var.get()
    coins = [c.strip().upper() for c in raw.split(",") if c.strip()]
    if not coins:
        messagebox.showerror("오류", "코인 티커를 입력하세요 (쉼표로 여러 개, 예: BTC,ETH,XRP)")
        return
    interval_text = interval_var.get()
    try:
        count = int(count_var.get())
    except Exception:
        messagebox.showerror("오류", "데이터 갯수는 숫자로 입력하세요")
        return

    status_label.config(text=f"{len(coins)}개 코인 불러오는 중...")
    btn_confirm.config(state="disabled")
    root.update_idletasks()

    charts.clear()
    failed = []
    for coin in coins:
        try:
            df = load_data(coin, interval_text, count)
            if df is None:
                failed.append(coin)
                continue
            ticker = f"KRW-{coin}"
            png_bytes = build_matplotlib_png(df, ticker, interval_text)
            img = Image.open(io.BytesIO(png_bytes))
            img.load()
            charts.append({"coin": coin, "interval_text": interval_text, "df": df, "img": img})
        except Exception as e:
            print(f"{coin} 차트 생성 실패: {e}")
            failed.append(coin)

    btn_confirm.config(state="normal")
    if not charts:
        status_label.config(text="")
        messagebox.showerror("오류", "차트를 하나도 못 만들었습니다.\n실패: " + ", ".join(failed))
        return

    current_index[0] = 0
    show_chart(0)
    if failed:
        messagebox.showwarning("일부 실패", "다음 코인은 못 불러왔습니다: " + ", ".join(failed))

# ================== 차트 화면 표시 + 드래그 전환 ==================
def show_chart(idx):
    if not charts:
        return
    idx = idx % len(charts)
    current_index[0] = idx
    c = charts[idx]

    w = max(chart_label.winfo_width(), 200)
    h = max(chart_label.winfo_height(), 200)
    img = c['img'].copy()
    img.thumbnail((w, h))
    tkimg = ImageTk.PhotoImage(img)
    chart_label.image = tkimg  # GC 방지용 참조 유지
    chart_label.config(image=tkimg)

    nav = f" ({idx+1}/{len(charts)}, 드래그로 이동)" if len(charts) > 1 else ""
    status_label.config(text=f"{c['coin']} - {c['interval_text']}봉{nav}")

_drag_state = {"x": 0}
def on_drag_press(e):
    _drag_state["x"] = e.x
def on_drag_release(e):
    if len(charts) < 2:
        return
    dx = e.x - _drag_state["x"]
    if dx <= -DRAG_THRESHOLD_PX:
        show_chart(current_index[0] + 1)   # 왼쪽으로 끌면 다음
    elif dx >= DRAG_THRESHOLD_PX:
        show_chart(current_index[0] - 1)   # 오른쪽으로 끌면 이전

def on_resize(_event=None):
    if charts:
        show_chart(current_index[0])

# ================== 차트 출력: 실제 파일 저장 (CSV + PNG만) ==================
def on_export():
    if not charts:
        messagebox.showerror("오류", "먼저 '입력확인'으로 차트를 불러오세요")
        return
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    time_str = datetime.now().strftime("%Y%m%d-%H%M")
    saved, failed = [], []

    for c in charts:
        coin, interval_text, df = c['coin'], c['interval_text'], c['df']
        base = f"{coin.lower()}-{interval_text}-{time_str}"

        try:
            df.to_csv(os.path.join(OUTPUT_DIR, base + ".csv"), index=True,
                      index_label="datetime", encoding="utf-8-sig")
            saved.append(base + ".csv")
        except Exception as e:
            failed.append(f"{coin} CSV: {e}")

        try:
            c['img'].save(os.path.join(OUTPUT_DIR, base + ".png"))
            saved.append(base + ".png")
        except Exception as e:
            failed.append(f"{coin} PNG: {e}")

    msg = f"{len(charts)}개 코인 저장 완료\n경로: {OUTPUT_DIR}\n\n" + "\n".join(saved[:16])
    if len(saved) > 16:
        msg += f"\n... 외 {len(saved) - 16}개"
    if failed:
        msg += "\n\n실패:\n" + "\n".join(failed[:10])
    messagebox.showinfo("차트 출력 완료", msg)

def on_exit():
    root.destroy()

# ================== GUI 레이아웃 ==================
top = tk.Frame(root)
top.pack(side="top", fill="x", padx=8, pady=8)

tk.Label(top, text="코인 (쉼표로 여러 개)", font=("맑은고딕", 10)).grid(row=0, column=0, padx=(0, 4))
coin_entry = tk.Entry(top, textvariable=coin_var, width=28, font=("맑은고딕", 11))
coin_entry.grid(row=0, column=1, padx=(0, 10))

tk.Label(top, text="데이터 갯수", font=("맑은고딕", 10)).grid(row=0, column=2, padx=(0, 4))
count_entry = tk.Entry(top, textvariable=count_var, width=7, font=("맑은고딕", 11))
count_entry.grid(row=0, column=3, padx=(0, 10))

tk.Label(top, text="시간봉", font=("맑은고딕", 10)).grid(row=0, column=4, padx=(0, 4))
interval_combo = ttk.Combobox(top, textvariable=interval_var, values=list(intervals.keys()),
                               state="readonly", width=7, font=("맑은고딕", 10))
interval_combo.grid(row=0, column=5, padx=(0, 10))

btn_confirm = tk.Button(top, text="입력확인", font=("맑은고딕", 10, "bold"),
                         bg="#3366cc", fg="white", command=on_confirm)
btn_confirm.grid(row=0, column=6, padx=(0, 4))

# 차트 미리보기 영역 (드래그로 여러 코인 전환)
display_frame = tk.Frame(root, bg="black", bd=1, relief="sunken")
display_frame.pack(side="top", fill="both", expand=True, padx=8, pady=(0, 4))
chart_label = tk.Label(display_frame, bg="black",
                        text="코인 입력 후 '입력확인'을 누르면 여기에 미리보기가 표시됩니다",
                        fg="gray", font=("맑은고딕", 11))
chart_label.pack(fill="both", expand=True)
chart_label.bind("<ButtonPress-1>", on_drag_press)
chart_label.bind("<ButtonRelease-1>", on_drag_release)
display_frame.bind("<Configure>", on_resize)

status_label = tk.Label(root, text="", font=("맑은고딕", 9), fg="gray")
status_label.pack(side="top", pady=(0, 4))

bottom = tk.Frame(root)
bottom.pack(side="bottom", fill="x", padx=8, pady=8)
btn_export = tk.Button(bottom, text="차트 출력", font=("맑은고딕", 12, "bold"),
                        bg="#00cc66", fg="white", height=2, width=20, command=on_export)
btn_export.pack(side="left", padx=(0, 8))
btn_exit = tk.Button(bottom, text="종료", font=("맑은고딕", 12, "bold"),
                      bg="#cc3333", fg="white", height=2, width=12, command=on_exit)
btn_exit.pack(side="right")

root.mainloop()
