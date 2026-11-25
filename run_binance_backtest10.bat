@echo off
chcp 65001 > nul
title Binance raw-rank bands then filter
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d C:\Users\K\Desktop\trading
rem [2026-11-25] raw value-rank bands, filter inside each band (binance_backtest10.py)
python binance_backtest10.py --months 12 > reports\binance_backtest10_log.txt 2>&1
type reports\binance_backtest10_log.txt
echo.
echo DONE. you can close this window.
timeout /t 600 > nul
