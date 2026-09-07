# Windows Task Scheduler deployment

This deployment registers a daily task for the current Windows user. It can run while the user is logged out, wakes supported hardware, starts after a missed trigger, permits the process to run for 18 hours, and refuses to start a duplicate instance.

## Install

Open PowerShell from the repository after creating the virtual environment, `config.yaml`, and `.env`. The installer validates all three before registering anything and prompts for the current Windows account password so Task Scheduler can run with network access while logged out.

```powershell
uv sync --no-dev
PowerShell -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\install.ps1
```

The default trigger is 7:00 AM host-local time. To select another local start time:

```powershell
.\deploy\windows\install.ps1 -StartTime "06:30"
```

Choose a time before the earliest configured T−95 job. This is especially important when the Windows time zone differs from the application `timezone` in `config.yaml`.

## Verify and operate

Use the read-only planner or health command before relying on automatic delivery:

```powershell
.\.venv\Scripts\fantasy-watchdog.exe plan-game-day --config .\config.yaml --env-file .\.env
.\.venv\Scripts\fantasy-watchdog.exe health --config .\config.yaml --env-file .\.env
Get-ScheduledTask -TaskName "Fantasy Watchdog"
Get-ScheduledTaskInfo -TaskName "Fantasy Watchdog"
Start-ScheduledTask -TaskName "Fantasy Watchdog"
```

Task Scheduler's History tab and `Get-ScheduledTaskInfo` show launch and exit status. `run-game-day` also writes one JSON log line per job and source event to stderr, with credentials redacted.

The PC must remain powered on and capable of waking. A task delayed until after some planned checks will run only the remaining future jobs, and a fully shut-down computer cannot be awakened by Task Scheduler.

## Update or remove

After pulling application updates, refresh the environment and rerun the installer if paths or scheduler settings changed:

```powershell
git pull
uv sync --no-dev
.\deploy\windows\install.ps1
```

Remove only the scheduled task; this leaves configuration, secrets, and cache data intact:

```powershell
Unregister-ScheduledTask -TaskName "Fantasy Watchdog" -Confirm:$false
```

Restrict access to `.env` to the account that owns the task. Application secrets are read from that file and are not embedded in the scheduled-task action.
