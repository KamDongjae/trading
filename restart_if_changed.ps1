# 세 Python 서비스의 코드 변경을 감지해 restart_all.ps1을 실행한다.
$ErrorActionPreference = 'Continue'
$root  = 'C:\Users\K\Desktop\trading'
$state = Join-Path $root 'reports\restart_if_changed_state.txt'
$log   = Join-Path $root 'reports\restart_if_changed_log.txt'
$files = @('data_collector.py', 'trading_server.py', 'trading_client.py')

$now = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$sig = ($files | ForEach-Object { (Get-FileHash (Join-Path $root $_) -Algorithm SHA256).Hash }) -join ','

if (-not (Test-Path $state)) {
  Set-Content -Path $state -Value $sig -Encoding UTF8
  Add-Content -Path $log -Value "[$now] 첫 실행: 현재 코드 기록만 함 (재시작 안 함)" -Encoding UTF8
  exit 0
}
$old = (Get-Content $state -Raw).Trim()
if ($old -eq $sig) { exit 0 }

# 저장 도중일 수 있으니 마지막 수정 후 60초 지나야 재시작
$latest = ($files | ForEach-Object { (Get-Item (Join-Path $root $_)).LastWriteTime } | Sort-Object -Descending)[0]
if (((Get-Date) - $latest).TotalSeconds -lt 60) {
  Start-Sleep -Seconds 60
  $sig = ($files | ForEach-Object { (Get-FileHash (Join-Path $root $_) -Algorithm SHA256).Hash }) -join ','
}

Add-Content -Path $log -Value "[$now] 코드 변경 감지 → restart_all 실행" -Encoding UTF8
try {
  & (Join-Path $root 'restart_all.ps1') *> (Join-Path $root 'reports\restart_all_log.txt')
} catch {
  Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] restart_all 실패: $_" -Encoding UTF8
  exit 1
}
Set-Content -Path $state -Value $sig -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] restart_all 완료" -Encoding UTF8
