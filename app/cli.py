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
    OfficialTeamArticle,
    OfficialTeamSource,
    PlayerIdentityResolver,
    SleeperStatusSource,
    assign_next_games,
    build_owned_depth_relations,
    group_kickoff_windows,
    join_owned_skill_players,
    map_rosters_to_nfl,
    parse_nfl_schedule,
    load_latest_depth_snapshot,
    render_depth_snapshot,
    render_inactives_document,
    render_inactives_report,
    render_injury_document,
    render_injury_report,
    render_kickoff_windows,
    render_next_games,
    render_nflverse_status_report,
    render_owned_depth_join,
    render_owned_depth_relations,
    render_roster_mapping,
    render_sleeper_status_report,
    render_team_status_report,
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
    player_status.add_argument(
        "--team-html",
        action="append",
        default=[],
        metavar="TEAM=PATH",
        help="Optional official team-site HTML fixture; may be repeated. Narrative is never binary status",
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
    team_status = subparsers.add_parser(
        "team-status",
        description="Parse optional official team-site articles as attributed notes, never as binary status",
    )
    team_status.add_argument("--home", required=True, help="Home team abbreviation")
    team_status.add_argument("--away", required=True, help="Away team abbreviation")
    team_status.add_argument("--game-id", default="manual")
    team_status.add_argument(
        "--article",
        action="append",
        default=[],
        metavar="TEAM=PATH",
        help="Official team HTML fixture; may be repeated, e.g. GB=tests/fixtures/team_sites/packers_lists.html",
    )
    team_status.add_argument(
        "--url",
        action="append",
        default=[],
        metavar="TEAM=URL",
        help="Optional source URL used for attribution, e.g. GB=https://www.packers.com/news/...",
    )
    team_status.add_argument(
        "--required",
        action="store_true",
        help="Treat missing team articles as failure. Default is optional and non-blocking",
    )
    depth_charts = subparsers.add_parser(
        "depth-charts",
        description="Select the latest nflverse depth snapshot at or before decision time",
    )
    depth_charts.add_argument("--season", type=int, help="NFL season; defaults to YAML config")
    depth_charts.add_argument("--config", type=Path, help="Optional YAML config for season and max age")
    depth_charts.add_argument("--env-file", type=Path, default=Path(".env"))
    depth_charts.add_argument(
        "--as-of",
        help="Timezone-aware ISO decision time; defaults to now in UTC",
    )
    depth_charts.add_argument(
        "--max-age-hours",
        type=int,
        help="Stale-snapshot limit in hours; defaults to YAML depth_chart.max_age_hours",
    )
    depth_charts.add_argument(
        "--charts",
        type=Path,
        help="Offline nflverse depth-chart JSON fixture",
    )
    owned_depth = subparsers.add_parser(
        "owned-depth",
        description="Join owned skill players and list every teammate ahead in the same depth slot",
    )
    owned_depth.add_argument("--config", type=Path, default=Path("config.yaml"))
    owned_depth.add_argument("--env-file", type=Path, default=Path(".env"))
    owned_depth.add_argument(
        "--as-of",
        help="Timezone-aware ISO decision time; defaults to now in UTC",
    )
    owned_depth.add_argument(
        "--max-age-hours",
        type=int,
        help="Stale-snapshot limit in hours; defaults to YAML depth_chart.max_age_hours",
    )
    owned_depth.add_argument(
        "--charts",
        type=Path,
        help="Offline nflverse depth-chart JSON fixture",
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
        if args.command == "team-status":
            return _team_status(args)
        if args.command == "depth-charts":
            return _depth_charts(args)
        if args.command == "owned-depth":
            return _owned_depth(args)
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
    resolver = _identity_resolver(snapshot)
    return map_rosters_to_nfl(rosters, resolver), snapshot


def _identity_resolver(snapshot):
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
    return PlayerIdentityResolver.from_nflverse(
        fantasy_player_ids=required["fantasy player IDs"],
        players=required["players"],
        rosters=required["rosters"],
    )


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


def _depth_charts(args: argparse.Namespace) -> int:
    season, max_age_hours = _depth_chart_settings(args)
    rows = _load_depth_chart_rows(args.charts) if args.charts else None
    snapshot = load_latest_depth_snapshot(
        season,
        as_of=_parse_as_of(args.as_of),
        max_age_hours=max_age_hours,
        rows=rows,
    )
    print(render_depth_snapshot(snapshot))
    return 0


def _owned_depth(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    mapping, core_snapshot = _mapped_rosters(args.config, args.env_file)
    rows = _load_depth_chart_rows(args.charts) if args.charts else None
    max_age_hours = (
        args.max_age_hours
        if args.max_age_hours is not None
        else config.depth_chart.max_age_hours
    )
    if max_age_hours <= 0:
        raise ConfigError("max_age_hours must be greater than 0")
    depth_snapshot = load_latest_depth_snapshot(
        config.season,
        as_of=_parse_as_of(args.as_of),
        max_age_hours=max_age_hours,
        rows=rows,
    )
    resolver = _identity_resolver(core_snapshot)
    players = tuple(player for roster in mapping.rosters for player in roster.players)
    result = join_owned_skill_players(
        players,
        depth_snapshot,
        identities=resolver.identities,
    )
    relations = build_owned_depth_relations(result)
    print(render_depth_snapshot(depth_snapshot))
    print(render_owned_depth_join(result))
    print(render_owned_depth_relations(relations))
    data_errors = {
        "not_in_snapshot",
        "ambiguous_depth_id",
    }
    return 1 if (
        any(issue.state.value in data_errors for issue in result.issues)
        or bool(relations.issues)
    ) else 0


def _depth_chart_settings(args: argparse.Namespace) -> tuple[int, int]:
    config = load_config(args.config) if args.config else None
    season = args.season if args.season is not None else (config.season if config else None)
    if season is None:
        raise ConfigError("Provide --season or --config so the depth-chart season is known")
    max_age = (
        args.max_age_hours
        if args.max_age_hours is not None
        else (config.depth_chart.max_age_hours if config else 30)
    )
    if max_age <= 0:
        raise ConfigError("max_age_hours must be greater than 0")
    return season, max_age


def _parse_as_of(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ConfigError(f"Invalid --as-of timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise ConfigError("--as-of must be a timezone-aware ISO timestamp")
    return parsed.astimezone(timezone.utc)


def _load_depth_chart_rows(path: Path) -> list:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise ConfigError(f"nflverse depth-chart fixture is invalid: {path}")
    return payload


def _team_status(args: argparse.Namespace) -> int:
    articles = _team_articles(args.article, args.url)
    with OfficialTeamSource(articles=articles, required=args.required) as source:
        report = source.fetch_game(
            RelevantGame(
                game_id=args.game_id,
                home_team=args.home,
                away_team=args.away,
                kickoff=datetime.now(timezone.utc),
                fantasy_players=(),
            )
        )
    print(render_team_status_report(report))
    if args.required:
        return 0 if report.report_state is not ReportState.FAILED else 1
    return 0


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
    return _status_exit_code(reports, team_required=False)


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
        return _status_exit_code(reports, team_required=False)

    resolution = cache.resolve_final(
        game_id=args.game_id,
        fetch_reports=lambda: _official_reports(args),
        subjects=_configured_status_subjects(args),
        decision_at=datetime.now(timezone.utc),
    )
    print(render_status_resolution(resolution))
    print(render_player_statuses(resolution.statuses, resolution.reports))
    if resolution.origin_fresh:
        return _status_exit_code(resolution.reports, team_required=False)
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
    team_html = getattr(args, "team_html", None)
    if team_html:
        with OfficialTeamSource(articles=_team_articles(team_html, ())) as team_source:
            reports.append(team_source.fetch_game(game))
    return tuple(reports)


def _team_articles(html_args: list[str], url_args: list[str]) -> dict[str, OfficialTeamArticle]:
    urls = _parse_team_pairs(url_args, "TEAM=URL")
    html_paths = _parse_team_pairs(html_args, "TEAM=PATH")
    teams = set(urls) | set(html_paths)
    articles: dict[str, OfficialTeamArticle] = {}
    for team in sorted(teams):
        path = html_paths.get(team)
        articles[team] = OfficialTeamArticle(
            team=team,
            html=Path(path).read_text(encoding="utf-8") if path else None,
            url=urls.get(team),
        )
    return articles


def _parse_team_pairs(values: list[str], metavar: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ConfigError(f"Use {metavar}, e.g. GB=tests/fixtures/team_sites/packers_lists.html")
        team, payload = value.split("=", 1)
        team = team.strip().upper()
        payload = payload.strip()
        if not team or not payload:
            raise ConfigError(f"Use {metavar}, e.g. GB=tests/fixtures/team_sites/packers_lists.html")
        parsed[team] = payload
    return parsed


def _status_exit_code(reports, *, team_required: bool) -> int:
    for report in reports:
        if report.report_state is not ReportState.FAILED:
            continue
        if report.source == "official_team" and not team_required:
            continue
        return 1
    return 0


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
