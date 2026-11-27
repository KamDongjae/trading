$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$log = Join-Path $root 'reports\service_watchdog.log'

function Write-Status($message) {
    $line = "$(Get-Date -Format o) $message"
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
    Write-Output $line
}

if ((Get-ScheduledTask -TaskName restart_if_changed -ErrorAction SilentlyContinue).State -eq 'Running') {
    Write-Status 'Skipped while the code update task is running.'
    exit 0
}

$python = (Get-Command python.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
Remove-Item Env:PYRAMIDE_BAT -ErrorAction SilentlyContinue
$processes = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'")
$failed = $false

foreach ($script in @('trading_server.py', 'data_collector.py')) {
    try {
        $relative = [regex]::Escape($script)
        $absolute = [regex]::Escape((Join-Path $root $script))
        $pattern = '^(?:"[^"]+"|\S+)\s+(?:-u\s+)?"?(?:' + $absolute + '|(?:\.\\)?' + $relative + ')"?(?:\s|$)'
        $running = @($processes | Where-Object { $_.CommandLine -match $pattern })
        if ($running.Count) {
            Write-Status "$script running (PID $($running.ProcessId -join ', '))."
            continue
        }
        if (-not (Test-Path -LiteralPath (Join-Path $root $script) -PathType Leaf)) {
            throw 'Script file is missing.'
        }
        $process = Start-Process -FilePath $python -ArgumentList $script -WorkingDirectory $root -WindowStyle Normal -PassThru
        if ($process.WaitForExit(2000)) {
            throw "Python exited during startup (code $($process.ExitCode))."
        }
        Write-Status "$script restarted (PID $($process.Id))."
    } catch {
        $failed = $true
        Write-Status "$script failed: $_"
    }
}

if ($failed) { exit 1 }
