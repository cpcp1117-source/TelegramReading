param(
    [ValidateSet('login', 'dialogs', 'discover-private', 'preview', 'collect', 'control-bot')]
    [string]$Command = 'login'
)

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
$apiIdInput = Read-DotEnvValue -Path $dotEnvPath -Key 'TELEGRAM_API_ID'
$apiHashPlain = Read-DotEnvValue -Path $dotEnvPath -Key 'TELEGRAM_API_HASH'
$apiHashSecure = $null

if ($apiIdInput -and $apiHashPlain) {
    Write-Host 'Using TELEGRAM_API_ID / TELEGRAM_API_HASH from local .env (not shown).'
}
else {
    if (-not $apiIdInput) {
        $apiIdInput = Read-Host 'Telegram API ID (terminal only)'
    }
    if (-not $apiHashPlain) {
        $apiHashSecure = Read-Host 'Telegram API Hash (hidden; terminal only)' -AsSecureString
        $apiHashPlain = [System.Net.NetworkCredential]::new('', $apiHashSecure).Password
    }
}
$databasePasswordSecure = $null
$databasePasswordPlain = $null
$needsDatabase = $Command -in @('collect', 'control-bot')

if ($needsDatabase) {
    $databasePasswordPlain = Read-DotEnvValue -Path $dotEnvPath -Key 'POSTGRES_PASSWORD'
    if ($databasePasswordPlain) {
        Write-Host 'Using POSTGRES_PASSWORD from local .env (not shown).'
    }
    else {
        $databasePasswordSecure = Read-Host 'PostgreSQL password (hidden; terminal only)' -AsSecureString
        $databasePasswordPlain = [System.Net.NetworkCredential]::new('', $databasePasswordSecure).Password
    }
}

$controlBotTokenPlain = $null
$controlBotTokenSecure = $null
$controlBotUserIdInput = $null

if ($Command -eq 'control-bot') {
    $controlBotTokenPlain = Read-DotEnvValue -Path $dotEnvPath -Key 'CONTROL_BOT_TOKEN'
    if ($controlBotTokenPlain) {
        Write-Host 'Using CONTROL_BOT_TOKEN from local .env (not shown).'
    }
    else {
        $controlBotTokenSecure = Read-Host 'Control Bot token (hidden; terminal only)' -AsSecureString
        $controlBotTokenPlain = [System.Net.NetworkCredential]::new('', $controlBotTokenSecure).Password
    }
    $controlBotUserIdInput = Read-DotEnvValue -Path $dotEnvPath -Key 'CONTROL_BOT_ALLOWLISTED_USER_ID'
    if ($controlBotUserIdInput) {
        Write-Host 'Using CONTROL_BOT_ALLOWLISTED_USER_ID from local .env.'
    }
    else {
        $controlBotUserIdInput = Read-Host 'Your numeric Telegram user ID (the only allowlisted approver)'
    }
}

try {
    $parsedApiId = 0
    if (-not [int]::TryParse($apiIdInput, [ref]$parsedApiId) -or $parsedApiId -le 0) {
        throw 'Telegram API ID must be a positive integer.'
    }
    if ([string]::IsNullOrWhiteSpace($apiHashPlain)) {
        throw 'Telegram API Hash cannot be blank.'
    }
    if ($needsDatabase -and [string]::IsNullOrWhiteSpace($databasePasswordPlain)) {
        throw 'PostgreSQL password cannot be blank.'
    }
    $parsedControlBotUserId = 0
    if ($Command -eq 'control-bot') {
        if ([string]::IsNullOrWhiteSpace($controlBotTokenPlain)) {
            throw 'Control Bot token cannot be blank.'
        }
        if (-not [int64]::TryParse($controlBotUserIdInput, [ref]$parsedControlBotUserId) -or $parsedControlBotUserId -le 0) {
            throw 'Control Bot allowlisted user ID must be a positive integer.'
        }
    }

    $env:TELEGRAM_API_ID = $parsedApiId.ToString()
    $env:TELEGRAM_API_HASH = $apiHashPlain

    if ($Command -eq 'collect') {
        $env:APP_ENVIRONMENT = 'telegram_readonly'
        $env:TELEGRAM_SESSION_PATH = 'secrets/telegram/collector'
        $env:POSTGRES_PASSWORD = $databasePasswordPlain
        & docker compose --profile telegram build collector
        if ($LASTEXITCODE -ne 0) {
            throw "Collector image build failed with exit code $LASTEXITCODE"
        }
        & docker compose --profile telegram run --rm collector alembic upgrade head
        if ($LASTEXITCODE -ne 0) {
            throw "Database migration failed with exit code $LASTEXITCODE"
        }
        & docker compose --profile telegram run --rm collector
    }
    elseif ($Command -eq 'control-bot') {
        $env:APP_ENVIRONMENT = 'control_bot'
        $env:POSTGRES_PASSWORD = $databasePasswordPlain
        $env:CONTROL_BOT_TOKEN = $controlBotTokenPlain
        $env:CONTROL_BOT_ALLOWLISTED_USER_ID = $parsedControlBotUserId.ToString()
        & docker compose --profile control-bot build control-bot
        if ($LASTEXITCODE -ne 0) {
            throw "Control Bot image build failed with exit code $LASTEXITCODE"
        }
        & docker compose --profile control-bot run --rm control-bot alembic upgrade head
        if ($LASTEXITCODE -ne 0) {
            throw "Database migration failed with exit code $LASTEXITCODE"
        }
        & docker compose --profile control-bot run --rm control-bot
    }
    else {
        $env:APP_ENVIRONMENT = 'telegram_readonly'
        $env:TELEGRAM_SESSION_PATH = 'secrets/telegram/collector'
        & uv run --no-sync python -m telegram_trader.telegram_cli $Command
    }
    $commandExitCode = $LASTEXITCODE
    if ($commandExitCode -ne 0) {
        throw "Telegram bootstrap failed with exit code $commandExitCode"
    }
}
finally {
    Remove-Item Env:TELEGRAM_API_ID -ErrorAction SilentlyContinue
    Remove-Item Env:TELEGRAM_API_HASH -ErrorAction SilentlyContinue
    Remove-Item Env:TELEGRAM_SESSION_PATH -ErrorAction SilentlyContinue
    Remove-Item Env:APP_ENVIRONMENT -ErrorAction SilentlyContinue
    Remove-Item Env:POSTGRES_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:CONTROL_BOT_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:CONTROL_BOT_ALLOWLISTED_USER_ID -ErrorAction SilentlyContinue
    $apiHashPlain = $null
    $apiHashSecure = $null
    $apiIdInput = $null
    $databasePasswordPlain = $null
    $databasePasswordSecure = $null
    $controlBotTokenPlain = $null
    $controlBotTokenSecure = $null
    $controlBotUserIdInput = $null
}
