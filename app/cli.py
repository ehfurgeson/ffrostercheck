"""Command-line entry points for incremental Fantasy Watchdog diagnostics."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.config import ConfigError, load_config, load_environment
from app.fantasy.sleeper import SleeperAPIError, SleeperClient, render_sleeper_rosters


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fantasy-watchdog")
    subparsers = parser.add_subparsers(dest="command", required=True)
    sleeper = subparsers.add_parser(
        "sleeper-rosters", description="Print interpreted Sleeper starters and bench players"
    )
    sleeper.add_argument("--config", type=Path, default=Path("config.yaml"))
    sleeper.add_argument("--env-file", type=Path, default=Path(".env"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "sleeper-rosters":
            return _sleeper_rosters(args.config, args.env_file)
    except (ConfigError, SleeperAPIError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


def _sleeper_rosters(config_path: Path, env_file: Path) -> int:
    config = load_config(config_path)
    environment = load_environment(env_file=env_file)
    username = environment.sleeper_user or config.sleeper.username
    if not username:
        raise ConfigError("Set SLEEPER_USER or sleeper.username in the YAML configuration")

    with SleeperClient() as client:
        rosters = client.load_rosters(
            username=username,
            season=config.season,
            configured_leagues=config.sleeper.leagues,
        )
    print(render_sleeper_rosters(rosters))
    return 0
