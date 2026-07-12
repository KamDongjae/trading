import python_bithumb
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from datetime import datetime
import os
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk   # ← 이미지 표시용

root = tk.Tk()
root.title("비트썸 멀티 코인 차트")
root.geometry("1100x800")
root.resizable(True, True)

coins_entry = tk.StringVar(value="BTC")
interval_var = tk.StringVar(value="60분")
count_var = tk.IntVar(value=200)

intervals = {
    "1분": "minute1", "5분": "minute5", "10분": "minute10", "15분": "minute15",
    "30분": "minute30", "60분": "minute60", "6시간": "minute360", "1일": "day"
}

charts = []           # 저장된 (코인, png_path)
current_index = 0
photo_image = None    # Tkinter 이미지 객체

def make_and_save_image(ticker, interval_text, count, save_path):
    interval = intervals[interval_text]
    df = python_bithumb.get_ohlcv(ticker=ticker, interval=interval, count=count)
    df.index = pd.to_datetime(df.index)

    df['MA5']  = df['close'].rolling(5).mean()
    df['MA20'] = df['close'].rolling(20).mean()
    df['MA60'] = df['close'].rolling(60).mean()

    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = -delta.where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    df['RSI_Delta'] = df['RSI'].diff()

    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.05,
                        row_heights=[0.50, 0.20, 0.15, 0.15])

    fig.add_trace(go.Candlestick(x=df.index, open=df['open'], high=df['high'], low=df['low'], close=df['close'],
                                 increasing_line_color='#00ff88', decreasing_line_color='#ff3838'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA5'], line=dict(color='#ffff00', width=1.8), name='MA5'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA20'], line=dict(color='#00ffff', width=1.8), name='MA20'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA60'], line=dict(color='#ff00ff', width=1.8), name='MA60'), row=1, col=1)

    fig.add_trace(go.Bar(x=df.index, y=df['volume'], marker_color='#7777ff'), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['RSI'], line=dict(color='#ffa500', width=2)), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['RSI_Delta'], line=dict(color='#00ccff', width=2)), row=4, col=1)

    fig.add_hline(y=30, line_dash="dash", line_color="lime", row=3, col=1)
    fig.add_hline(y=70, line_dash="dash", line_color="red", row=3, col=1)
    fig.add_hline(y=0, line_dash="dot", line_color="white", row=4, col=1)

    fig.update_layout(title=f'{ticker} {interval_text}봉', template='plotly_dark', height=800, width=1200)
    fig.write_image(save_path, scale=1.5)
    return save_path

def load_image_to_gui(image_path):
    global photo_image
    img = Image.open(image_path)
    img = img.resize((1000, 650), Image.Resampling.LANCZOS)   # GUI 크기에 맞춤
    photo_image = ImageTk.PhotoImage(img)
    preview_label.config(image=photo_image)

def next_chart(event=None):
    global current_index
    if not charts:
        return
    current_index = (current_index + 1) % len(charts)
    coin, img_path = charts[current_index]
    status_label.config(text=f"현재 차트: {coin}   ({current_index+1}/{len(charts)})")
    load_image_to_gui(img_path)

def confirm_input():
    global charts, current_index
    coins_text = coins_entry.get().strip().upper()
    coin_list = [c.strip() for c in coins_text.split(',') if c.strip()]

    charts.clear()
    temp_dir = "/storage/emulated/0/Pictures/chart_temp"
    os.makedirs(temp_dir, exist_ok=True)

    for coin in coin_list:
        try:
            ticker = f"KRW-{coin}"
            filename = f"{coin}_{datetime.now().strftime('%H%M%S')}.png"
            img_path = os.path.join(temp_dir, filename)
            
            make_and_save_image(ticker, interval_var.get(), count_var.get(), img_path)
            charts.append((coin, img_path))
        except Exception as e:
            messagebox.showwarning("경고", f"{coin} 오류: {e}")

    if charts:
        current_index = 0
        status_label.config(text=f"현재 차트: {charts[0][0]}   (1/{len(charts)})")
        load_image_to_gui(charts[0][1])
        messagebox.showinfo("완료", "차트 영역을 클릭하면 다음 차트로 넘어갑니다.")

# ====================== GUI ======================
tk.Label(root, text="비트썸 코인 차트", font=("맑은고딕", 18, "bold")).pack(pady=10)

frame = tk.LabelFrame(root, text="설정", padx=15, pady=10)
frame.pack(fill="x", padx=20, pady=5)

tk.Label(frame, text="코인 (쉼표로 구분)").pack(anchor="w")
tk.Entry(frame, textvariable=coins_entry, font=11).pack(fill="x", pady=5)

tk.Label(frame, text="시간봉").pack(anchor="w")
ttk.Combobox(frame, textvariable=interval_var, values=list(intervals.keys()), state="readonly").pack(fill="x", pady=5)

tk.Label(frame, text="데이터 개수").pack(anchor="w")
tk.Entry(frame, textvariable=count_var).pack(anchor="w", pady=5)

tk.Button(frame, text="차트 생성", bg="#4488ff", fg="white", font=11, command=confirm_input).pack(pady=10)

# 차트 표시 영역
preview_label = tk.Label(root, bg="#1e1e1e", relief="sunken")
preview_label.pack(fill="both", expand=True, padx=20, pady=10)

# 상태 표시
status_label = tk.Label(root, text="차트 영역을 클릭하면 다음 차트로 이동합니다", fg="#00ffaa", font=("맑은고딕", 10))
status_label.pack(pady=5)

# 클릭 이벤트
preview_label.bind("<Button-1>", next_chart)

tk.Button(root, text="모두 PNG로 저장", bg="#00cc66", fg="white", command=lambda: messagebox.showinfo("알림", "Pictures 폴더에 저장되었습니다.")).pack(pady=8)

root.mainloop()