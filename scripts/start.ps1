$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$configPath = Join-Path $projectRoot 'config\local.toml'
$frontendDist = Join-Path $projectRoot 'frontend\dist'
$pidRoot = Join-Path $projectRoot 'data\pids'
if (-not (Test-Path -LiteralPath $configPath)) {
    throw "Missing $configPath. Copy config\app.example.toml first."
}
if (-not (Test-Path -LiteralPath $frontendDist)) {
    & npm --prefix (Join-Path $projectRoot 'frontend') run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
}
Push-Location $projectRoot
try { & uv sync; if ($LASTEXITCODE -ne 0) { throw 'uv sync failed.' } }
finally { Pop-Location }
New-Item -ItemType Directory -Force -Path $pidRoot | Out-Null
$launcher = Join-Path $projectRoot '.venv\Scripts\great-agent.exe'
if (-not (Test-Path -LiteralPath $launcher)) { throw "Missing launcher: $launcher" }

$api = Start-Process -FilePath $launcher -ArgumentList @('api', '--config', $configPath) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
$worker = Start-Process -FilePath $launcher -ArgumentList @('worker', '--config', $configPath) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
Set-Content -LiteralPath (Join-Path $pidRoot 'api.pid') -Value $api.Id -Encoding ascii
Set-Content -LiteralPath (Join-Path $pidRoot 'worker.pid') -Value $worker.Id -Encoding ascii
Start-Process 'http://127.0.0.1:8765/'
Write-Host "GREAT Agent started. API PID=$($api.Id), worker PID=$($worker.Id)"
