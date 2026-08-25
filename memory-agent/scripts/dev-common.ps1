$script:ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$script:BackendRoot = Join-Path $script:ProjectRoot 'backend'
$script:FrontendRoot = Join-Path $script:ProjectRoot 'frontend'
$script:RuntimeRoot = Join-Path $script:ProjectRoot '.dev-runtime'
$script:LogRoot = Join-Path $script:ProjectRoot 'logs\dev'
$script:StatePath = Join-Path $script:RuntimeRoot 'dev-state.json'
$script:DatabasePath = Join-Path $script:BackendRoot 'memory.db'
$script:VenvPython = Join-Path $script:BackendRoot '.venv\Scripts\python.exe'

function Get-CommandLine([int]$ProcessId) {
    try { return (Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop).CommandLine }
    catch { return '<unavailable: insufficient permission>' }
}

function Read-DevState {
    if (-not (Test-Path -LiteralPath $script:StatePath)) { return $null }
    try { return Get-Content -Raw -LiteralPath $script:StatePath | ConvertFrom-Json }
    catch { return $null }
}

function Test-RecordedProcess($Record) {
    if (-not $Record -or -not $Record.pid) { return $false }
    $process = Get-Process -Id ([int]$Record.pid) -ErrorAction SilentlyContinue
    if (-not $process) { return $false }
    try {
        $startMatches = $process.StartTime.ToUniversalTime().ToString('o') -eq [string]$Record.startTimeUtc
        $pathMatches = -not $Record.executablePath -or $process.Path -eq [string]$Record.executablePath
        return $startMatches -and $pathMatches
    }
    catch { return $false }
}

function Get-PortOwner([int]$Port) {
    $processId = $null
    try {
        $connection = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop | Select-Object -First 1
        if ($connection) { $processId = [int]$connection.OwningProcess }
    } catch {
        $match = netstat -ano | Select-String "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$" | Select-Object -First 1
        if ($match -and $match.Matches.Count) { $processId = [int]$match.Matches[0].Groups[1].Value }
    }
    if (-not $processId) { return $null }
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    $commandLine = Get-CommandLine $processId
    $state = Read-DevState
    $recordedHere = ($state -and (($state.backend.pid -eq $processId) -or ($state.frontend.pid -eq $processId)))
    $looksLikeProject = $recordedHere -or ($commandLine -ne '<unavailable: insufficient permission>' -and $commandLine.IndexOf($script:ProjectRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0)
    [pscustomobject]@{
        Port = $Port
        PID = $processId
        ProcessName = if ($process) { $process.ProcessName } else { '<unknown>' }
        ExecutablePath = if ($process -and $process.Path) { $process.Path } else { '<unavailable>' }
        CommandLine = $commandLine
        LooksLikeCurrentProject = [bool]$looksLikeProject
    }
}

function Remove-StaleDevState {
    $state = Read-DevState
    if (-not $state) {
        if (Test-Path -LiteralPath $script:StatePath) { Remove-Item -LiteralPath $script:StatePath -Force }
        return
    }
    if ((Test-RecordedProcess $state.backend) -or (Test-RecordedProcess $state.frontend)) { return }
    Remove-Item -LiteralPath $script:StatePath -Force
    Write-Host "Removed stale development state: $script:StatePath"
}

function New-ProcessRecord($Process) {
    [ordered]@{
        pid = $Process.Id
        startTimeUtc = $Process.StartTime.ToUniversalTime().ToString('o')
        executablePath = $Process.Path
    }
}
