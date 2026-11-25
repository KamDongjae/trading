@echo off
chcp 65001 > nul
title Binance D-trait check
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d C:\Users\K\Desktop\trading
rem [2026-11-25] D-trait rules applied to all Binance (binance_backtest7.py)
python binance_backtest7.py --months 12 > reports\binance_backtest7_log.txt 2>&1
type reports\binance_backtest7_log.txt
echo.
echo DONE. you can close this window.
timeout /t 600 > nul
