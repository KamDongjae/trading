@echo off
chcp 65001 > nul
title Train Binance V3 model
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d C:\Users\K\Desktop\trading
rem [2026-11-25] train Binance V3 model (prob_model.py binance_v3)
python prob_model.py binance_v3 > reports\train_binance_v3_log.txt 2>&1
type reports\train_binance_v3_log.txt
echo.
echo DONE. you can close this window.
timeout /t 600 > nul
