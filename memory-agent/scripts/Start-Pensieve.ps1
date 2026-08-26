param(
    [ValidateRange(1, 65535)][int]$BackendPort = 8001,
    [ValidateRange(1, 65535)][int]$FrontendPort = 8080,
    [string]$ApiBase = $env:MEMORY_AGENT_API_BASE,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$backendRoot = Join-Path $projectRoot 'backend'
$frontendRoot = Join-Path $projectRoot 'frontend'
$runtimeRoot = Join-Path $projectRoot 'runtime'
$logRoot = Join-Path $runtimeRoot 'logs'
$pidFile = Join-Path $runtimeRoot 'pids.json'
$pythonPath = Join-Path $backendRoot '.venv\Scripts\python.exe'
$backendEntry = Join-Path $backendRoot 'main.py'
$frontendEntry = Join-Path $frontendRoot 'index.html'
$environmentFile = Join-Path $backendRoot '.env'
$databaseFile = Join-Path $backendRoot 'memory.db'
if ([string]::IsNullOrWhiteSpace($ApiBase)) { $ApiBase = "http://127.0.0.1:$BackendPort" }
try { $apiUri = [Uri]$ApiBase } catch { throw "Invalid API base URL: $ApiBase" }
if (-not $apiUri.IsAbsoluteUri -or $apiUri.Scheme -ne 'http' -or $apiUri.Host -notin @('127.0.0.1', 'localhost')) {
    throw 'Local API base must be an absolute HTTP URL using 127.0.0.1 or localhost.'
}
if ($apiUri.UserInfo -or $apiUri.Query -or $apiUri.Fragment -or $apiUri.AbsolutePath -notin @('/', '/api', '/api/')) {
    throw 'API base must be a service root without credentials, query parameters, or fragments.'
}
$BackendPort = $apiUri.Port
$backendBase = "http://127.0.0.1:$BackendPort"
$frontendOrigin = "http://127.0.0.1:$FrontendPort"
$frontendUrl = "$frontendOrigin/?apiBase=$([Uri]::EscapeDataString($backendBase))"
$createdProcesses = @()
$browserOpened = $false

function Get-ProcessCommandLine([int]$ProcessId) {
    try {
        return [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop).CommandLine
    } catch {
        return '<unavailable: insufficient permission or process exited>'
    }
}

function Get-PortOwner([int]$Port) {
    $connection = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $connection) {
        $match = netstat -ano | Select-String "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$" |
            Select-Object -First 1
        if ($match) { $ownerPid = [int]$match.Matches[0].Groups[1].Value }
    } else {
        $ownerPid = [int]$connection.OwningProcess
    }
    if (-not $ownerPid) { return $null }
    $process = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
    return [pscustomobject]@{
        PID = $ownerPid
        ProcessName = if ($process) { $process.ProcessName } else { '<unknown>' }
        ExecutablePath = if ($process) { $process.Path } else { '<unknown>' }
        CommandLine = Get-ProcessCommandLine $ownerPid
    }
}

function Test-BackendHealth {
    try {
        $response = Invoke-RestMethod "$backendBase/api/health" -TimeoutSec 3
        if ($response.application -eq 'memory-agent' -and $response.status -eq 'ok' -and $response.database_accessible -eq $true) {
            return [pscustomobject]@{ Healthy = $true; Detail = "HTTP 200, memory-agent $($response.version), database accessible" }
        }
        return [pscustomobject]@{ Healthy = $false; Detail = "Unexpected health response: $($response | ConvertTo-Json -Compress -Depth 3)" }
    } catch {
        try {
            $schema = Invoke-WebRequest "$backendBase/openapi.json" -UseBasicParsing -TimeoutSec 3
            if ($schema.StatusCode -eq 200 -and $schema.Content -match 'memory-agent') {
                return [pscustomobject]@{ Healthy = $true; Detail = 'Health endpoint failed, but the Pensieve OpenAPI document returned HTTP 200' }
            }
        } catch { }
        return [pscustomobject]@{ Healthy = $false; Detail = $_.Exception.Message }
    }
}

function Test-FrontendHealth {
    try {
        $response = Invoke-WebRequest "$frontendOrigin/" -UseBasicParsing -TimeoutSec 3
        # Keep the marker ASCII-only so Windows PowerShell 5.1 can parse this
        # UTF-8 script consistently regardless of the active console code page.
        if ($response.StatusCode -eq 200 -and $response.Content -match 'src="api-runtime\.js') {
            return [pscustomobject]@{ Healthy = $true; Detail = 'HTTP 200, Pensieve frontend marker found' }
        }
        return [pscustomobject]@{ Healthy = $false; Detail = "HTTP $($response.StatusCode), but the Pensieve frontend marker was not found" }
    } catch {
        return [pscustomobject]@{ Healthy = $false; Detail = $_.Exception.Message }
    }
}

