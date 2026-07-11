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

# ====================== 설정 ======================
root = tk.Tk()
root.title("비트썸 멀티 코인 차트 생성기")
root.geometry("1000x720")
root.resizable(True, True)

coins_entry = tk.StringVar(value="BTC")
interval_var = tk.StringVar(value="60분")
count_var = tk.IntVar(value=200)

intervals = {
    "1분": "minute1", "5분": "minute5", "10분": "minute10", "15분": "minute15",
    "30분": "minute30", "60분": "minute60", "6시간": "minute360", "1일": "day"
}

def make_chart(ticker, interval, interval_text, count):
    """차트 생성 함수"""
    df = python_bithumb.get_ohlcv(ticker=ticker, interval=interval, count=count)
    df.index = pd.to_datetime(df.index)

    # 기술적 지표
    df['MA5']  = df['close'].rolling(5).mean()
    df['MA20'] = df['close'].rolling(20).mean()
    df['MA60'] = df['close'].rolling(60).mean()

    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = -delta.where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    df['RSI_Delta'] = df['RSI'].diff()

    # 차트 만들기
    fig = make_subplots(
        rows=4, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.50, 0.20, 0.15, 0.15],
        subplot_titles=(f'{ticker} {interval_text}봉', '거래량', 'RSI (14)', 'RSI Delta')
    )

    fig.add_trace(go.Candlestick(
        x=df.index, open=df['open'], high=df['high'], low=df['low'], close=df['close'],
        increasing_line_color='#00ff88', decreasing_line_color='#ff3838'
    ), row=1, col=1)

    fig.add_trace(go.Scatter(x=df.index, y=df['MA5'],  line=dict(color='#ffff00', width=1.8), name='MA5'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA20'], line=dict(color='#00ffff', width=1.8), name='MA20'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA60'], line=dict(color='#ff00ff', width=1.8), name='MA60'), row=1, col=1)

    fig.add_trace(go.Bar(x=df.index, y=df['volume'], marker_color='#7777ff', name='Volume'), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['RSI'], line=dict(color='#ffa500', width=2), name='RSI'), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['RSI_Delta'], line=dict(color='#00ccff', width=2), name='RSI Delta'), row=4, col=1)

    fig.add_hline(y=30, line_dash="dash", line_color="lime", row=3, col=1)
    fig.add_hline(y=70, line_dash="dash", line_color="red", row=3, col=1)
    fig.add_hline(y=0, line_dash="dot", line_color="white", row=4, col=1)

    fig.update_layout(
        title=f'{ticker} {interval_text}봉 차트',
        template='plotly_dark',
        height=950,
        width=1450,
        legend=dict(x=0.01, y=0.98, bgcolor='rgba(0,0,0,0.7)')
    )
    return fig

def confirm_input():
    """입력 확인 및 미리보기"""
    coins_text = coins_entry.get().strip().upper()
    coin_list = [c.strip() for c in coins_text.split(',') if c.strip()]

    if not coin_list:
        messagebox.showwarning("경고", "코인을 입력해주세요! (예: BTC,ETH)")
        return

    interval_text = interval_var.get()
    interval = intervals[interval_text]
    count = count_var.get()

    for coin in coin_list[:4]:   # 최대 4개까지 미리보기
        try:
            ticker = f"KRW-{coin}"
            fig = make_chart(ticker, interval, interval_text, count)
            
            html_path = f"/storage/emulated/0/Pictures/preview_{coin}_{datetime.now().strftime('%H%M%S')}.html"
            fig.write_html(html_path)
            
            # 브라우저로 자동 열기
            threading.Thread(target=webbrowser.open, args=(f"file://{html_path}",)).start()
            
        except Exception as e:
            messagebox.showerror("오류", f"{coin} 차트 생성 실패:\n{e}")

    messagebox.showinfo("미리보기", f"{len(coin_list)}개 차트가 브라우저에 열렸습니다.")

def export_charts():
    """차트 출력 (저장)"""
    coins_text = coins_entry.get().strip().upper()
    coin_list = [c.strip() for c in coins_text.split(',') if c.strip()]
    if not coin_list:
        messagebox.showwarning("경고", "코인을 먼저 입력해주세요.")
        return

    interval_text = interval_var.get()
    interval = intervals[interval_text]
    count = count_var.get()
    now = datetime.now().strftime("%Y%m%d-%H%M")

    saved_count = 0
    pictures_dir = "/storage/emulated/0/Pictures"
    os.makedirs(pictures_dir, exist_ok=True)

    for coin in coin_list:
        try:
            ticker = f"KRW-{coin}"
            fig = make_chart(ticker, interval, interval_text, count)
            
            filename = f"{coin.lower()}-{interval_text}-{now}.png"
            save_path = os.path.join(pictures_dir, filename)
            
            fig.write_image(save_path, scale=1.6)
            print(f"저장 완료: {filename}")
            saved_count += 1
        except:
            # PNG 실패 시 HTML 저장
            html_path = save_path.replace('.png', '.html')
            fig.write_html(html_path)
            print(f"HTML 저장: {html_path}")
            saved_count += 1

    messagebox.showinfo("저장 완료", f"{saved_count}개 차트가 Pictures 폴더에 저장되었습니다.")

# ====================== GUI ======================
tk.Label(root, text="비트썸 멀티 코인 차트 생성기", font=("맑은고딕", 18, "bold")).pack(pady=15)

frame = tk.LabelFrame(root, text="입력 설정", font=("맑은고딕", 11), padx=15, pady=15)
frame.pack(fill="x", padx=20, pady=10)

tk.Label(frame, text="코인 입력 (쉼표로 구분 예: BTC,ETH,XRP)", font=("맑은고딕", 10)).pack(anchor="w")
tk.Entry(frame, textvariable=coins_entry, font=("맑은고딕", 11)).pack(fill="x", pady=8)

tk.Label(frame, text="시간봉 선택", font=("맑은고딕", 10)).pack(anchor="w")
ttk.Combobox(frame, textvariable=interval_var, values=list(intervals.keys()), state="readonly", font=("맑은고딕", 11)).pack(fill="x", pady=8)

tk.Label(frame, text="데이터 개수 (최근)", font=("맑은고딕", 10)).pack(anchor="w")
tk.Entry(frame, textvariable=count_var, font=("맑은고딕", 11)).pack(anchor="w", pady=8)

btn_frame = tk.Frame(frame)
btn_frame.pack(pady=15)
tk.Button(btn_frame, text="입력 확인\n(미리보기)", bg="#4488ff", fg="white", width=15, height=2, command=confirm_input).pack(side="left", padx=8)
tk.Button(btn_frame, text="차트 출력\n(Pictures에 저장)", bg="#00cc66", fg="white", width=18, height=2, command=export_charts).pack(side="left", padx=8)
tk.Button(btn_frame, text="종료", bg="#ee4444", fg="white", width=10, height=2, command=root.quit).pack(side="left", padx=8)

tk.Label(root, text="※ 미리보기는 브라우저로 열립니다.\n※ 차트 출력 버튼으로 PNG 파일 저장", 
         fg="gray", font=("맑은고딕", 9)).pack(pady=10)

root.mainloop()