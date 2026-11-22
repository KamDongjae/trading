@echo off
chcp 65001 > nul
title Pyramide client
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d C:\Users\K\Desktop\trading

rem 비정상 종료(에러 코드 0이 아님)일 때만 30초 뒤 다시 실행. 창을 직접 닫거나 정상 종료하면 끝남.
:loop
echo [%date% %time%] client start >> client_log.txt
python trading_client.py >> client_log.txt 2>&1
set RC=%errorlevel%
if "%RC%"=="0" (
  echo [%date% %time%] client exited normally, no restart >> client_log.txt
  goto :eof
)
echo [%date% %time%] client crashed (code %RC%), restart in 30s >> client_log.txt
timeout /t 30 /nobreak > nul
goto loop
