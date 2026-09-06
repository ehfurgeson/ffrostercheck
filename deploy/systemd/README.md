# systemd deployment

This deployment runs one supervised process at 7:00 AM Eastern every day. The process discovers that day's relevant kickoff windows, waits for each configured prefetch/final instant, exits after the last job, and does no work when there are no relevant games.

## Install

The checked-in unit assumes the repository is installed at `/opt/fantasy-watchdog`, runtime configuration is under `/etc/fantasy-watchdog`, and the unprivileged service account is named `fantasy-watchdog`.

```bash
sudo useradd --system --home /var/lib/fantasy-watchdog --create-home fantasy-watchdog
sudo install -d -o fantasy-watchdog -g fantasy-watchdog /etc/fantasy-watchdog /var/lib/fantasy-watchdog/cache
sudo git clone https://github.com/ehfurgeson/ffrostercheck.git /opt/fantasy-watchdog
sudo chown -R fantasy-watchdog:fantasy-watchdog /opt/fantasy-watchdog
cd /opt/fantasy-watchdog
sudo -u fantasy-watchdog uv sync --no-dev
sudo install -m 600 -o fantasy-watchdog -g fantasy-watchdog config.yaml .env /etc/fantasy-watchdog/
sudo install -m 644 deploy/systemd/fantasy-watchdog.service deploy/systemd/fantasy-watchdog.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fantasy-watchdog.timer
```

If the host uses another time zone, keep the explicit `America/New_York` timer zone or intentionally change it together with the application configuration. Adjust the repository path or service user in the unit before installation if your host layout differs.

## Verify and operate

Run the read-only planner before enabling delivery, then inspect the timer and logs:

```bash
sudo -u fantasy-watchdog /opt/fantasy-watchdog/.venv/bin/fantasy-watchdog plan-game-day --config /etc/fantasy-watchdog/config.yaml --env-file /etc/fantasy-watchdog/environment
systemctl list-timers fantasy-watchdog.timer
journalctl -u fantasy-watchdog.service
```

The environment file must contain the same `NAME=value` format as `.env`; systemd and `python-dotenv` can both read it. A failed or excessively late job is isolated so later windows still run, and the service returns a nonzero status after the final job if any invocation was unsuccessful.
