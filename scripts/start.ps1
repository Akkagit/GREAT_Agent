param(
    [string]$Config = 'config\local.toml',
    [switch]$NoBrowser,
    [switch]$NoBuild,
    [switch]$ApiOnly,
    [ValidateRange(5, 600)][int]$HealthTimeoutSeconds = 60
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing $python. Install dependencies explicitly before starting."
}
# Keep an explicit process value; otherwise inherit the user's saved key.
# Child API/Worker processes receive the value without writing it to disk.
if (-not $env:GREAT_AGENT_MODEL_API_KEY) {
    $env:GREAT_AGENT_MODEL_API_KEY = [Environment]::GetEnvironmentVariable('GREAT_AGENT_MODEL_API_KEY', 'User')
}
if (-not $env:GREAT_AGENT_RESEARCH_MODEL_API_KEY) {
    $env:GREAT_AGENT_RESEARCH_MODEL_API_KEY = [Environment]::GetEnvironmentVariable('GREAT_AGENT_RESEARCH_MODEL_API_KEY', 'User')
}
$arguments = @((Join-Path $PSScriptRoot 'local_startup.py'), '--config', $Config,
    '--health-timeout', $HealthTimeoutSeconds)
if ($NoBrowser) { $arguments += '--no-browser' }
if ($NoBuild) { $arguments += '--no-build' }
if ($ApiOnly) { $arguments += '--api-only' }
# A normal launch never installs or downloads dependencies.
Push-Location -LiteralPath $projectRoot
try {
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw "Startup failed (exit $LASTEXITCODE); see the reported release logs." }
}
finally { Pop-Location }
