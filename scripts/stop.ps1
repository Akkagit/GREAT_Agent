$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pidRoot = Join-Path $projectRoot 'data\pids'
foreach ($name in @('api', 'worker')) {
    $pidPath = Join-Path $pidRoot "$name.pid"
    if (-not (Test-Path -LiteralPath $pidPath)) { continue }
    $savedPid = [int](Get-Content -LiteralPath $pidPath -Raw).Trim()
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $savedPid" -ErrorAction SilentlyContinue
    if ($null -ne $process) {
        if ($process.CommandLine -notmatch 'great[-_]agent') {
            throw "PID $savedPid no longer belongs to GREAT Agent; refusing to stop it."
        }
        Stop-Process -Id $savedPid
        Write-Host "Stopped $name PID $savedPid"
    }
    Remove-Item -LiteralPath $pidPath
}
