@echo off
chcp 65001 > nul
title Pyramide restart all
cd /d C:\Users\K\Desktop\trading
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\K\Desktop\trading\restart_all.ps1" > reports\restart_all_log.txt 2>&1
type reports\restart_all_log.txt
timeout /t 5 > nul
