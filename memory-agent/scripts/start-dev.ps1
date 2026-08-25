param(
    [ValidateRange(1, 65535)][int]$BackendPort = 8001,
    [ValidateRange(1, 65535)][int]$FrontendPort = 8080,
    [string]$ApiBase = $env:MEMORY_AGENT_API_BASE
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'dev-common.ps1')

# Some Windows launchers pass both Path and PATH in the process environment.
# Start-Process builds a case-insensitive environment dictionary and otherwise
# fails before either service can start.
$pathKeys = @([Environment]::GetEnvironmentVariables().Keys | Where-Object { $_ -ieq 'PATH' })
if ($pathKeys.Count -gt 1) {
    $pathValue = [Environment]::GetEnvironmentVariable('PATH')
    [Environment]::SetEnvironmentVariable('Path', $null, 'Process')
    [Environment]::SetEnvironmentVariable('PATH', $pathValue, 'Process')
}

function Stop-StartedProcess($Process) {
    if ($Process -and -not $Process.HasExited) { Stop-Process -Id $Process.Id -ErrorAction SilentlyContinue }
}

if (-not (Test-Path -LiteralPath $script:VenvPython)) {
    $hint = "`nCreate it with:`n  cd `"$script:BackendRoot`"`n  python -m venv .venv`n  .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
    throw "Project virtual environment is missing: $script:VenvPython$hint"
}
$python = (Resolve-Path -LiteralPath $script:VenvPython).Path

if ([string]::IsNullOrWhiteSpace($ApiBase)) { $ApiBase = "http://127.0.0.1:$BackendPort" }
try { $apiUri = [Uri]$ApiBase } catch { throw "Invalid API base URL: $ApiBase" }
if (-not $apiUri.IsAbsoluteUri -or $apiUri.Scheme -ne 'http' -or $apiUri.Host -notin @('127.0.0.1', 'localhost')) {
    throw 'Local API base must be an absolute HTTP URL using 127.0.0.1 or localhost.'
}
if ($apiUri.UserInfo -or $apiUri.Query -or $apiUri.Fragment -or $apiUri.AbsolutePath -notin @('/', '/api', '/api/')) {
    throw 'API base must be a service root without credentials, query parameters, or fragments.'
}
$BackendPort = $apiUri.Port
$ApiBase = "http://127.0.0.1:$BackendPort"
$frontendOrigin = "http://127.0.0.1:$FrontendPort"
$frontendUrl = "$frontendOrigin/?apiBase=$([Uri]::EscapeDataString($ApiBase))"

Remove-StaleDevState
$existingState = Read-DevState
foreach ($port in @($BackendPort, $FrontendPort)) {
    $owner = Get-PortOwner $port
    if ($owner) {
        $owner | Format-List | Out-Host
        throw "Port $port is occupied. No process was stopped and no alternate port was selected. If it belongs to this project, use scripts\stop-dev.ps1 only when it is recorded there; otherwise stop it from its owning application."
    }
}
if ($existingState -and ((Test-RecordedProcess $existingState.backend) -or (Test-RecordedProcess $existingState.frontend))) {
    throw 'This project already has recorded development processes. Run scripts\diagnose-dev.ps1, then scripts\stop-dev.ps1.'
}

if (-not (Test-Path -LiteralPath $script:DatabasePath -PathType Leaf)) { throw "Database file is missing: $script:DatabasePath" }
$databaseDirectory = Split-Path -Parent $script:DatabasePath
if (-not (Test-Path -LiteralPath $databaseDirectory -PathType Container)) { throw "Database directory is missing: $databaseDirectory" }
$databaseCheck = & $python -c @'
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1], timeout=2)
try:
    result = connection.execute('PRAGMA quick_check').fetchone()
    if result != ('ok',):
        raise RuntimeError(f'SQLite quick_check failed: {result!r}')
    connection.execute('BEGIN IMMEDIATE')
    connection.rollback()
finally:
    connection.close()
'@ $script:DatabasePath 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Database is not readable and writable by the project Python environment: $script:DatabasePath`n$($databaseCheck -join [Environment]::NewLine)"
}

$envFile = Join-Path $script:BackendRoot '.env'
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) { throw "Backend environment file is missing: $envFile" }
$envFileNames = Get-Content -LiteralPath $envFile | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=') { $Matches[1] }
}
$missingVariables = @('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'MODEL_NAME') | Where-Object {
    -not [Environment]::GetEnvironmentVariable($_) -and $_ -notin $envFileNames
}
if ($missingVariables) { throw "Missing required environment variables: $($missingVariables -join ', ')" }

$savedErrorPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
Push-Location $script:BackendRoot
try {
    $dependencyOutput = & $python -c 'import fastapi, uvicorn, chromadb, openai, dotenv' 2>&1
    $dependencyExitCode = $LASTEXITCODE
    $preflightOutput = & $python -c 'import app_meta, database, main; from pathlib import Path; print(app_meta.APP_VERSION); print(Path(main.__file__).resolve()); print(database.DATABASE_PATH.resolve())' 2>&1
    $preflightExitCode = $LASTEXITCODE
} finally { Pop-Location }
$ErrorActionPreference = $savedErrorPreference
if ($dependencyExitCode -ne 0) { throw "Required Python dependency check failed:`n$($dependencyOutput -join [Environment]::NewLine)" }
if ($preflightExitCode -ne 0) { throw "Backend import preflight failed:`n$($preflightOutput -join [Environment]::NewLine)" }
$version, $loadedModule, $resolvedDatabase = $preflightOutput[-3..-1]
if ([IO.Path]::GetFullPath($loadedModule) -ne [IO.Path]::GetFullPath((Join-Path $script:BackendRoot 'main.py'))) {
    throw "Wrong backend module would be loaded: $loadedModule"
}

