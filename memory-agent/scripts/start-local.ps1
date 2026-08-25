param(
    [ValidateRange(1, 65535)][int]$BackendPort = 8001,
    [ValidateRange(1, 65535)][int]$FrontendPort = 8080,
    [string]$ApiBase = $env:MEMORY_AGENT_API_BASE
)

Write-Warning 'start-local.ps1 is a compatibility alias; use start-dev.ps1 for the documented workflow.'
& (Join-Path $PSScriptRoot 'start-dev.ps1') -BackendPort $BackendPort -FrontendPort $FrontendPort -ApiBase $ApiBase
exit $LASTEXITCODE
