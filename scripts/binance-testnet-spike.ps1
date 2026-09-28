$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:UV_PROJECT_ENVIRONMENT = '.venv-ci'

function Read-DotEnvValue {
    # Reads one KEY=VALUE line from a local, git-ignored .env file. Never echoes the value.
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Key
    )
    if (-not (Test-Path $Path)) { return $null }
    $line = Get-Content $Path -ErrorAction SilentlyContinue |
        Where-Object { $_ -match "^\s*$Key\s*=" } |
        Select-Object -Last 1
    if (-not $line) { return $null }
    $value = ($line -replace "^\s*$Key\s*=", '').Trim()
    if ($value.Length -ge 2 -and $value.StartsWith('"') -and $value.EndsWith('"')) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    if ([string]::IsNullOrWhiteSpace($value)) { return $null }
    return $value
}

$dotEnvPath = Join-Path $PSScriptRoot '..\.env'
$apiKeyPlain = Read-DotEnvValue -Path $dotEnvPath -Key 'BINANCE_TESTNET_API_KEY'
$apiSecretPlain = Read-DotEnvValue -Path $dotEnvPath -Key 'BINANCE_TESTNET_API_SECRET'
$baseUrlPlain = Read-DotEnvValue -Path $dotEnvPath -Key 'BINANCE_TESTNET_BASE_URL'

if ($apiKeyPlain -and $apiSecretPlain) {
    Write-Host 'Using BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET from local .env (not shown).'
}
else {
    throw 'BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET are missing from .env. Fill them in first (see .env.example).'
}

try {
    $env:BINANCE_TESTNET_API_KEY = $apiKeyPlain
    $env:BINANCE_TESTNET_API_SECRET = $apiSecretPlain
    if ($baseUrlPlain) {
        $env:BINANCE_TESTNET_BASE_URL = $baseUrlPlain
    }

    & uv run --no-sync python scripts/binance_testnet_spike.py
    $commandExitCode = $LASTEXITCODE
    if ($commandExitCode -ne 0) {
        throw "Binance Testnet spike failed with exit code $commandExitCode"
    }
}
finally {
    Remove-Item Env:BINANCE_TESTNET_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:BINANCE_TESTNET_API_SECRET -ErrorAction SilentlyContinue
    Remove-Item Env:BINANCE_TESTNET_BASE_URL -ErrorAction SilentlyContinue
    $apiKeyPlain = $null
    $apiSecretPlain = $null
    $baseUrlPlain = $null
}
