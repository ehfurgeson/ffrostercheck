[CmdletBinding()]
param(
    [string]$ProjectRoot,
    [string]$TaskName = "Fantasy Watchdog",
    [ValidatePattern("^(?:[01]\d|2[0-3]):[0-5]\d$")]
    [string]$StartTime = "07:00"
)

$ErrorActionPreference = "Stop"

# $PSScriptRoot is unreliable in param() defaults; resolve after binding.
if (-not $ProjectRoot) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$Executable = Join-Path $ProjectRoot ".venv\Scripts\fantasy-watchdog.exe"
$ConfigPath = Join-Path $ProjectRoot "config.yaml"
$EnvPath = Join-Path $ProjectRoot ".env"
$CachePath = Join-Path $ProjectRoot "cache"

foreach ($RequiredPath in @($Executable, $ConfigPath, $EnvPath)) {
    if (-not (Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
        throw "Required file not found: $RequiredPath"
    }
}
New-Item -ItemType Directory -Path $CachePath -Force | Out-Null

$Arguments = 'run-game-day --config "{0}" --env-file "{1}" --cache-dir "{2}"' -f `
    $ConfigPath, $EnvPath, $CachePath
$Action = New-ScheduledTaskAction `
    -Execute $Executable `
    -Argument $Arguments `
    -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At $StartTime
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -ExecutionTimeLimit (New-TimeSpan -Hours 18) `
    -MultipleInstances IgnoreNew

$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Credential = Get-Credential `
    -UserName $CurrentUser `
    -Message "Enter the Windows password used to run Fantasy Watchdog while logged out."
$Principal = New-ScheduledTaskPrincipal `
    -UserId $Credential.UserName `
    -LogonType Password `
    -RunLevel Limited
$Task = New-ScheduledTask `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Run Fantasy Watchdog's supervised NFL game-day alert process."

$PlainPassword = $Credential.GetNetworkCredential().Password
try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -InputObject $Task `
        -User $Credential.UserName `
        -Password $PlainPassword `
        -Force | Out-Null
}
finally {
    $PlainPassword = $null
    $Credential = $null
}

Write-Host "Installed scheduled task '$TaskName' at $StartTime host-local time."
Write-Host "Verify with: Get-ScheduledTask -TaskName '$TaskName'"
