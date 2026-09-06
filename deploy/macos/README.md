# macOS LaunchAgent deployment

This deployment installs a LaunchAgent for the current user. It starts at login and daily at the configured host-local hour, then relies on `run-game-day` to wait for the exact NFL prefetch and final-alert times.

## Install

Create the virtual environment, `config.yaml`, and `.env`, then run:

```bash
uv sync --no-dev
chmod 600 .env
./deploy/macos/install.sh
```

The installer derives absolute paths from the repository, safely renders and validates the plist, creates the cache and log directories, and loads the agent. To choose another host-local start hour, use an integer from 0 through 23:

```bash
FANTASY_WATCHDOG_START_HOUR=6 ./deploy/macos/install.sh
```

Choose an hour before the earliest configured T−95 job. This is especially important when the Mac's time zone differs from the application `timezone` in `config.yaml`.

## Verify and operate

Use the read-only planner before relying on automatic delivery:

```bash
.venv/bin/fantasy-watchdog plan-game-day --config config.yaml --env-file .env
launchctl print "gui/$(id -u)/com.fantasy-watchdog.game-day"
tail -f "$HOME/Library/Logs/FantasyWatchdog/stdout.log"
tail -f "$HOME/Library/Logs/FantasyWatchdog/stderr.log"
```

The selected user must remain logged in for a LaunchAgent to run, and the Mac should remain awake through the scheduled checks. `launchd` may coalesce a missed calendar event after the Mac wakes, but it should not be relied on to wake a sleeping Mac and cannot run while the Mac is shut down. `RunAtLoad` lets a login or installation later in the day plan any remaining future jobs, and launchd does not start a second copy while the first is still running.

## Update or remove

After pulling application updates, refresh the environment and rerun the installer if paths or scheduler settings changed:

```bash
git pull
uv sync --no-dev
./deploy/macos/install.sh
```

Unload and remove only the LaunchAgent; this leaves configuration, secrets, logs, and cache data intact:

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.fantasy-watchdog.game-day.plist"
rm "$HOME/Library/LaunchAgents/com.fantasy-watchdog.game-day.plist"
```

Application secrets remain in the repository's ignored `.env` file and are not embedded in the installed plist.
