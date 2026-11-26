@echo off
chcp 65001 > nul
title Binance V3 short + exit fee/slippage
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d C:\Users\K\Desktop\trading
rem [2026-11-26] bt14 S0 + exit cost 0 / 0.05 / 0.35 / 0.7%% (binance_backtest15.py)
python binance_backtest15.py --months 12 > reports\binance_backtest15_log.txt 2>&1
type reports\binance_backtest15_log.txt
echo.
echo DONE. you can close this window.
timeout /t 600 > nul
