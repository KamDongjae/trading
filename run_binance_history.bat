@echo off
chcp 65001 > nul
title Binance history download
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d C:\Users\K\Desktop\trading
rem [2026-11-23] 바이낸스 선물 1시간봉·펀딩비 400일치 받기 (history_binance\). 끝나면 창 닫아도 됨
python binance_history_downloader.py
echo.
echo 끝났어. 이 창은 닫아도 돼.
pause > nul
