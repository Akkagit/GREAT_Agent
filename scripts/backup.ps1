param(
    [Parameter(Mandatory = $true)][ValidateSet('backup', 'verify', 'restore')][string]$Operation,
    [string]$Config,
    [string]$Backup,
    [string]$Destination
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Install the project dependencies before running backup tools.' }
if ($Operation -eq 'backup' -and -not $Config) { throw 'backup requires -Config.' }
if ($Operation -ne 'backup' -and -not $Backup) { throw 'verify/restore requires -Backup.' }
if ($Operation -ne 'verify' -and -not $Destination) { throw 'backup/restore requires -Destination.' }
$arguments = @('-m', 'great_agent.services.backup_service', $Operation)
if ($Config) { $arguments += @('--config', $Config) }
if ($Backup) { $arguments += @('--backup', $Backup) }
if ($Destination) { $arguments += @('--destination', $Destination) }
Push-Location -LiteralPath $projectRoot
try {
    & $python @arguments
    $result = $LASTEXITCODE
}
finally { Pop-Location }
exit $result
