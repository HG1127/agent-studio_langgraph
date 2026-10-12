# Install or inspect a user-session Windows Task Scheduler CI poller.
# Uses existing GitHub CLI authentication; no password is stored by this script.
# Runs only while the current Windows user is logged in.

[CmdletBinding()]
param(
    [ValidateSet('Check','Install','Remove')]
    [string]$Action = 'Check'
)

$ErrorActionPreference = 'Stop'
$taskName = 'AgentStudio-Develop-CI'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$scriptFile = Join-Path $PSScriptRoot 'ci-watch.ps1'
$exe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$expectedArgs = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $scriptFile + '"'

if (-not (Test-Path -LiteralPath $scriptFile)) {
    throw 'ci-watch.ps1 is missing'
}
if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) {
    throw 'Windows ScheduledTasks PowerShell module is unavailable'
}

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($Action -eq 'Check') {
    if (-not $existing) {
        Write-Host 'CI_SCHEDULE_STATUS=not_installed'
        return
    }
    $configured = @($existing.Actions)[0]
    if ($configured.Execute -ne $exe -or $configured.Arguments -ne $expectedArgs) {
        throw 'Task name exists with different action; refusing to change it'
    }
    Write-Host 'CI_SCHEDULE_STATUS=installed'
    Write-Host ('CI_TASK_STATE=' + $existing.State)
    Write-Host 'CI_INTERVAL=15_minutes_while_user_logged_in'
    return
}
if ($Action -eq 'Remove') {
    if ($existing) {
        $configured = @($existing.Actions)[0]
        if ($configured.Execute -ne $exe -or $configured.Arguments -ne $expectedArgs) {
            throw 'Task name exists with different action; refusing to delete it'
        }
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host 'CI_SCHEDULE_STATUS=removed'
    } else {
        Write-Host 'CI_SCHEDULE_STATUS=not_installed'
    }
    return
}

if ($existing) {
    throw 'Task name already exists. Use -Action Check; do not overwrite it.'
}
# Running repository code automatically under a Windows administrator is unsafe.
# This is only a source-worktree boundary, NOT a process/security sandbox.
$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$isElevated = ([Security.Principal.WindowsPrincipal]::new($currentIdentity)).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if ($isElevated -or $currentIdentity.User.Value -match '-500$') {
    throw 'Install requires a dedicated non-admin Windows CI account. Use -Action Check or manual ci.ps1 on this administrator session.'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$taskAction = New-ScheduledTaskAction -Execute $exe -Argument $expectedArgs -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Once -At ([datetime]::Now.AddMinutes(1)) -RepetitionInterval ([timespan]::FromMinutes(15)) -RepetitionDuration ([timespan]::FromDays(365))
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([timespan]::FromMinutes(20)) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
Write-Host 'CI_SCHEDULE_STATUS=installed'
Write-Host ('CI_SCHEDULE_ACCOUNT=' + $identity)
Write-Host 'CI_INTERVAL=15_minutes_while_user_logged_in'
Write-Host 'CI_SCOPE=develop_only'
