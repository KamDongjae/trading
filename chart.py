import python_bithumb
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from datetime import datetime
import os
import tkinter as tk
from tkinter import ttk, messagebox
import webbrowser
import threading

root = tk.Tk()
root.title("비트썸 멀티 코인 차트 생성기")
root.geometry("1000x750")
root.resizable(True, True)

coins_entry = tk.StringVar(value="BTC")
interval_var = tk.StringVar(value="60분")
count_var = tk.IntVar(value=200)

intervals = {
    "1분": "minute1", "5분": "minute5", "10분": "minute10", "15분": "minute15",
    "30분": "minute30", "60분": "minute60", "6시간": "minute360", "1일": "day"
}

charts = []          # 생성된 차트 리스트
current_index = 0    # 현재 보여주는 차트 인덱스

def make_chart(ticker, interval_text, count):
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
                        row_heights=[0.50, 0.20, 0.15, 0.15],
                        subplot_titles=(f'{ticker} {interval_text}봉', '거래량', 'RSI (14)', 'RSI Delta'))

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

    fig.update_layout(title=f'{ticker} {interval_text}봉 차트', template='plotly_dark',
                      height=950, width=1450, xaxis_rangeslider_visible=False)
    return fig

def show_current_chart():
    """현재 인덱스의 차트를 브라우저에 표시"""
    if not charts:
        return
    coin, fig = charts[current_index]
    html_path = f"/storage/emulated/0/Pictures/preview_{coin}_{datetime.now().strftime('%H%M%S')}.html"
    fig.write_html(html_path)
    webbrowser.open(f"file://{html_path}")

def next_chart(event=None):
    """클릭 시 다음 차트로 (순환)"""
    global current_index
    if not charts:
        return
    current_index = (current_index + 1) % len(charts)
    show_current_chart()

def confirm_input():
    global charts, current_index
    coins_text = coins_entry.get().strip().upper()
    coin_list = [c.strip() for c in coins_text.split(',') if c.strip()]

    if not coin_list:
        messagebox.showwarning("경고", "코인을 입력해주세요!")
        return

    interval_text = interval_var.get()
    count = count_var.get()
    charts.clear()

    for coin in coin_list:
        try:
            ticker = f"KRW-{coin}"
            fig = make_chart(ticker, interval_text, count)
            charts.append((coin, fig))
        except Exception as e:
            messagebox.showwarning("경고", f"{coin} 생성 실패: {e}")

    current_index = 0
    if charts:
        messagebox.showinfo("완료", f"{len(charts)}개 차트 준비 완료!\n\n미리보기 영역을 클릭하면 다음 차트로 넘어갑니다.")
        show_current_chart()

def export_charts():
    if not charts:
        messagebox.showwarning("경고", "먼저 입력 확인을 눌러주세요.")
        return

    pictures_dir = "/storage/emulated/0/Pictures"
    os.makedirs(pictures_dir, exist_ok=True)
    now = datetime.now().strftime("%Y%m%d-%H%M")
    saved = 0

    for coin, fig in charts:
        try:
            filename = f"{coin.lower()}-{interval_var.get()}-{now}.png"
            path = os.path.join(pictures_dir, filename)
            fig.write_image(path, scale=1.6)
            saved += 1
        except:
            path = os.path.join(pictures_dir, filename.replace('.png', '.html'))
            fig.write_html(path)
            saved += 1

    messagebox.showinfo("저장 완료", f"{saved}개 차트가 Pictures 폴더에 저장되었습니다.")

# ====================== GUI ======================
tk.Label(root, text="비트썸 멀티 코인 차트", font=("맑은고딕", 18, "bold")).pack(pady=10)

frame = tk.LabelFrame(root, text="설정", padx=15, pady=10)
frame.pack(fill="x", padx=20, pady=5)

tk.Label(frame, text="코인 (쉼표 구분)", font=10).pack(anchor="w")
tk.Entry(frame, textvariable=coins_entry, font=11).pack(fill="x", pady=5)

tk.Label(frame, text="시간봉", font=10).pack(anchor="w")
ttk.Combobox(frame, textvariable=interval_var, values=list(intervals.keys()), state="readonly").pack(fill="x", pady=5)

tk.Label(frame, text="데이터 개수", font=10).pack(anchor="w")
tk.Entry(frame, textvariable=count_var).pack(anchor="w", pady=5)

btn_frame = tk.Frame(frame)
btn_frame.pack(pady=10)
tk.Button(btn_frame, text="입력 확인", bg="#4488ff", fg="white", width=12, command=confirm_input).pack(side="left", padx=5)
tk.Button(btn_frame, text="모두 저장", bg="#00cc66", fg="white", width=12, command=export_charts).pack(side="left", padx=5)
tk.Button(btn_frame, text="종료", bg="#ee4444", fg="white", command=root.quit).pack(side="left", padx=5)

# 미리보기 영역 (클릭으로 다음 차트)
preview = tk.Label(root, text="← 차트 미리보기 영역 →\n\n여기를 클릭하면 다음 차트로 넘어갑니다\n(마지막 → 처음으로 순환)", 
                   bg="#1e1e1e", fg="#cccccc", font=("맑은고딕", 12), height=18, relief="sunken")
preview.pack(fill="both", expand=True, padx=20, pady=15)

# 클릭 이벤트 연결
preview.bind("<Button-1>", next_chart)

root.mainloop()