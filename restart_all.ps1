# 서버, 클라이언트, 수집기를 종료하고 새 코드로 다시 켠다.
param([switch]$KeepClient)

$ErrorActionPreference = 'Stop'
$root = 'C:\Users\K\Desktop\trading'
$python = (Get-Command python.exe -CommandType Application | Select-Object -First 1).Source
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
Remove-Item Env:PYRAMIDE_BAT -ErrorAction SilentlyContinue
$data = 'C:\storage\emulated\0\Documents\simulation_data_usd.csv'
$bkdir = Join-Path $root 'backups\20261124_restart_all'
New-Item -ItemType Directory -Force -Path $bkdir | Out-Null
$bk = Join-Path $bkdir 'simulation_data_usd_before_restart.csv'
"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] restart_all start"
$backedUp = $false

function Kill-Tree($names, $pattern, $label) {
  $all = @(Get-CimInstance Win32_Process)
  $targets = @($all | Where-Object { $_.Name -in $names -and $_.CommandLine -match $pattern })
  if ($targets.Count -eq 0) { "$label : 실행 중인 것 없음"; return }
  foreach ($p in $targets) {
    if (-not (Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue)) { continue }
    "$label : pid $($p.ProcessId) 종료 ($($p.Name))"
    & taskkill /PID $p.ProcessId /T /F | Out-Null
    if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue)) {
      throw "$label 종료 실패 (pid $($p.ProcessId))"
    }
  }
}

# 1) 서버: 데이터 파일 쓰기 끝날 때까지 기다렸다가 백업 후 종료 (bat 창까지 같이 끔 → 자동 재실행 안 겹치게)
if (Test-Path $data) {
  for ($i = 0; $i -lt 30; $i++) {
    if (((Get-Date) - (Get-Item $data).LastWriteTime).TotalSeconds -ge 2) { break }
    Start-Sleep -Seconds 1
  }
  Copy-Item $data $bk -Force
  $before = @(Get-Content $bk).Count
  $backedUp = $true
  "백업 $bk ($before 줄)"
}
Kill-Tree @('cmd.exe') '\s+/?c\s+"*([^"\s]*[\\/])?run_server\.bat(?:"|\s|$)' '서버 창'
Kill-Tree @('python.exe','pythonw.exe') '^("[^"]+"|\S+)\s+"*([^"\s]*[\\/])?trading_server\.py(?:"|\s|$)' '서버 python'
# 2) 클라
if (-not $KeepClient) {
  Kill-Tree @('cmd.exe') '\s+/?c\s+"*([^"\s]*[\\/])?run_client\.bat(?:"|\s|$)' '클라 창'
  Kill-Tree @('python.exe','pythonw.exe') '^("[^"]+"|\S+)\s+"*([^"\s]*[\\/])?trading_client\.py(?:"|\s|$)' '클라 python'
}
Kill-Tree @('cmd.exe') '\s+/?c\s+"*([^"\s]*[\\/])?run_collector\.bat(?:"|\s|$)' '수집기 창'
Kill-Tree @('python.exe','pythonw.exe') '^("[^"]+"|\S+)\s+"*([^"\s]*[\\/])?data_collector\.py(?:"|\s|$)' '수집기 python'
Start-Sleep -Seconds 2

if ($backedUp) {
  $after = if (Test-Path $data) { @(Get-Content $data).Count } else { 0 }
  $ok = ($after -ge $before) -and (Test-Path $data) -and (Select-String -Path $data -Pattern '^positions' -Quiet)
  if (-not $ok) { Copy-Item $bk $data -Force; "데이터 파일 이상($after 줄) → 백업 복구" } else { "데이터 파일 정상 ($after 줄)" }
}

function Start-Python($script) {
  $process = Start-Process -FilePath $python -ArgumentList $script -WorkingDirectory $root -WindowStyle Normal -PassThru
  Start-Sleep -Seconds 1
  if ($process.HasExited) { throw "$script 시작 실패 (종료 코드 $($process.ExitCode))" }
  "$script 시작 (pid $($process.Id))"
}

Start-Python 'data_collector.py'
Start-Python 'trading_server.py'
if (-not $KeepClient) {
  Start-Sleep -Seconds 20
  Start-Python 'trading_client.py'
}
"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] restart_all done"
