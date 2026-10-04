# Registers (or removes) the Windows Task Scheduler task that keeps the analysis-setup worker
# running: starts at logon, hidden, no time limit, restarts if it dies.
#
#   powershell -File scripts\register-setup-worker-task.ps1            # register and start
#   powershell -File scripts\register-setup-worker-task.ps1 -Remove    # stop and unregister

param([switch]$Remove)

$ErrorActionPreference = 'Stop'
$taskName = 'TelegramTrader-SetupWorker'

if ($Remove) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Remove-Item (Join-Path (Join-Path $env:USERPROFILE '.telegram-trader') 'run-setup-worker-stub.ps1') -ErrorAction SilentlyContinue
        Write-Host "Removed task $taskName."
    }
    else { Write-Host "Task $taskName is not registered." }
    return
}

# Task Scheduler passes the command line to powershell.exe in the ANSI code page, which mangles a
# project path containing Chinese characters, so the task points at an ASCII-only stub whose
# *contents* (UTF-8 with BOM) hold the real path. The stub lives under %USERPROFILE%, not AppData:
# a packaged app (like this desktop client) gets a private, virtualized AppData that scheduled
# tasks cannot see.
$stubDir = Join-Path $env:USERPROFILE '.telegram-trader'
New-Item -ItemType Directory -Path $stubDir -Force | Out-Null
$script = Join-Path $stubDir 'run-setup-worker-stub.ps1'
$real = Join-Path $PSScriptRoot 'run-setup-worker.ps1'
$quotedReal = $real.Replace("'", "''")
$stub = @"
`$breadcrumb = Join-Path (Split-Path `$PSCommandPath -Parent) 'stub.log'
try {
    "[`$(Get-Date -Format o)] starting" | Out-File `$breadcrumb -Append -Encoding utf8
    & '$quotedReal'
} catch {
    "[`$(Get-Date -Format o)] failed: `$_" | Out-File `$breadcrumb -Append -Encoding utf8
}
"@
[System.IO.File]::WriteAllText($script, $stub, (New-Object System.Text.UTF8Encoding($true)))
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Analysis-setup worker: AI describes analyst posts, code monitors the market.' `
    -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Host "Registered and started task $taskName. Log: logs\setup-worker.log"
