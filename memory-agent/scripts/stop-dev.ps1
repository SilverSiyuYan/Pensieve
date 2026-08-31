$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'dev-common.ps1')

$state = Read-DevState
if (-not $state) {
    if (Test-Path -LiteralPath $script:StatePath) { Remove-Item -LiteralPath $script:StatePath -Force }
    Write-Host 'No valid development state was found; no process was stopped.'
    exit 0
}

foreach ($name in @('frontend', 'backend')) {
    $record = $state.$name
    if (-not (Test-RecordedProcess $record)) {
        Write-Host "$name PID $($record.pid) is no longer the recorded process; it was not stopped."
        continue
    }
    $process = Get-Process -Id ([int]$record.pid) -ErrorAction Stop
    Write-Host "Stopping recorded $name PID $($process.Id): $($process.Path)"
    Stop-Process -Id $process.Id -ErrorAction Stop
    Wait-Process -Id $process.Id -Timeout 10 -ErrorAction SilentlyContinue
}

Remove-Item -LiteralPath $script:StatePath -Force
Write-Host 'Recorded development services stopped. No name-based process termination was used.'
