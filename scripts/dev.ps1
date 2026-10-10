$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$configPath = Join-Path $projectRoot 'config\local.toml'
if (-not (Test-Path -LiteralPath $configPath)) {
    throw "Missing $configPath. Copy config\app.example.toml first."
}

$quotedRoot = $projectRoot.Replace("'", "''")
$quotedConfig = $configPath.Replace("'", "''")
Start-Process pwsh -ArgumentList @('-NoExit', '-Command', "Set-Location -LiteralPath '$quotedRoot'; uv run great-agent api --config '$quotedConfig'")
Start-Process pwsh -ArgumentList @('-NoExit', '-Command', "Set-Location -LiteralPath '$quotedRoot'; uv run great-agent worker --config '$quotedConfig'")
Start-Process pwsh -ArgumentList @('-NoExit', '-Command', "Set-Location -LiteralPath '$quotedRoot'; npm --prefix frontend run dev")
Write-Host 'Development API, worker, and Vite logs opened in separate terminals.'
