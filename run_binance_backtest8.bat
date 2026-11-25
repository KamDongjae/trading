@echo off
chcp 65001 > nul
title Binance value-rank bands
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d C:\Users\K\Desktop\trading
rem [2026-11-25] value-rank band check (binance_backtest8.py), waits for backtest7 to finish
:wait
if exist reports\binance_backtest7_2026*.md goto run
echo waiting for backtest7...
timeout /t 30 > nul
goto wait
:run
python binance_backtest8.py --months 12 > reports\binance_backtest8_log.txt 2>&1
type reports\binance_backtest8_log.txt
echo.
echo DONE. you can close this window.
timeout /t 600 > nul
