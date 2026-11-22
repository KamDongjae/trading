@echo off
chcp 65001 > nul
title Pyramide server
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d C:\Users\K\Desktop\trading

rem 비정상 종료(에러 코드 0이 아님)일 때만 30초 뒤 다시 실행. 창을 직접 닫거나 정상 종료하면 끝남.
:loop
echo [%date% %time%] server start >> server_log.txt
python trading_server.py >> server_log.txt 2>&1
set RC=%errorlevel%
if "%RC%"=="0" (
  echo [%date% %time%] server exited normally, no restart >> server_log.txt
  goto :eof
)
echo [%date% %time%] server crashed (code %RC%), restart in 30s >> server_log.txt
timeout /t 30 /nobreak > nul
goto loop
