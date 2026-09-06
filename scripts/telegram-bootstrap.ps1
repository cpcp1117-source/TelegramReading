param(
    [ValidateSet('login', 'dialogs', 'collect')]
    [string]$Command = 'login'
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:UV_PROJECT_ENVIRONMENT = '.venv-ci'

$apiIdInput = Read-Host 'Telegram API ID (terminal only)'
$apiHashSecure = Read-Host 'Telegram API Hash (hidden; terminal only)' -AsSecureString
$apiHashPlain = [System.Net.NetworkCredential]::new('', $apiHashSecure).Password
$databasePasswordSecure = $null
$databasePasswordPlain = $null

if ($Command -eq 'collect') {
    $databasePasswordSecure = Read-Host 'PostgreSQL password (hidden; terminal only)' -AsSecureString
    $databasePasswordPlain = [System.Net.NetworkCredential]::new('', $databasePasswordSecure).Password
}

try {
    $parsedApiId = 0
    if (-not [int]::TryParse($apiIdInput, [ref]$parsedApiId) -or $parsedApiId -le 0) {
        throw 'Telegram API ID must be a positive integer.'
    }
    if ([string]::IsNullOrWhiteSpace($apiHashPlain)) {
        throw 'Telegram API Hash cannot be blank.'
    }
    if ($Command -eq 'collect' -and [string]::IsNullOrWhiteSpace($databasePasswordPlain)) {
        throw 'PostgreSQL password cannot be blank.'
    }

    $env:APP_ENVIRONMENT = 'telegram_readonly'
    $env:TELEGRAM_API_ID = $parsedApiId.ToString()
    $env:TELEGRAM_API_HASH = $apiHashPlain
    $env:TELEGRAM_SESSION_PATH = 'secrets/telegram/collector'
    $env:TELEGRAM_TARGET_USERNAME = 'followgerry'
    $env:TELEGRAM_TARGET_CHANNEL_ID = '2439599598'

    if ($Command -eq 'collect') {
        $env:POSTGRES_PASSWORD = $databasePasswordPlain
        & docker compose --profile telegram run --rm collector alembic upgrade head
        if ($LASTEXITCODE -ne 0) {
            throw "Database migration failed with exit code $LASTEXITCODE"
        }
        & docker compose --profile telegram run --rm collector
    }
    else {
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
    Remove-Item Env:TELEGRAM_TARGET_USERNAME -ErrorAction SilentlyContinue
    Remove-Item Env:TELEGRAM_TARGET_CHANNEL_ID -ErrorAction SilentlyContinue
    Remove-Item Env:APP_ENVIRONMENT -ErrorAction SilentlyContinue
    Remove-Item Env:POSTGRES_PASSWORD -ErrorAction SilentlyContinue
    $apiHashPlain = $null
    $apiHashSecure = $null
    $apiIdInput = $null
    $databasePasswordPlain = $null
    $databasePasswordSecure = $null
}