function Test-CorsHealth {
    try {
        $preflight = Invoke-WebRequest "$backendBase/api/memory/auto" -Method Options -UseBasicParsing -MaximumRedirection 0 -TimeoutSec 5 -Headers @{
            Origin = $frontendOrigin
            'Access-Control-Request-Method' = 'POST'
            'Access-Control-Request-Headers' = 'authorization,content-type'
        }
        $actual = Invoke-WebRequest "$backendBase/api/health" -Method Get -UseBasicParsing -MaximumRedirection 0 -TimeoutSec 5 -Headers @{
            Origin = $frontendOrigin
        }
        $preflightOrigin = [string]$preflight.Headers['Access-Control-Allow-Origin']
        $actualOrigin = [string]$actual.Headers['Access-Control-Allow-Origin']
        $healthy = $preflight.StatusCode -eq 200 -and $actual.StatusCode -eq 200 -and
            $preflightOrigin -eq $frontendOrigin -and $actualOrigin -eq $frontendOrigin
        return [pscustomobject]@{
            Healthy = $healthy
            Detail = "OPTIONS HTTP $($preflight.StatusCode), Allow-Origin='$preflightOrigin'; GET HTTP $($actual.StatusCode), Allow-Origin='$actualOrigin'"
        }
    } catch {
        return [pscustomobject]@{ Healthy = $false; Detail = $_.Exception.Message }
    }
}

function Wait-ServiceHealth([string]$Service, [scriptblock]$Probe, [int]$TimeoutSeconds, [string]$ErrorLog) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastResult = [pscustomobject]@{ Healthy = $false; Detail = 'Health check has not run' }
    do {
        $lastResult = & $Probe
        if ($lastResult.Healthy) { return $lastResult }
        Start-Sleep -Milliseconds 400
    } while ((Get-Date) -lt $deadline)

    throw "$Service failed to become healthy within $TimeoutSeconds seconds.`nLast health result: $($lastResult.Detail)`nError log: $ErrorLog`nSuggestion: inspect the error log, verify backend\.env and dependencies, then run scripts\diagnose-dev.ps1."
}

function Show-UnsafePortAndExit([string]$Service, [int]$Port, $Owner, [string]$HealthDetail) {
    Write-Error @"
$Service cannot start because port $Port is occupied and its health check failed.
Last health result: $HealthDetail
PID: $($Owner.PID)
Process: $($Owner.ProcessName)
Executable: $($Owner.ExecutablePath)
Command line: $($Owner.CommandLine)
No process was stopped. Identify the owning application and stop it there, or rerun this script with explicit unused -BackendPort/-FrontendPort values.
"@
    exit 1
}

function New-ProcessRecord([string]$Service, $Process, [string]$ExpectedCommandFeature) {
    $runtimeProcess = Get-Process -Id $Process.Id -ErrorAction Stop
    return [pscustomobject]@{
        pid = $runtimeProcess.Id
        startedAtUtc = $runtimeProcess.StartTime.ToUniversalTime().ToString('o')
        service = $Service
        executablePath = $runtimeProcess.Path
        expectedCommandFeature = $ExpectedCommandFeature
    }
}

function Read-ValidPreviousRecords {
    if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) { return @() }
    try {
        $parsedRecords = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
        $records = @($parsedRecords | ForEach-Object { $_ })
    } catch { return @() }
    return @($records | Where-Object {
        $record = $_
        $process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
        if (-not $process) { return $false }
        $sameStart = $process.StartTime.ToUniversalTime().ToString('o') -eq [string]$record.startedAtUtc
        $commandLine = Get-ProcessCommandLine ([int]$record.pid)
        return $sameStart -and $commandLine.IndexOf([string]$record.expectedCommandFeature, [StringComparison]::OrdinalIgnoreCase) -ge 0
    })
}

function Save-ProcessRecords {
    $createdKeys = @($createdProcesses | ForEach-Object { "$($_.pid)|$($_.startedAtUtc)" })
    $previousRecords = @(Read-ValidPreviousRecords | Where-Object { "$($_.pid)|$($_.startedAtUtc)" -notin $createdKeys })
    $allRecords = @($previousRecords + $createdProcesses)
    $temporaryPidFile = "$pidFile.tmp"
    ConvertTo-Json -InputObject @($allRecords) -Depth 4 | Set-Content -Encoding UTF8 -LiteralPath $temporaryPidFile
    Move-Item -Force -LiteralPath $temporaryPidFile -Destination $pidFile
}

foreach ($requiredFile in @($backendEntry, $frontendEntry, $environmentFile, $databaseFile, $pythonPath)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) { throw "Required file is missing: $requiredFile" }
}

$pythonVersion = & $pythonPath --version 2>&1
if ($LASTEXITCODE -ne 0) { throw "Project Python failed to run: $pythonPath`n$pythonVersion" }
& $pythonPath -c 'import fastapi, uvicorn, chromadb, openai, dotenv, http.server' 2>$null
if ($LASTEXITCODE -ne 0) { throw "Python dependency or frontend http.server check failed. Run: `"$pythonPath`" -m pip install -r `"$(Join-Path $backendRoot 'requirements.txt')`"" }

# The checked-in frontend is static. If a future checkout introduces a Node build,
# fail explicitly and point to npm.cmd so PowerShell never selects npm.ps1.
if (Test-Path -LiteralPath (Join-Path $frontendRoot 'package.json')) {
    $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npmCommand) { throw 'A Node frontend was detected, but npm.cmd was not found on PATH.' }
    throw "A Node frontend was detected at runtime. Review its scripts before changing the confirmed static-server launch path. npm executable: $($npmCommand.Source)"
}

