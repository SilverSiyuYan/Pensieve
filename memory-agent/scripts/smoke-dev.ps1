param([ValidateRange(1, 20)][int]$Cycles = 5)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'dev-common.ps1')

if (Get-PortOwner 8001 -or Get-PortOwner 8080) { throw 'Smoke test requires ports 8001 and 8080 to be free.' }
$databaseHashBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $script:DatabasePath).Hash
$sourceVersion = & $script:VenvPython -c 'import sys; sys.path.insert(0, sys.argv[1]); import app_meta; print(app_meta.APP_VERSION)' $script:BackendRoot
if ($LASTEXITCODE -ne 0) { throw 'Could not read source version.' }

$originalLocation = Get-Location
$otherDirectory = [IO.Path]::GetTempPath()
try {
    Set-Location $otherDirectory
    for ($cycle = 1; $cycle -le $Cycles; $cycle++) {
        Write-Host "Smoke cycle $cycle/${Cycles}: starting from $otherDirectory"
        & (Join-Path $PSScriptRoot 'start-dev.ps1')
        $state = Read-DevState
        if (-not $state) { throw "Cycle ${cycle} did not create state." }
        $health4 = Invoke-RestMethod 'http://127.0.0.1:8001/api/health' -TimeoutSec 5
        $healthLocalhost = Invoke-RestMethod 'http://localhost:8001/api/health' -TimeoutSec 5
        $schema = Invoke-RestMethod 'http://127.0.0.1:8001/openapi.json' -TimeoutSec 5
        if ($health4.version -ne $sourceVersion -or $healthLocalhost.version -ne $sourceVersion -or $schema.info.version -ne $sourceVersion) {
            throw "Cycle ${cycle} version mismatch."
        }
        if (-not $health4.database_accessible -or [IO.Path]::GetFullPath($state.databasePath) -ne [IO.Path]::GetFullPath($script:DatabasePath)) {
            throw "Cycle ${cycle} database path or health mismatch."
        }
        & (Join-Path $PSScriptRoot 'stop-dev.ps1')
        if (Get-PortOwner 8001 -or Get-PortOwner 8080) { throw "Cycle ${cycle} left a listening port." }
        if (Test-Path -LiteralPath $script:StatePath) { throw "Cycle ${cycle} left a state file." }
        Write-Host "Smoke cycle $cycle/${Cycles}: PASS"
    }
} finally {
    Set-Location $originalLocation
    $state = Read-DevState
    if ($state) { & (Join-Path $PSScriptRoot 'stop-dev.ps1') }
}

$databaseHashAfter = (Get-FileHash -Algorithm SHA256 -LiteralPath $script:DatabasePath).Hash
if ($databaseHashBefore -ne $databaseHashAfter) { throw 'Database file changed during start/stop smoke cycles.' }
Write-Host "$Cycles/$Cycles smoke cycles passed; database SHA-256 is unchanged."
