# Runs the analysis-setup worker on the host (next to the logged-in Codex CLI) and restarts it
# if it ever exits. Started by the Windows Task Scheduler task registered by
# scripts/register-setup-worker-task.ps1; can also be run by hand.
#
# Needs: the db published on 127.0.0.1:5432 (compose.host-db.yaml), `codex` logged in.
# Secrets are read from the git-ignored .env and never printed or logged.

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

function Read-DotEnvValue {
    param([string]$Key)
    $line = Get-Content (Join-Path $root '.env') -ErrorAction SilentlyContinue |
        Where-Object { $_ -match "^\s*$Key\s*=" } | Select-Object -Last 1
    if (-not $line) { return $null }
    $value = ($line -replace "^\s*$Key\s*=", '').Trim()
    if ($value.Length -ge 2 -and $value.StartsWith('"') -and $value.EndsWith('"')) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    return $value
}

$password = Read-DotEnvValue -Key 'POSTGRES_PASSWORD'
if (-not $password) { throw 'POSTGRES_PASSWORD not found in .env' }

$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = Join-Path $root 'src'
$env:AI_PROVIDER = 'codex'
$env:APP_ENVIRONMENT = 'offline'
$env:APP_DATABASE_HOST = '127.0.0.1'
$env:APP_DATABASE_PORT = '5432'
$env:APP_DATABASE_NAME = 'telegram_trader'
$env:APP_DATABASE_USER = 'postgres'
$env:APP_DATABASE_PASSWORD = $password
# Task Scheduler may start with a thinner PATH than an interactive shell; `codex` lives here.
$env:PATH = "$env:APPDATA\npm;$env:PATH"

$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logFile = Join-Path $logDir 'setup-worker.log'
$python = Join-Path $root '.venv\Scripts\python.exe'

while ($true) {
    if ((Test-Path $logFile) -and ((Get-Item $logFile).Length -gt 20MB)) {
        Move-Item $logFile "$logFile.1" -Force
    }
    # Python logs to stderr; under PowerShell 5.1 that becomes ErrorRecords, which must not
    # abort the script, so errors are made non-terminating around the call.
    $ErrorActionPreference = 'Continue'
    & $python -m telegram_trader.setup_worker 2>&1 |
        ForEach-Object { "$_" } | Out-File -FilePath $logFile -Append -Encoding utf8
    $ErrorActionPreference = 'Stop'
    "[$(Get-Date -Format o)] setup worker exited with code $LASTEXITCODE; restarting in 30s" |
        Out-File -FilePath $logFile -Append -Encoding utf8
    Start-Sleep -Seconds 30
}
