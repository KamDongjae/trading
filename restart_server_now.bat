@echo off
chcp 65001 > nul
title Pyramide server restart
cd /d C:\Users\K\Desktop\trading
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_server_now.ps1" > reports\restart_server_log.txt 2>&1
type reports\restart_server_log.txt
timeout /t 5 > nul
