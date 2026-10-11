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
    stations = @(
        'AHB0','BJB0','GDA4','GSE4','GXC4',
        'HBB2','HJE2','HNB1','JL18','LNC1',
        'QHA4','SCI5','SD10','SXE4','YNJ8'
    )
    input_profile_id = 'local_default'; solver_profile_id = 'great_local'; template_profile_id = 'pppflt_base'
    concurrency = 1; outputs = @('flt','log')
}
$planJson = $plan | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText($planPath, $planJson, [System.Text.UTF8Encoding]::new($false))
Push-Location $projectRoot
try {
    if ($PreflightOnly) { & uv run great-agent preflight --config $configPath --plan $planPath }
    else { & uv run great-agent smoke --config $configPath --plan $planPath }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally { Pop-Location }