New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backendOut = Join-Path $logRoot "backend-$timestamp.stdout.log"
$backendErr = Join-Path $logRoot "backend-$timestamp.stderr.log"
$backendLogHint = '<backend was reused; this run did not create a backend log>'
$frontendOut = Join-Path $logRoot "frontend-$timestamp.stdout.log"
$frontendErr = Join-Path $logRoot "frontend-$timestamp.stderr.log"

$backendHealth = Test-BackendHealth
$backendOwner = Get-PortOwner $BackendPort
if ($backendOwner) {
    if (-not $backendHealth.Healthy) { Show-UnsafePortAndExit 'Backend' $BackendPort $backendOwner $backendHealth.Detail }
    Write-Host "Backend is already healthy on port $BackendPort; reusing PID $($backendOwner.PID)."
} else {
    $previousCors = $env:CORS_ALLOWED_ORIGINS
    $previousListen = $env:MEMORY_AGENT_LISTEN_ADDRESS
    try {
        $env:CORS_ALLOWED_ORIGINS = "$frontendOrigin,http://localhost:$FrontendPort"
        $env:MEMORY_AGENT_LISTEN_ADDRESS = "127.0.0.1:$BackendPort"
        try {
            $backendProcess = Start-Process -FilePath $pythonPath -WorkingDirectory $backendRoot -ArgumentList @(
                '-m', 'uvicorn', 'main:application', '--host', '127.0.0.1', '--port', $BackendPort, '--workers', '1'
            ) -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr -WindowStyle Hidden -PassThru
        } catch {
            throw "Backend process failed to start: $($_.Exception.Message)`nError log: $backendErr`nSuggestion: verify the virtual environment and backend\.env, then run scripts\diagnose-dev.ps1."
        }
    } finally {
        if ($null -eq $previousCors) { Remove-Item Env:CORS_ALLOWED_ORIGINS -ErrorAction SilentlyContinue } else { $env:CORS_ALLOWED_ORIGINS = $previousCors }
        if ($null -eq $previousListen) { Remove-Item Env:MEMORY_AGENT_LISTEN_ADDRESS -ErrorAction SilentlyContinue } else { $env:MEMORY_AGENT_LISTEN_ADDRESS = $previousListen }
    }
    $createdProcesses += New-ProcessRecord 'backend' $backendProcess 'main:application'
    $backendLogHint = $backendErr
    Save-ProcessRecords
    Wait-ServiceHealth 'Backend' ${function:Test-BackendHealth} 30 $backendErr | Out-Null
    Write-Host "Backend started and is healthy (PID $($backendProcess.Id))."
}

$corsHealth = Test-CorsHealth
if (-not $corsHealth.Healthy) {
    throw "Backend CORS validation failed for frontend origin $frontendOrigin.`nLast CORS result: $($corsHealth.Detail)`nBackend log: $backendLogHint`nSuggestion: verify CORS_ALLOWED_ORIGINS and make sure port $BackendPort belongs to this Pensieve checkout."
}
Write-Host "CORS validation passed: $($corsHealth.Detail)"

$frontendHealth = Test-FrontendHealth
$frontendOwner = Get-PortOwner $FrontendPort
if ($frontendOwner) {
    if (-not $frontendHealth.Healthy) { Show-UnsafePortAndExit 'Frontend' $FrontendPort $frontendOwner $frontendHealth.Detail }
    Write-Host "Frontend is already healthy on port $FrontendPort; reusing PID $($frontendOwner.PID)."
} else {
    try {
        $frontendProcess = Start-Process -FilePath $pythonPath -WorkingDirectory $projectRoot -ArgumentList @(
            '-m', 'http.server', $FrontendPort, '--bind', '127.0.0.1', '--directory', ('"{0}"' -f $frontendRoot)
        ) -RedirectStandardOutput $frontendOut -RedirectStandardError $frontendErr -WindowStyle Hidden -PassThru
    } catch {
        throw "Frontend process failed to start: $($_.Exception.Message)`nError log: $frontendErr`nSuggestion: verify the frontend directory and project Python, then retry."
    }
    $createdProcesses += New-ProcessRecord 'frontend' $frontendProcess 'http.server'
    Save-ProcessRecords
    Wait-ServiceHealth 'Frontend' ${function:Test-FrontendHealth} 15 $frontendErr | Out-Null
    Write-Host "Frontend started and is healthy (PID $($frontendProcess.Id))."
}

Save-ProcessRecords

if (-not $NoBrowser -and -not $browserOpened) {
    Start-Process $frontendUrl
    $browserOpened = $true
}

Write-Host 'Pensieve is ready.'
Write-Host "Frontend: $frontendUrl"
Write-Host "Backend: $backendBase"
Write-Host "PID state: $pidFile"
Write-Host "Logs: $logRoot"
