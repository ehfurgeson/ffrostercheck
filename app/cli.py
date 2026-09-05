"""Command-line entry points for incremental Fantasy Watchdog diagnostics."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.config import ConfigError, load_config, load_environment
from app.fantasy.espn import ESPNAPIError, ESPNClient, render_espn_roster
from app.fantasy.manager import FantasyManager, FantasyManagerError, render_all_rosters
from app.fantasy.sleeper import SleeperAPIError, SleeperClient, render_sleeper_rosters
from app.nfl import (
    NFLVerseLoadError,
    NFLVerseSource,
    PlayerIdentityResolver,
    map_rosters_to_nfl,
    render_roster_mapping,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fantasy-watchdog")
    subparsers = parser.add_subparsers(dest="command", required=True)
    sleeper = subparsers.add_parser(
        "sleeper-rosters", description="Print interpreted Sleeper starters and bench players"
    )
    sleeper.add_argument("--config", type=Path, default=Path("config.yaml"))
    sleeper.add_argument("--env-file", type=Path, default=Path(".env"))
    espn = subparsers.add_parser(
        "espn-roster", description="Print the interpreted ESPN starters and bench players"
    )
    espn.add_argument("--config", type=Path, default=Path("config.yaml"))
    espn.add_argument("--env-file", type=Path, default=Path(".env"))
    all_rosters = subparsers.add_parser(
        "all-rosters", description="Print normalized starters and bench players from all platforms"
    )
    all_rosters.add_argument("--config", type=Path, default=Path("config.yaml"))
    all_rosters.add_argument("--env-file", type=Path, default=Path(".env"))
    resolved_rosters = subparsers.add_parser(
        "resolved-rosters",
        description="Validate canonical IDs and current NFL teams for every fantasy player",
    )
    resolved_rosters.add_argument("--config", type=Path, default=Path("config.yaml"))
    resolved_rosters.add_argument("--env-file", type=Path, default=Path(".env"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "sleeper-rosters":
            return _sleeper_rosters(args.config, args.env_file)
        if args.command == "espn-roster":
            return _espn_roster(args.config, args.env_file)
        if args.command == "all-rosters":
            return _all_rosters(args.config, args.env_file)
        if args.command == "resolved-rosters":
            return _resolved_rosters(args.config, args.env_file)
    except (
        ConfigError,
        SleeperAPIError,
        ESPNAPIError,
        FantasyManagerError,
        NFLVerseLoadError,
    ) as exc:
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


def _espn_roster(config_path: Path, env_file: Path) -> int:
    config = load_config(config_path)
    environment = load_environment(env_file=env_file)
    configured = config.espn.leagues[0] if config.espn.leagues else None
    league_id = environment.espn_league_id or (configured.id if configured else None)
    if not league_id or league_id.startswith("REPLACE_WITH_"):
        raise ConfigError("Set ESPN_LEAGUE_ID or configure an ESPN league in YAML")

    team_id = configured.team_id if configured else None
    if team_id and team_id.startswith("REPLACE_WITH_"):
        team_id = None
    nickname = configured.nickname if configured else "ESPN"
    with ESPNClient(swid=environment.espn_swid, espn_s2=environment.espn_s2) as client:
        roster = client.load_roster(
            season=config.season,
            league_id=league_id,
            team_id=team_id,
            nickname=nickname,
        )
    print(render_espn_roster(roster))
    return 0


def _all_rosters(config_path: Path, env_file: Path) -> int:
    config = load_config(config_path)
    environment = load_environment(env_file=env_file)
    with (
        SleeperClient() as sleeper,
        ESPNClient(swid=environment.espn_swid, espn_s2=environment.espn_s2) as espn,
    ):
        manager = FantasyManager(sleeper=sleeper, espn=espn)
        rosters = manager.get_all_rosters(config, environment)
    print(render_all_rosters(rosters))
    return 0


def _resolved_rosters(config_path: Path, env_file: Path) -> int:
    config = load_config(config_path)
    environment = load_environment(env_file=env_file)
    with (
        SleeperClient() as sleeper,
        ESPNClient(swid=environment.espn_swid, espn_s2=environment.espn_s2) as espn,
    ):
        rosters = FantasyManager(sleeper=sleeper, espn=espn).get_all_rosters(
            config, environment
        )
    snapshot = NFLVerseSource().load_snapshot(config.season)
    required = {
        "players": snapshot.players.frame,
        "rosters": snapshot.rosters.frame,
        "fantasy player IDs": snapshot.fantasy_player_ids.frame,
    }
    missing = [name for name, frame in required.items() if frame is None]
    if missing:
        raise NFLVerseLoadError(
            "Cannot resolve fantasy rosters; unavailable nflverse data: " + ", ".join(missing)
        )
    resolver = PlayerIdentityResolver.from_nflverse(
        fantasy_player_ids=required["fantasy player IDs"],
        players=required["players"],
        rosters=required["rosters"],
    )
    result = map_rosters_to_nfl(rosters, resolver)
    print(render_roster_mapping(result))
    return 0 if not result.unresolved else 1
