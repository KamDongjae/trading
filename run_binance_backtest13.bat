@echo off
chcp 65001 > nul
title Binance V3 rank bands 1-300
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d C:\Users\K\Desktop\trading
rem [2026-11-25] V3 model: re-select value-rank band over 1-300 (binance_backtest13.py)
python binance_backtest13.py --months 12 > reports\binance_backtest13_log.txt 2>&1
type reports\binance_backtest13_log.txt
echo.
echo DONE. you can close this window.
timeout /t 600 > nul
