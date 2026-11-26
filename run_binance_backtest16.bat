@echo off
chcp 65001 > nul
title Binance V3 short exp4 cut compare
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d C:\Users\K\Desktop\trading
rem [2026-11-26] V3 short exp4 cut -0.5 / -0.45 / -0.4 / -0.35 / -0.3 / -0.2 (binance_backtest16.py)
python binance_backtest16.py --months 12 --cuts -0.5 -0.45 -0.4 -0.35 -0.3 -0.2 > reports\binance_backtest16_log.txt 2>&1
type reports\binance_backtest16_log.txt
echo.
echo DONE. you can close this window.
timeout /t 600 > nul
