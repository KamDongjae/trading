@echo off
chcp 65001 > nul
title Binance rank band + risk limits
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d C:\Users\K\Desktop\trading
rem [2026-11-25] value-rank band + portfolio risk limits (binance_backtest9.py)
python binance_backtest9.py --months 12 > reports\binance_backtest9_log.txt 2>&1
type reports\binance_backtest9_log.txt
echo.
echo DONE. you can close this window.
timeout /t 600 > nul
