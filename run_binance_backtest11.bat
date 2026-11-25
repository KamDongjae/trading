@echo off
chcp 65001 > nul
title Binance model rework step2
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d C:\Users\K\Desktop\trading
rem [2026-11-25] rework step 2: universe-independent features + Binance-trained model (binance_backtest11.py)
python binance_backtest11.py --months 12 > reports\binance_backtest11_log.txt 2>&1
type reports\binance_backtest11_log.txt
echo.
echo DONE. you can close this window.
timeout /t 600 > nul
