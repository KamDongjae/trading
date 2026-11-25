@echo off
chcp 65001 > nul
title Binance V3 short parity + long check
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d C:\Users\K\Desktop\trading
rem [2026-11-26] V3 short rules vs server + long validation (binance_backtest14.py)
python binance_backtest14.py --months 12 > reports\binance_backtest14_log.txt 2>&1
type reports\binance_backtest14_log.txt
echo.
echo DONE. you can close this window.
timeout /t 600 > nul
