"""Render the LaunchAgent template with absolute, XML-safe paths."""

from __future__ import annotations

import argparse
import plistlib
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--start-hour", type=int, choices=range(24), default=7)
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    with args.template.open("rb") as source:
        payload = plistlib.load(source)

    log_dir = Path.home() / "Library" / "Logs" / "FantasyWatchdog"
    payload["ProgramArguments"] = [
        str(project_root / ".venv" / "bin" / "fantasy-watchdog"),
        "run-game-day",
        "--config",
        str(project_root / "config.yaml"),
        "--env-file",
        str(project_root / ".env"),
        "--cache-dir",
        str(project_root / "cache"),
    ]
    payload["WorkingDirectory"] = str(project_root)
    payload["StartCalendarInterval"] = {"Hour": args.start_hour, "Minute": 0}
    payload["StandardOutPath"] = str(log_dir / "stdout.log")
    payload["StandardErrorPath"] = str(log_dir / "stderr.log")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("wb") as destination:
        plistlib.dump(payload, destination, fmt=plistlib.FMT_XML, sort_keys=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
