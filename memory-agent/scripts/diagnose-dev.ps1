$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'dev-common.ps1')

$state = Read-DevState
$backendPort = if ($state) { [int]$state.backendPort } else { 8001 }
$frontendPort = if ($state) { [int]$state.frontendPort } else { 8080 }
$apiBase = if ($state) { [string]$state.apiBase } elseif ($env:MEMORY_AGENT_API_BASE) { $env:MEMORY_AGENT_API_BASE } else { "http://127.0.0.1:$backendPort" }

Write-Host "Project root: $script:ProjectRoot"
Write-Host "API Base: $apiBase"
Write-Host "Virtual environment: $script:VenvPython (exists: $(Test-Path $script:VenvPython))"
Write-Host "Database: $script:DatabasePath (exists: $(Test-Path $script:DatabasePath))"
Write-Host "Python: $((& python --version 2>&1) -join ' ')"
Write-Host "Venv Python: $(if (Test-Path $script:VenvPython) { (& $script:VenvPython --version 2>&1) -join ' ' } else { '<missing>' })"
$node = Get-Command node -ErrorAction SilentlyContinue
Write-Host "Node: $(if ($node) { (& $node.Source --version 2>&1) -join ' ' } else { '<not installed; not required>' })"

foreach ($port in @($backendPort, $frontendPort)) {
    $owner = Get-PortOwner $port
    if ($owner) { $owner | Format-List | Out-Host } else { Write-Host "Port ${port}: not listening" }
}

try {
    $health = Invoke-RestMethod "$apiBase/api/health" -TimeoutSec 3
    Write-Host 'Health: OK'
    Write-Host "Actual backend version: $($health.version)"
    Write-Host "Database accessible: $($health.database_accessible)"
} catch {
    Write-Host "Health: FAILED - $($_.Exception.Message)"
    Write-Host 'Actual backend version: <unavailable>'
}

if ($state) {
    Write-Host "State file: $script:StatePath"
    Write-Host "Frontend URL: $($state.frontendUrl)"
    Write-Host "Backend log: $($state.logs.backend)"
    Write-Host "Backend error log: $($state.logs.backendError)"
    Write-Host "Frontend log: $($state.logs.frontend)"
    Write-Host "Frontend error log: $($state.logs.frontendError)"
} else {
    Write-Host 'State file: <not found>'
    $latestLogs = Get-ChildItem -LiteralPath $script:LogRoot -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 4
    if ($latestLogs) { Write-Host 'Recent logs:'; $latestLogs.FullName | ForEach-Object { Write-Host "  $_" } }
}
