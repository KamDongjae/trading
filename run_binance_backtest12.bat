@echo off
chcp 65001 > nul
title Binance V3 liquidation reduction
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d C:\Users\K\Desktop\trading
rem [2026-11-25] V3 model: liquidation reduction + threshold recalibration grid (binance_backtest12.py)
python binance_backtest12.py --months 12 > reports\binance_backtest12_log.txt 2>&1
type reports\binance_backtest12_log.txt
echo.
echo DONE. you can close this window.
timeout /t 600 > nul
