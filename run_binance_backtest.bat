@echo off
chcp 65001 > nul
title Binance backtest
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d C:\Users\K\Desktop\trading
rem [2026-11-23] 바이낸스 1년 백테스트 — 결과는 reports\binance_backtest_날짜.md
python binance_backtest.py --months 12 > reports\binance_backtest_log.txt 2>&1
type reports\binance_backtest_log.txt
echo.
echo 끝났어. 이 창은 닫아도 돼.
pause > nul
