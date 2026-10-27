@echo off
chcp 65001 > nul
title Pyramide data_collector
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
set PYRAMIDE_BAT=1
cd /d C:\Users\K\Desktop\trading

:loop
echo [%date% %time%] collector start >> collector_log.txt
python data_collector.py >> collector_log.txt 2>&1
echo [%date% %time%] collector stopped (code %errorlevel%), restart in 30s >> collector_log.txt
timeout /t 30 /nobreak > nul
goto loop