New-Item -ItemType Directory -Force -Path $script:RuntimeRoot, $script:LogRoot | Out-Null
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backendOut = Join-Path $script:LogRoot "backend-$timestamp.log"
$backendErr = Join-Path $script:LogRoot "backend-$timestamp.error.log"
$frontendOut = Join-Path $script:LogRoot "frontend-$timestamp.log"
$frontendErr = Join-Path $script:LogRoot "frontend-$timestamp.error.log"

$previousCors = $env:CORS_ALLOWED_ORIGINS
$previousListenAddress = $env:MEMORY_AGENT_LISTEN_ADDRESS
$env:CORS_ALLOWED_ORIGINS = "$frontendOrigin,http://localhost:$FrontendPort"
$env:MEMORY_AGENT_LISTEN_ADDRESS = "127.0.0.1:$BackendPort"
$backend = Start-Process -FilePath $python -WorkingDirectory $script:BackendRoot -ArgumentList @(
    '-m', 'uvicorn', 'main:application', '--host', '127.0.0.1', '--port', $BackendPort, '--workers', '1'
) -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr -WindowStyle Hidden -PassThru
if ($null -eq $previousCors) { Remove-Item Env:CORS_ALLOWED_ORIGINS -ErrorAction SilentlyContinue } else { $env:CORS_ALLOWED_ORIGINS = $previousCors }
if ($null -eq $previousListenAddress) { Remove-Item Env:MEMORY_AGENT_LISTEN_ADDRESS -ErrorAction SilentlyContinue } else { $env:MEMORY_AGENT_LISTEN_ADDRESS = $previousListenAddress }

$frontend = $null
$backendRuntime = $null
$frontendRuntime = $null
try {
    $deadline = (Get-Date).AddSeconds(30)
    $health = $null
    do {
        if ($backend.HasExited) {
            $errorText = if (Test-Path $backendErr) { Get-Content -Raw $backendErr } else { '<no error log>' }
            throw "Backend exited with code $($backend.ExitCode).`n$errorText"
        }
        try { $health = Invoke-RestMethod "$ApiBase/api/health" -TimeoutSec 2 } catch { Start-Sleep -Milliseconds 400 }
    } until (($health.status -eq 'ok' -and $health.application -eq 'memory-agent' -and $health.database_accessible -eq $true) -or (Get-Date) -ge $deadline)
    if (-not $health -or $health.status -ne 'ok') {
        $errorText = if (Test-Path $backendErr) { Get-Content -Raw $backendErr } else { '<no error log>' }
        throw "Backend health check failed within 30 seconds.`n$errorText"
    }
    $backendOwner = Get-PortOwner $BackendPort
    if (-not $backendOwner) { throw "Backend passed health check but no listener owns port $BackendPort." }
    $backendRuntime = Get-Process -Id $backendOwner.PID -ErrorAction Stop

    $frontend = Start-Process -FilePath $python -WorkingDirectory $script:ProjectRoot -ArgumentList @(
        '-m', 'http.server', $FrontendPort, '--bind', '127.0.0.1', '--directory', $script:FrontendRoot
    ) -RedirectStandardOutput $frontendOut -RedirectStandardError $frontendErr -WindowStyle Hidden -PassThru
    $frontendDeadline = (Get-Date).AddSeconds(10)
    $frontendResponse = $null
    do {
        if ($frontend.HasExited) {
            $errorText = if (Test-Path $frontendErr) { Get-Content -Raw $frontendErr } else { '<no error log>' }
            throw "Frontend exited with code $($frontend.ExitCode).`n$errorText"
        }
        try { $frontendResponse = Invoke-WebRequest "$frontendOrigin/" -UseBasicParsing -TimeoutSec 2 } catch { Start-Sleep -Milliseconds 300 }
    } until (($frontendResponse.StatusCode -eq 200) -or (Get-Date) -ge $frontendDeadline)
    if (-not $frontendResponse -or $frontendResponse.StatusCode -ne 200) { throw 'Frontend did not become ready within 10 seconds.' }
    $frontendOwner = Get-PortOwner $FrontendPort
    if (-not $frontendOwner) { throw "Frontend returned HTTP 200 but no listener owns port $FrontendPort." }
    $frontendRuntime = Get-Process -Id $frontendOwner.PID -ErrorAction Stop

    $state = [ordered]@{
        projectRoot = $script:ProjectRoot; apiBase = $ApiBase; frontendUrl = $frontendUrl
        backendPort = $BackendPort; frontendPort = $FrontendPort; version = $health.version
        databasePath = $resolvedDatabase; pythonPath = $python; startedAtUtc = (Get-Date).ToUniversalTime().ToString('o')
        backend = New-ProcessRecord $backendRuntime; frontend = New-ProcessRecord $frontendRuntime
        logs = [ordered]@{ backend = $backendOut; backendError = $backendErr; frontend = $frontendOut; frontendError = $frontendErr }
    }
    $state | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 -LiteralPath $script:StatePath

    Write-Host 'Development services are ready.'
    Write-Host "Backend: $ApiBase (PID $($backendRuntime.Id), version $($health.version))"
    Write-Host "Backend module: $loadedModule"
    Write-Host "Database: $resolvedDatabase"
    Write-Host "Frontend: $frontendUrl (PID $($frontendRuntime.Id))"
    Write-Host "Logs: $script:LogRoot"
    Write-Host 'Stop: powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop-dev.ps1'
} catch {
    Stop-StartedProcess $frontendRuntime
    Stop-StartedProcess $backendRuntime
    Stop-StartedProcess $frontend
    Stop-StartedProcess $backend
    Write-Error $_
    exit 1
}
