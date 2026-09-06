$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$ciUserName = if ([string]::IsNullOrWhiteSpace($env:USERNAME)) { 'ci' } else { $env:USERNAME }
$safeCiUserName = $ciUserName -replace '[^A-Za-z0-9_.-]', '_'
$pytestTempRoot = Join-Path '.pytest_tmp' $safeCiUserName
New-Item -ItemType Directory -Path $pytestTempRoot -Force | Out-Null

function Invoke-UvStep {
    param(
        [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)]
        [string[]]$UvArguments
    )

    & uv @UvArguments
    $stepExitCode = $LASTEXITCODE
    if ($stepExitCode -ne 0) {
        throw "uv $($UvArguments -join ' ') failed with exit code $stepExitCode"
    }
}

Invoke-UvStep sync --dev --locked --no-editable --reinstall-package telegram-trading-monitor
Invoke-UvStep run --no-sync ruff check .
Invoke-UvStep run --no-sync ruff format --check .
Invoke-UvStep run --no-sync mypy
$pytestArguments = @(
    'run', '--no-sync', 'pytest', '-m', 'not integration',
    '--basetemp', (Join-Path $pytestTempRoot 'run'),
    '-o', "cache_dir=$(Join-Path $pytestTempRoot 'cache')"
)
Invoke-UvStep -UvArguments $pytestArguments
Invoke-UvStep run --no-sync python scripts/secret_scan.py --root .
Invoke-UvStep run --no-sync pip-audit . --strict --progress-spinner=off
