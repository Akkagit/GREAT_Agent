param(
    [string]$Report = '',
    [string]$Python = '',
    [ValidateRange(1, 86400)][int]$TimeoutSeconds = 900
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (-not $Python) { $Python = Join-Path $projectRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Missing Python: $Python. Install locked dependencies before verification."
}
$verifyArguments = @((Join-Path $PSScriptRoot 'verify.py'), '--timeout-seconds', $TimeoutSeconds)
if ($Report) { $verifyArguments += @('--report', $Report) }
# Python owns the command logs, timeouts and structured report. Preserve its exit
# code so a failed gate also fails a caller's release or CI step.
& $Python @verifyArguments
exit $LASTEXITCODE
