param(
    [switch]$PreflightOnly,
    [switch]$Execute,
    [string]$Config = 'config\local.toml'
)
$ErrorActionPreference = 'Stop'
if ($PreflightOnly -eq $Execute) { throw 'Choose exactly one of -PreflightOnly or -Execute.' }
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$configPath = (Resolve-Path -LiteralPath (Join-Path $projectRoot $Config)).Path
$smokeRoot = Join-Path $projectRoot 'data\real-smoke'
New-Item -ItemType Directory -Force -Path $smokeRoot | Out-Null
$planPath = Join-Path $smokeRoot 'plan.json'
$plan = @{
    schema_version = '1.0'; workflow = 'pppflt'
    scenario = @{ mode = 'static'; frequency = 'DF'; ambiguity = 'float'; reset_hours = 0 }
    date_segments = @(@{ year = 2024; start_doy = 350; end_doy = 350 })
    excluded_dates = @(); station_group_id = 'default_15'
    stations = @('S000','S001','S002','S003','S004','S005','S006','S007','S008','S009','S010','S011','S012','S013','S014')
    input_profile_id = 'local_default'; solver_profile_id = 'great_local'; template_profile_id = 'pppflt_base'
    concurrency = 1; outputs = @('flt','log')
}
$plan | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $planPath -Encoding utf8
Push-Location $projectRoot
try {
    if ($PreflightOnly) { & uv run great-agent preflight --config $configPath --plan $planPath }
    else { & uv run great-agent smoke --config $configPath --plan $planPath }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally { Pop-Location }
