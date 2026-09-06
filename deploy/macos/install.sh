#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
start_hour=${FANTASY_WATCHDOG_START_HOUR:-7}

case "$start_hour" in
    ''|*[!0-9]*)
        echo "FANTASY_WATCHDOG_START_HOUR must be an integer from 0 through 23" >&2
        exit 2
        ;;
esac
if [ "$start_hour" -lt 0 ] || [ "$start_hour" -gt 23 ]; then
    echo "FANTASY_WATCHDOG_START_HOUR must be an integer from 0 through 23" >&2
    exit 2
fi

python="$project_root/.venv/bin/python"
executable="$project_root/.venv/bin/fantasy-watchdog"
config="$project_root/config.yaml"
env_file="$project_root/.env"
for required in "$python" "$executable" "$config" "$env_file"; do
    if [ ! -f "$required" ]; then
        echo "Required file not found: $required" >&2
        exit 2
    fi
done

agent_dir="$HOME/Library/LaunchAgents"
log_dir="$HOME/Library/Logs/FantasyWatchdog"
plist="$agent_dir/com.fantasy-watchdog.game-day.plist"
domain="gui/$(id -u)"
mkdir -p "$agent_dir" "$log_dir" "$project_root/cache"

"$python" "$script_dir/render_plist.py" \
    --template "$script_dir/com.fantasy-watchdog.game-day.plist" \
    --output "$plist" \
    --project-root "$project_root" \
    --start-hour "$start_hour"
plutil -lint "$plist"
launchctl bootout "$domain" "$plist" 2>/dev/null || true
launchctl bootstrap "$domain" "$plist"
launchctl enable "$domain/com.fantasy-watchdog.game-day"

echo "Installed com.fantasy-watchdog.game-day at ${start_hour}:00 host-local time."
echo "Verify with: launchctl print $domain/com.fantasy-watchdog.game-day"
