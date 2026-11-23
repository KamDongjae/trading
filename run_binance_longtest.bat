@echo off
chcp 65001 > nul
title Binance long backtest
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d C:\Users\K\Desktop\trading
rem [2026-11-23] 1) binance history backfill to 1100 days  2) backtest2 (binance-trained model, universe compare)
python binance_history_backfill.py > reports\binance_backtest2_log.txt 2>&1
python binance_backtest2.py --months 12 >> reports\binance_backtest2_log.txt 2>&1
type reports\binance_backtest2_log.txt
echo.
echo DONE. you can close this window.
pause > nul
