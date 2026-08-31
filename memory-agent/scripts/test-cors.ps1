param(
    [string]$ApiBase = 'http://127.0.0.1:8001',
    [string]$Origin = 'http://127.0.0.1:8080'
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'dev-common.ps1')

$apiUri = [Uri]$ApiBase
if (-not $apiUri.IsAbsoluteUri -or $apiUri.Scheme -notin @('http', 'https')) {
    throw "ApiBase must be an absolute HTTP(S) URL: $ApiBase"
}
$ApiBase = $ApiBase.TrimEnd('/')
$backendOwner = Get-PortOwner $apiUri.Port
if ($backendOwner) { $backendOwner | Format-List | Out-Host } else { throw "Port $($apiUri.Port) is not listening." }

function Show-CorsResponse($Label, $Response) {
    Write-Host $Label
    [pscustomobject]@{
        Status = [int]$Response.StatusCode
        AllowOrigin = $Response.Headers['Access-Control-Allow-Origin']
        AllowCredentials = $Response.Headers['Access-Control-Allow-Credentials']
        AllowMethods = $Response.Headers['Access-Control-Allow-Methods']
        AllowHeaders = $Response.Headers['Access-Control-Allow-Headers']
        Vary = $Response.Headers['Vary']
        Location = $Response.Headers['Location']
        ContentType = $Response.Headers['Content-Type']
    } | Format-List | Out-Host
}

$preflight = Invoke-WebRequest "$ApiBase/api/memory/auto" -Method Options -UseBasicParsing -MaximumRedirection 0 -Headers @{
    Origin = $Origin
    'Access-Control-Request-Method' = 'POST'
    'Access-Control-Request-Headers' = 'authorization,content-type'
}
Show-CorsResponse 'CORS preflight:' $preflight

$actual = Invoke-WebRequest "$ApiBase/api/health" -Method Get -UseBasicParsing -MaximumRedirection 0 -Headers @{ Origin = $Origin }
Show-CorsResponse 'CORS actual GET:' $actual
$health = $actual.Content | ConvertFrom-Json
Write-Host "Backend application: $($health.application)"
Write-Host "Backend version: $($health.version)"
Write-Host "Database accessible: $($health.database_accessible)"

if ($preflight.StatusCode -ne 200) { throw "Preflight failed with HTTP $($preflight.StatusCode)." }
if ($preflight.Headers['Access-Control-Allow-Origin'] -ne $Origin) { throw 'Preflight Allow-Origin does not exactly match Origin.' }
if ($actual.StatusCode -ne 200) { throw "Actual request failed with HTTP $($actual.StatusCode)." }
if ($actual.Headers['Access-Control-Allow-Origin'] -ne $Origin) { throw 'Actual response Allow-Origin does not exactly match Origin.' }
if ($health.application -ne 'memory-agent' -or -not $health.version) { throw 'Health response is not the expected Pensieve backend.' }

Write-Host 'CORS smoke test: PASS'
