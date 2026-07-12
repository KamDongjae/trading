import python_bithumb
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from datetime import datetime
import os
import io
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk

# ================== 설정 ==================
OUTPUT_DIR = "/root/Pictures"  # 차트 출력 저장 경로. 안드로이드 Termux면 "/storage/emulated/0/Pictures"로 바꿀 것.

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
# 한글은 fpdf2 기본 Helvetica 폰트로 못 그려서, PDF 제목에 쓸 봉 이름은 영문으로 매핑.
INTERVAL_EN = {
    "1분": "1min", "5분": "5min", "10분": "10min", "15분": "15min",
    "30분": "30min", "60분": "60min", "6시간": "6h", "1일": "1d",
}
DRAG_THRESHOLD_PX = 50  # 이만큼 이상 좌우로 끌어야 다음/이전 차트로 넘어감
# ===========================================

root = tk.Tk()
root.title("비트썸 코인 차트 생성기")
root.geometry("1000x760")

coin_var = tk.StringVar(value="BTC")
interval_var = tk.StringVar(value="60분")
count_var = tk.IntVar(value=200)

charts = []          # [{coin, interval_text, df, fig, img(PIL)}]
current_index = [0]

# ================== 지표/차트 계산 ==================
def build_chart(coin, interval_text, count):
    """빗썸 OHLCV를 받아 지표를 붙이고 plotly figure를 만든다. 실패하면 (None, None)."""
    ticker = f"KRW-{coin}"
    interval = intervals[interval_text]
    df = python_bithumb.get_ohlcv(ticker=ticker, interval=interval, count=count)
    if df is None or len(df) == 0:
        return None, None
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

    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.05,
                         row_heights=[0.50, 0.20, 0.15, 0.15],
                         subplot_titles=(f'{ticker} {interval_text}봉', '거래량', 'RSI (14)', 'RSI Delta'))

    fig.add_trace(go.Candlestick(x=df.index, open=df['open'], high=df['high'],
                                  low=df['low'], close=df['close'],
                                  increasing_line_color='#00ff88',
                                  decreasing_line_color='#ff3838'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA5'], line=dict(color='#ffff00', width=1.8), name='MA5'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA20'], line=dict(color='#00ffff', width=1.8), name='MA20'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA60'], line=dict(color='#ff00ff', width=1.8), name='MA60'), row=1, col=1)
    fig.add_trace(go.Bar(x=df.index, y=df['volume'], name='Volume', marker_color='#7777ff'), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['RSI'], line=dict(color='#ffa500', width=2), name='RSI'), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['RSI_Delta'], line=dict(color='#00ccff', width=2), name='RSI Delta'), row=4, col=1)
    fig.add_hline(y=30, line_dash="dash", line_color="lime", row=3, col=1)
    fig.add_hline(y=70, line_dash="dash", line_color="red", row=3, col=1)
    fig.add_hline(y=0, line_dash="dot", line_color="white", row=4, col=1)

    fig.update_layout(
        title=f'{ticker} {interval_text}봉 차트',
        template='plotly_dark',
        height=1000, width=1400,
        legend=dict(x=0.01, y=0.98, bgcolor='rgba(0,0,0,0.7)'),
        xaxis_rangeslider_visible=False,
    )
    return df, fig

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
            df, fig = build_chart(coin, interval_text, count)
            if df is None:
                failed.append(coin)
                continue
            png_bytes = fig.to_image(format="png", width=1400, height=1000, scale=1.3)
            img = Image.open(io.BytesIO(png_bytes))
            img.load()  # 바이트 버퍼는 여기서 닫혀도 되게 즉시 로드
            charts.append({"coin": coin, "interval_text": interval_text, "df": df, "fig": fig, "img": img})
        except Exception as e:
            print(f"{coin} 차트 생성 실패: {e}")
            failed.append(coin)

    btn_confirm.config(state="normal")
    if not charts:
        status_label.config(text="")
        messagebox.showerror("오류", "차트를 하나도 못 만들었습니다.\n실패: " + ", ".join(failed) +
                              "\n\n(kaleido 미설치면 미리보기 자체가 안 됩니다: pip install kaleido)")
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

# ================== 차트 출력: 실제 파일 저장 ==================
def on_export():
    if not charts:
        messagebox.showerror("오류", "먼저 '입력확인'으로 차트를 불러오세요")
        return
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    time_str = datetime.now().strftime("%Y%m%d-%H%M")
    saved, failed = [], []

    for c in charts:
        coin, interval_text, df, fig = c['coin'], c['interval_text'], c['df'], c['fig']
        base = f"{coin.lower()}-{interval_text}-{time_str}"

        try:
            fig.write_html(os.path.join(OUTPUT_DIR, base + ".html"))
            saved.append(base + ".html")
        except Exception as e:
            failed.append(f"{coin} HTML: {e}")

        try:
            df.to_csv(os.path.join(OUTPUT_DIR, base + ".csv"), index=True,
                      index_label="datetime", encoding="utf-8-sig")
            saved.append(base + ".csv")
        except Exception as e:
            failed.append(f"{coin} CSV: {e}")

        png_path = os.path.join(OUTPUT_DIR, base + ".png")
        try:
            fig.write_image(png_path, width=1400, height=1000, scale=2)
            saved.append(base + ".png")
        except Exception as e:
            failed.append(f"{coin} PNG(kaleido 필요): {e}")
            png_path = None

        if png_path:
            try:
                from fpdf import FPDF
                img_w_px, img_h_px = Image.open(png_path).size
                aspect = img_h_px / img_w_px
                pdf = FPDF(orientation='L', unit='mm', format='A4')
                pdf.set_margins(8, 8, 8)
                pdf.set_auto_page_break(False)
                pdf.add_page()
                page_w = pdf.w - pdf.l_margin - pdf.r_margin
                interval_en = INTERVAL_EN.get(interval_text, interval_text)
                pdf.set_font('Helvetica', 'B', 14)
                pdf.cell(page_w, 8, f"{coin} - {interval_en} - {time_str}", ln=1)
                img_w = page_w
                img_h = img_w * aspect
                max_h = pdf.h - pdf.t_margin - pdf.b_margin - 12
                if img_h > max_h:
                    img_h = max_h
                    img_w = img_h / aspect
                pdf.image(png_path, x=pdf.l_margin, y=pdf.get_y() + 2, w=img_w, h=img_h)
                pdf.output(os.path.join(OUTPUT_DIR, base + ".pdf"))
                saved.append(base + ".pdf")
            except ImportError:
                failed.append(f"{coin} PDF(fpdf2/pillow 필요)")
            except Exception as e:
                failed.append(f"{coin} PDF: {e}")

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
