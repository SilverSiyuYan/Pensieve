$ErrorActionPreference = 'Stop'

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtimeRoot = Join-Path $projectRoot 'runtime'
$pidFile = Join-Path $runtimeRoot 'pids.json'

function Get-ProcessCommandLine([int]$ProcessId) {
    try {
        return [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop).CommandLine
    } catch {
        return $null
    }
}

function Test-RecordedProcess($Record) {
    if (-not $Record -or -not $Record.pid -or -not $Record.startedAtUtc -or
        -not $Record.executablePath -or -not $Record.expectedCommandFeature) {
        return $false
    }

    $process = Get-Process -Id ([int]$Record.pid) -ErrorAction SilentlyContinue
    if (-not $process) { return $false }

    try {
        $recordedStart = ([DateTimeOffset]::Parse([string]$Record.startedAtUtc)).UtcDateTime
        $actualStart = $process.StartTime.ToUniversalTime()
        $sameStart = [Math]::Abs(($actualStart - $recordedStart).TotalSeconds) -lt 0.01
        $sameExecutable = [IO.Path]::GetFullPath($process.Path) -eq [IO.Path]::GetFullPath([string]$Record.executablePath)
        $commandLine = Get-ProcessCommandLine ([int]$Record.pid)
        $hasFeature = $commandLine -and $commandLine.IndexOf(
            [string]$Record.expectedCommandFeature,
            [StringComparison]::OrdinalIgnoreCase
        ) -ge 0
        return $sameStart -and $sameExecutable -and $hasFeature
    } catch {
        return $false
    }
}

function Get-DescendantSnapshot([int]$RootPid) {
    $result = [Collections.Generic.List[object]]::new()
    $visited = [Collections.Generic.HashSet[int]]::new()
    [void]$visited.Add($RootPid)
    function Add-Children([int]$ParentPid, [int]$Depth) {
        try {
            $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$ParentPid" -ErrorAction Stop)
        } catch {
            $children = @()
        }
        foreach ($child in $children) {
            $childPid = [int]$child.ProcessId
            if ($childPid -le 0 -or -not $visited.Add($childPid)) { continue }
            $result.Add([pscustomobject]@{
                pid = $childPid
                parentPid = $ParentPid
                creationDate = [string]$child.CreationDate
                depth = $Depth
            })
            Add-Children $childPid ($Depth + 1)
        }
    }
    Add-Children $RootPid 1
    return @($result)
}

function Test-SnapshotProcess($Snapshot) {
    try {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$Snapshot.pid)" -ErrorAction Stop
        return $current -and [string]$current.CreationDate -eq [string]$Snapshot.creationDate
    } catch {
        return $false
    }
}

function Stop-ValidatedTree($Record) {
    if (-not (Test-RecordedProcess $Record)) { return $false }

    $rootPid = [int]$Record.pid
    $descendants = @(Get-DescendantSnapshot $rootPid | Sort-Object depth -Descending)
    foreach ($child in $descendants) {
        if (-not (Test-RecordedProcess $Record)) {
            Write-Warning "Root PID $rootPid changed while stopping; remaining processes were not touched."
            return $false
        }
        if (Test-SnapshotProcess $child) {
            Write-Host "Stopping child PID $($child.pid) of recorded $($Record.service) PID $rootPid."
            Stop-Process -Id ([int]$child.pid) -ErrorAction SilentlyContinue
            Wait-Process -Id ([int]$child.pid) -Timeout 5 -ErrorAction SilentlyContinue
        }
    }

    if (-not (Test-RecordedProcess $Record)) {
        if (-not (Get-Process -Id $rootPid -ErrorAction SilentlyContinue)) { return $true }
        Write-Warning "Recorded $($Record.service) PID $rootPid no longer matches its identity; it was not stopped."
        return $false
    }

    Write-Host "Stopping recorded $($Record.service) PID $rootPid."
    Stop-Process -Id $rootPid -ErrorAction SilentlyContinue
    Wait-Process -Id $rootPid -Timeout 10 -ErrorAction SilentlyContinue
    return -not [bool](Get-Process -Id $rootPid -ErrorAction SilentlyContinue)
}

if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
    Write-Host 'No Pensieve PID state was found. Nothing was stopped.'
    exit 0
}

try {
    $parsedRecords = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
    $records = @($parsedRecords | ForEach-Object { $_ })
} catch {
    Write-Warning "Pensieve PID state is unreadable and cannot be trusted: $pidFile"
    Write-Warning 'No process was stopped. Delete the malformed file only after manually confirming that no recorded service is still needed.'
    exit 1
}

$remaining = [Collections.Generic.List[object]]::new()
foreach ($record in $records) {
    if (-not (Test-RecordedProcess $record)) {
        Write-Host "Skipping stale or mismatched PID record: PID $($record.pid), service $($record.service)."
        continue
    }

    if (-not (Stop-ValidatedTree $record)) {
        if (Test-RecordedProcess $record) {
            Write-Warning "Recorded $($record.service) PID $($record.pid) is still running and remains in the PID state."
            $remaining.Add($record)
        }
    }
}

if ($remaining.Count -eq 0) {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-Host 'Pensieve stop completed. No valid recorded process remains.'
} else {
    $temporaryPidFile = "$pidFile.tmp"
    ConvertTo-Json -InputObject @($remaining) -Depth 4 | Set-Content -Encoding UTF8 -LiteralPath $temporaryPidFile
    Move-Item -Force -LiteralPath $temporaryPidFile -Destination $pidFile
    Write-Warning "$($remaining.Count) validated process record(s) remain in $pidFile."
}
