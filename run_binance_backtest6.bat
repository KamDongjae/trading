@echo off
chcp 65001 > nul
title Binance overfit check
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d C:\Users\K\Desktop\trading
rem [2026-11-25] overfit check: split KRW coins (binance_backtest6.py)
python binance_backtest6.py --months 12 > reports\binance_backtest6_log.txt 2>&1
type reports\binance_backtest6_log.txt
echo.
echo DONE. you can close this window.
timeout /t 600 > nul
