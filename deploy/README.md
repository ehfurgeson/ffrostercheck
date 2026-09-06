# Deployment options

Fantasy Watchdog uses the same `run-game-day` command on every platform. The native scheduler starts it once each morning; the process plans that day's relevant NFL windows, waits for each job, and exits after the final window.

| Platform | Scheduler | Best fit | Guide |
|---|---|---|---|
| Linux | systemd service and timer | Always-on server, VPS, or Raspberry Pi | [systemd](systemd/README.md) |
| Windows | Task Scheduler | Personal or always-on Windows PC | [Windows](windows/README.md) |
| macOS | Per-user LaunchAgent | Mac whose selected user stays logged in | [macOS](macos/README.md) |

The default start time is 7:00 AM in the host's local time zone. If the host and the `timezone` in `config.yaml` differ, choose a host-local start time that occurs before the earliest configured prefetch job.

The computer must be powered on and either awake or supported by the host scheduler's wake behavior. Docker is intentionally not included yet: it can standardize a future server deployment, but it would still need host scheduling, mounted secrets and cache storage, networking, and an awake host.
