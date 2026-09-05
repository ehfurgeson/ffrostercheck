"""Command-line entry points for incremental Fantasy Watchdog diagnostics."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.analysis import (
    combine_official_statuses,
    render_player_statuses,
    subjects_from_fantasy_players,
    subjects_from_source_reports,
)
from app.config import ConfigError, load_config, load_environment
from app.fantasy.espn import ESPNAPIError, ESPNClient, render_espn_roster
from app.fantasy.manager import FantasyManager, FantasyManagerError, render_all_rosters
from app.fantasy.sleeper import SleeperAPIError, SleeperClient, render_sleeper_rosters
from app.models import RelevantGame, ReportState
from app.nfl import (
    NFLInactivesSource,
    NFLInjuryReportSource,
    NFLVerseLoadError,
    NFLVerseSource,
    NFLVerseStatusSource,
    PlayerIdentityResolver,
    SleeperStatusSource,
    assign_next_games,
    group_kickoff_windows,
    map_rosters_to_nfl,
    parse_nfl_schedule,
    render_inactives_document,
    render_inactives_report,
    render_injury_document,
    render_injury_report,
    render_kickoff_windows,
    render_next_games,
    render_nflverse_status_report,
    render_roster_mapping,
    render_sleeper_status_report,
)
from app.storage.cache import (
    StatusCache,
    StatusCacheError,
    render_cached_snapshot,
    render_status_resolution,
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
    next_games = subparsers.add_parser(
        "next-games",
        description="Map every resolved fantasy player to their next unstarted NFL game",
    )
    next_games.add_argument("--config", type=Path, default=Path("config.yaml"))
    next_games.add_argument("--env-file", type=Path, default=Path(".env"))
    kickoff_windows = subparsers.add_parser(
        "kickoff-windows",
        description="Print fantasy alert candidates grouped by exact NFL kickoff",
    )
    kickoff_windows.add_argument("--config", type=Path, default=Path("config.yaml"))
    kickoff_windows.add_argument("--env-file", type=Path, default=Path(".env"))
    inactives = subparsers.add_parser(
        "nfl-inactives",
        description="Parse official NFL.com inactives without inferring active from absence",
    )
    inactives.add_argument("--html", type=Path, help="Offline article or landing-page fixture")
    inactives.add_argument("--home", help="Home team abbreviation for a single-game report")
    inactives.add_argument("--away", help="Away team abbreviation for a single-game report")
    inactives.add_argument("--game-id", default="manual")
    injuries = subparsers.add_parser(
        "nfl-injuries",
        description="Parse official NFL.com weekly injury reports without inferring active status",
    )
    injuries.add_argument("--season", type=int, required=True)
    injuries.add_argument("--week", type=int, required=True)
    injuries.add_argument("--html", type=Path, help="Offline injury-report fixture")
    injuries.add_argument("--home", help="Home team abbreviation for a single-game report")
    injuries.add_argument("--away", help="Away team abbreviation for a single-game report")
    injuries.add_argument("--game-id", default="manual")
    player_status = subparsers.add_parser(
        "player-status",
        description="Combine official inactives and injury reports into one status per player",
    )
    player_status.add_argument("--home", required=True, help="Home team abbreviation")
    player_status.add_argument("--away", required=True, help="Away team abbreviation")
    player_status.add_argument("--season", type=int, required=True)
    player_status.add_argument("--week", type=int, required=True)
    player_status.add_argument("--game-id", default="manual")
    player_status.add_argument("--inactives-html", type=Path, help="Offline inactives article fixture")
    player_status.add_argument("--injuries-html", type=Path, help="Offline injury-report fixture")
    player_status.add_argument(
        "--sleeper-players",
        type=Path,
        help="Optional offline Sleeper player-catalog fixture used as a lower-confidence fallback",
    )
    player_status.add_argument(
        "--nflverse-injuries",
        type=Path,
        help="Optional offline nflverse injuries fixture used as a lower-confidence fallback",
    )
    player_status.add_argument("--config", type=Path, help="Optional YAML config to include owned players")
    player_status.add_argument("--env-file", type=Path, default=Path(".env"))
    status_cache = subparsers.add_parser(
        "status-cache",
        description="Save T-90 official statuses or refresh them at T-5 without treating cache as origin-fresh",
    )
    status_cache.add_argument("--home", required=True, help="Home team abbreviation")
    status_cache.add_argument("--away", required=True, help="Away team abbreviation")
    status_cache.add_argument("--season", type=int, required=True)
    status_cache.add_argument("--week", type=int, required=True)
    status_cache.add_argument("--game-id", default="manual")
    status_cache.add_argument("--inactives-html", type=Path, help="Offline inactives article fixture")
    status_cache.add_argument("--injuries-html", type=Path, help="Offline injury-report fixture")
    status_cache.add_argument("--config", type=Path, help="Optional YAML config to include owned players")
    status_cache.add_argument("--env-file", type=Path, default=Path(".env"))
    status_cache.add_argument("--cache-dir", type=Path, default=Path("cache"))
    status_cache.add_argument(
        "--stage",
        choices=("prefetch", "final"),
        required=True,
        help="prefetch stores a T-90 snapshot; final attempts a refresh before using cache",
    )
    sleeper_status = subparsers.add_parser(
        "sleeper-status",
        description="Normalize Sleeper catalog injury/status fields without treating active as game-day active",
    )
    sleeper_status.add_argument("--home", required=True, help="Home team abbreviation")
    sleeper_status.add_argument("--away", required=True, help="Away team abbreviation")
    sleeper_status.add_argument("--game-id", default="manual")
    sleeper_status.add_argument(
        "--players",
        type=Path,
        help="Offline Sleeper player-catalog JSON fixture",
    )
    nflverse_status = subparsers.add_parser(
        "nflverse-status",
        description="Normalize nflverse injuries without treating absence as healthy or game-day active",
    )
    nflverse_status.add_argument("--season", type=int, required=True)
    nflverse_status.add_argument("--week", type=int, required=True)
    nflverse_status.add_argument("--home", required=True, help="Home team abbreviation")
    nflverse_status.add_argument("--away", required=True, help="Away team abbreviation")
    nflverse_status.add_argument("--game-id", default="manual")
    nflverse_status.add_argument(
        "--injuries",
        type=Path,
        help="Offline nflverse injuries JSON fixture",
    )
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
        if args.command == "next-games":
            return _next_games(args.config, args.env_file)
        if args.command == "kickoff-windows":
            return _kickoff_windows(args.config, args.env_file)
        if args.command == "nfl-inactives":
            return _nfl_inactives(args)
        if args.command == "nfl-injuries":
            return _nfl_injuries(args)
        if args.command == "player-status":
            return _player_status(args)
        if args.command == "status-cache":
            return _status_cache(args)
        if args.command == "sleeper-status":
            return _sleeper_status(args)
        if args.command == "nflverse-status":
            return _nflverse_status(args)
    except (
        ConfigError,
        SleeperAPIError,
        ESPNAPIError,
        FantasyManagerError,
        NFLVerseLoadError,
        StatusCacheError,
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
    mapping, _snapshot = _mapped_rosters(config_path, env_file)
    print(render_roster_mapping(mapping))
    return 0 if not mapping.unresolved else 1


def _next_games(config_path: Path, env_file: Path) -> int:
    result = _assigned_next_games(config_path, env_file)
    print(render_next_games(result))
    return 0 if not result.data_errors else 1


def _kickoff_windows(config_path: Path, env_file: Path) -> int:
    result = _assigned_next_games(config_path, env_file)
    print(render_kickoff_windows(group_kickoff_windows(result)))
    return 0 if not result.data_errors else 1


def _assigned_next_games(config_path: Path, env_file: Path):
    mapping, snapshot = _mapped_rosters(config_path, env_file)
    if snapshot.schedules.frame is None:
        raise NFLVerseLoadError(
            "Cannot assign next games; unavailable nflverse data: schedules"
        )
    return assign_next_games(
        mapping.rosters,
        parse_nfl_schedule(snapshot.schedules.frame),
        as_of=datetime.now(timezone.utc),
    )


def _mapped_rosters(config_path: Path, env_file: Path):
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
    return map_rosters_to_nfl(rosters, resolver), snapshot


def _nfl_inactives(args: argparse.Namespace) -> int:
    html = args.html.read_text(encoding="utf-8") if args.html else None
    if bool(args.home) != bool(args.away):
        raise ConfigError("Provide both --home and --away to evaluate a single game")
    source_kwargs = {}
    if html is not None:
        source_kwargs["article_html"] = html
        source_kwargs["article_url"] = str(args.html)
    with NFLInactivesSource(**source_kwargs) as source:
        if args.home and args.away:
            report = source.fetch_game(
                RelevantGame(
                    game_id=args.game_id,
                    home_team=args.home,
                    away_team=args.away,
                    kickoff=datetime.now(timezone.utc),
                    fantasy_players=(),
                )
            )
            print(render_inactives_report(report))
            return 0 if report.report_state is not ReportState.FAILED else 1
        print(render_inactives_document(source.load_document()))
        return 0 if source.load_document().report_state is not ReportState.FAILED else 1


def _nfl_injuries(args: argparse.Namespace) -> int:
    html = args.html.read_text(encoding="utf-8") if args.html else None
    if bool(args.home) != bool(args.away):
        raise ConfigError("Provide both --home and --away to evaluate a single game")
    with NFLInjuryReportSource(
        season=args.season,
        week=args.week,
        html=html,
    ) as source:
        if args.home and args.away:
            report = source.fetch_game(
                RelevantGame(
                    game_id=args.game_id,
                    home_team=args.home,
                    away_team=args.away,
                    kickoff=datetime.now(timezone.utc),
                    fantasy_players=(),
                ),
                validate_date=False,
            )
            print(render_injury_report(report))
            return 0 if report.report_state is not ReportState.FAILED else 1
        print(render_injury_document(source.load_document()))
        return 0 if source.load_document().report_state is not ReportState.FAILED else 1


def _sleeper_status(args: argparse.Namespace) -> int:
    catalog = _load_sleeper_catalog(args.players) if args.players else None
    with SleeperStatusSource(catalog=catalog) as source:
        report = source.fetch_game(
            RelevantGame(
                game_id=args.game_id,
                home_team=args.home,
                away_team=args.away,
                kickoff=datetime.now(timezone.utc),
                fantasy_players=(),
            )
        )
    print(render_sleeper_status_report(report))
    return 0 if report.report_state is not ReportState.FAILED else 1


def _nflverse_status(args: argparse.Namespace) -> int:
    rows = _load_nflverse_injury_rows(args.injuries) if args.injuries else None
    with NFLVerseStatusSource(season=args.season, week=args.week, rows=rows) as source:
        report = source.fetch_game(
            RelevantGame(
                game_id=args.game_id,
                home_team=args.home,
                away_team=args.away,
                kickoff=datetime.now(timezone.utc),
                fantasy_players=(),
            )
        )
    print(render_nflverse_status_report(report))
    return 0 if report.report_state is not ReportState.FAILED else 1


def _player_status(args: argparse.Namespace) -> int:
    reports, subjects = _official_status_inputs(args)
    print(
        render_player_statuses(
            combine_official_statuses(
                subjects,
                reports,
                decision_at=datetime.now(timezone.utc),
            ),
            reports,
        )
    )
    return 0 if all(report.report_state is not ReportState.FAILED for report in reports) else 1


def _status_cache(args: argparse.Namespace) -> int:
    cache = StatusCache(args.cache_dir)
    if args.stage == "prefetch":
        reports, subjects = _official_status_inputs(args)
        decision_at = datetime.now(timezone.utc)
        statuses = combine_official_statuses(subjects, reports, decision_at=decision_at)
        snapshot = cache.save_prefetch(
            game_id=args.game_id,
            reports=reports,
            statuses=statuses,
            cached_at=decision_at,
        )
        print(render_cached_snapshot(snapshot))
        print(render_player_statuses(snapshot.statuses, snapshot.reports))
        return 0 if all(report.report_state is not ReportState.FAILED for report in reports) else 1

    resolution = cache.resolve_final(
        game_id=args.game_id,
        fetch_reports=lambda: _official_reports(args),
        subjects=_configured_status_subjects(args),
        decision_at=datetime.now(timezone.utc),
    )
    print(render_status_resolution(resolution))
    print(render_player_statuses(resolution.statuses, resolution.reports))
    if resolution.origin_fresh:
        return 0 if all(report.report_state is not ReportState.FAILED for report in resolution.reports) else 1
    return 0


def _official_status_inputs(args: argparse.Namespace):
    reports = _official_reports(args)
    return reports, _status_subjects(args, reports)


def _official_reports(args: argparse.Namespace):
    game = RelevantGame(
        game_id=args.game_id,
        home_team=args.home,
        away_team=args.away,
        kickoff=datetime.now(timezone.utc),
        fantasy_players=(),
    )
    inactives_html = args.inactives_html.read_text(encoding="utf-8") if args.inactives_html else None
    injuries_html = args.injuries_html.read_text(encoding="utf-8") if args.injuries_html else None
    inactives_kwargs: dict[str, str] = {}
    if inactives_html is not None:
        inactives_kwargs["article_html"] = inactives_html
        inactives_kwargs["article_url"] = str(args.inactives_html)
    with (
        NFLInactivesSource(**inactives_kwargs) as inactives,
        NFLInjuryReportSource(
            season=args.season,
            week=args.week,
            html=injuries_html,
        ) as injuries,
    ):
        reports = [
            inactives.fetch_game(game),
            injuries.fetch_game(game, validate_date=False),
        ]
    sleeper_players = getattr(args, "sleeper_players", None)
    if sleeper_players is not None:
        with SleeperStatusSource(catalog=_load_sleeper_catalog(sleeper_players)) as sleeper:
            reports.append(sleeper.fetch_game(game))
    nflverse_injuries = getattr(args, "nflverse_injuries", None)
    if nflverse_injuries is not None:
        with NFLVerseStatusSource(
            season=args.season,
            week=args.week,
            rows=_load_nflverse_injury_rows(nflverse_injuries),
        ) as nflverse:
            reports.append(nflverse.fetch_game(game))
    return tuple(reports)


def _load_sleeper_catalog(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not all(isinstance(value, dict) for value in payload.values()):
        raise ConfigError(f"Sleeper player catalog fixture is invalid: {path}")
    return payload


def _load_nflverse_injury_rows(path: Path) -> list:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise ConfigError(f"nflverse injuries fixture is invalid: {path}")
    return payload


def _configured_status_subjects(args: argparse.Namespace):
    if not args.config:
        return ()
    return _status_subjects(args, ())


def _status_subjects(args: argparse.Namespace, reports):
    if args.config:
        mapping, _snapshot = _mapped_rosters(args.config, args.env_file)
        teams = {args.home.upper(), args.away.upper()}
        owned = tuple(
            player
            for roster in mapping.rosters
            for player in roster.players
            if player.nfl_team in teams
        )
        return subjects_from_fantasy_players(owned)
    return subjects_from_source_reports(reports)
