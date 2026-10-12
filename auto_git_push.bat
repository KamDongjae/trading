@echo off
chcp 65001 > nul
rem 코드/조건식/리포트 변경분을 자동으로 commit + push 한다.
rem 데이터(csv, data_archive), 백업 zip, .bak 파일은 올리지 않는다.
rem [2026-10-12] 새 변경이 없어도 push를 시도한다 — 이전 push가 실패해서 로컬에만 남은 commit을 다시 올리기 위해.
cd /d C:\Users\K\Desktop\trading

echo [%date% %time%] auto push start >> git_push_log.txt
git pull --rebase --autostash >> git_push_log.txt 2>&1

git add *.py *.txt *.md *.bat *.json reports analysis backups >> git_push_log.txt 2>&1
git reset -q -- collector_log.txt git_push_log.txt >> git_push_log.txt 2>&1

git diff --cached --quiet
if %errorlevel%==0 (
    echo [%date% %time%] no new changes >> git_push_log.txt
) else (
    git commit -m "auto: update %date% %time%" >> git_push_log.txt 2>&1
)

git push >> git_push_log.txt 2>&1
echo [%date% %time%] push done (code %errorlevel%) >> git_push_log.txt
