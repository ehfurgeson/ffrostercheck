"""Operational health checks for Fantasy Watchdog.

Live probes confirm current connectivity and configuration. Cached official
snapshots and planned jobs are inspected without treating empty non-game-day
state as failure. Missing or failed official HTML is never treated as healthy
player status; scraper reachability still reports OK when the page parses as
``NOT_YET_PUBLISHED``.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import AppConfig, ConfigError, EnvironmentConfig, load_config, load_environment
from app.fantasy.espn import ESPNAPIError, ESPNClient
from app.fantasy.sleeper import SleeperAPIError, SleeperClient
from app.models import ReportState
from app.nfl.depth_chart import DepthChartSnapshot, DepthSnapshotState, load_latest_depth_snapshot
from app.nfl.depth_join import DepthJoinIssueState, OwnedDepthJoinResult, join_owned_skill_players
from app.nfl.nflverse import NFLVerseSource
from app.nfl.sources.nfl_inactives import NFLInactivesSource
from app.nfl.sources.nfl_injuries import NFLInjuryReportSource
from app.notification.email import EmailDeliveryError, SMTPEmailNotifier
from app.scheduling.planner import build_game_day_plan
from app.scheduling.production import ProductionServiceError, load_operational_snapshot
from app.storage.cache import StatusCache


UNRESOLVED_DEPTH_JOIN_STATES = frozenset(
    {
        DepthJoinIssueState.UNRESOLVED_IDENTITY,
        DepthJoinIssueState.NOT_IN_SNAPSHOT,
        DepthJoinIssueState.AMBIGUOUS_DEPTH_ID,
    }
)


@dataclass(frozen=True)
class HealthCheck:
    """One named operational probe."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class HealthReport:
    """All probes from one health run."""

    checked_at: datetime
    checks: tuple[HealthCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


def render_health_report(report: HealthReport) -> str:
    """Render the compact operator-facing health summary."""

    lines = [f"Health check: {report.checked_at.isoformat().replace('+00:00', 'Z')}"]
    for check in report.checks:
        status = "OK" if check.ok else "FAIL"
        lines.append(f"{check.name}: {status} — {check.detail}")
    lines.append(f"Overall: {'OK' if report.ok else 'FAIL'}")
    return "\n".join(lines)


def collect_health_report(
    config: AppConfig,
    environment: EnvironmentConfig,
    *,
    cache_dir: Path = Path("cache"),
    checked_at: datetime | None = None,
    sleeper_client: SleeperClient | None = None,
    espn_client: ESPNClient | None = None,
    inactives_source: NFLInactivesSource | None = None,
    injury_source: NFLInjuryReportSource | None = None,
    nflverse: NFLVerseSource | None = None,
    smtp_notifier: SMTPEmailNotifier | None = None,
    operational_loader=load_operational_snapshot,
    depth_loader=load_latest_depth_snapshot,
    depth_joiner=join_owned_skill_players,
) -> HealthReport:
    """Run connectivity, depth, cache, SMTP, and next-job probes."""

    now = (checked_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    checks: list[HealthCheck] = []

    sleeper_check = _check_sleeper(config, environment, client=sleeper_client)
    checks.append(sleeper_check)

    espn_check = _check_espn(config, environment, client=espn_client)
    checks.append(espn_check)

    checks.append(_check_nfl_inactives(source=inactives_source))

    operational: object | None = None
    operational_error: str | None = None
    if sleeper_check.ok and espn_check.ok:
        try:
            operational = operational_loader(config, environment, decision_at=now)
        except (ConfigError, ProductionServiceError, SleeperAPIError, ESPNAPIError) as exc:
            operational_error = f"{type(exc).__name__}: {exc}"

    injury_week = _injury_week(config, operational)
    checks.append(
        _check_nfl_injuries(
            season=injury_week[0],
            week=injury_week[1],
            source=injury_source,
        )
    )

    depth_check, _depth_snapshot, depth_join = _check_depth(
        config,
        operational,
        operational_error=operational_error,
        checked_at=now,
        nflverse=nflverse,
        depth_loader=depth_loader,
        depth_joiner=depth_joiner,
    )
    checks.append(depth_check)
    checks.append(_check_unresolved_depth_joins(depth_join, operational_error=operational_error))
    checks.append(_check_status_cache(StatusCache(cache_dir), operational))
    checks.append(_check_smtp(config, environment, notifier=smtp_notifier))
    checks.append(
        _check_next_job(
            config,
            operational,
            checked_at=now,
            operational_error=operational_error,
        )
    )
    return HealthReport(checked_at=now, checks=tuple(checks))


def _check_sleeper(
    config: AppConfig,
    environment: EnvironmentConfig,
    *,
    client: SleeperClient | None,
) -> HealthCheck:
    username = environment.sleeper_user or config.sleeper.username
    if not username:
        return HealthCheck("Sleeper", False, "SLEEPER_USER or sleeper.username is missing")
    owns = client is None
    sleeper = client or SleeperClient()
    try:
        user = sleeper.resolve_user(username)
        leagues = sleeper.get_leagues(str(user["user_id"]), config.season)
        return HealthCheck(
            "Sleeper",
            True,
            f"user {user['user_id']} — {len(leagues)} NFL league(s) for {config.season}",
        )
    except (SleeperAPIError, OSError) as exc:
        return HealthCheck("Sleeper", False, f"{type(exc).__name__}: {exc}")
    finally:
        if owns:
            sleeper.close()


def _check_espn(
    config: AppConfig,
    environment: EnvironmentConfig,
    *,
    client: ESPNClient | None,
) -> HealthCheck:
    configured = config.espn.leagues[0] if config.espn.leagues else None
    league_id = environment.espn_league_id or (configured.id if configured else None)
    if not league_id or str(league_id).startswith("REPLACE_WITH_"):
        return HealthCheck("ESPN", False, "ESPN_LEAGUE_ID or espn.leagues[].id is missing")
    if not environment.espn_swid or not environment.espn_s2:
        return HealthCheck("ESPN", False, "ESPN_SWID / ESPN_S2 are missing")
    owns = client is None
    espn = client or ESPNClient(swid=environment.espn_swid, espn_s2=environment.espn_s2)
    try:
        payload = espn.fetch_league(season=config.season, league_id=str(league_id))
        teams = payload.get("teams") if isinstance(payload, dict) else None
        team_count = len(teams) if isinstance(teams, list) else 0
        return HealthCheck("ESPN", True, f"league {league_id} — {team_count} team(s)")
    except (ESPNAPIError, OSError) as exc:
        return HealthCheck("ESPN", False, f"{type(exc).__name__}: {exc}")
    finally:
        if owns:
            espn.close()


def _check_nfl_inactives(*, source: NFLInactivesSource | None) -> HealthCheck:
    owns = source is None
    inactives = source or NFLInactivesSource()
    try:
        document = inactives.load_document()
        if document.report_state is ReportState.FAILED:
            detail = "; ".join(document.errors) or "failed to load NFL.com inactives"
            return HealthCheck("NFL inactives scraper", False, detail)
        teams = len(document.parsed_teams)
        return HealthCheck(
            "NFL inactives scraper",
            True,
            f"{document.report_state.value} — {teams} team list(s)",
        )
    except OSError as exc:
        return HealthCheck("NFL inactives scraper", False, f"{type(exc).__name__}: {exc}")
    finally:
        if owns:
            inactives.close()


def _check_nfl_injuries(
    *,
    season: int,
    week: int,
    source: NFLInjuryReportSource | None,
) -> HealthCheck:
    owns = source is None
    injuries = source or NFLInjuryReportSource(season=season, week=week)
    try:
        document = injuries.load_document()
        if document.report_state is ReportState.FAILED:
            detail = "; ".join(document.errors) or "failed to load NFL.com injury report"
            return HealthCheck("NFL injury scraper", False, detail)
        return HealthCheck(
            "NFL injury scraper",
            True,
            f"{season} WEEK {week} — {document.report_state.value}",
        )
    except OSError as exc:
        return HealthCheck("NFL injury scraper", False, f"{type(exc).__name__}: {exc}")
    finally:
        if owns:
            injuries.close()


def _check_depth(
    config: AppConfig,
    operational,
    *,
    operational_error: str | None,
    checked_at: datetime,
    nflverse: NFLVerseSource | None,
    depth_loader,
    depth_joiner,
) -> tuple[HealthCheck, DepthChartSnapshot | None, OwnedDepthJoinResult | None]:
    if not config.depth_chart.enabled:
        return HealthCheck("Depth chart", True, "disabled"), None, None
    if operational is None:
        return (
            HealthCheck(
                "Depth chart",
                False,
                operational_error or "operational snapshot unavailable",
            ),
            None,
            None,
        )
    try:
        snapshot = depth_loader(
            config.season,
            as_of=checked_at,
            max_age_hours=config.depth_chart.max_age_hours,
            nflverse=nflverse,
        )
    except Exception as exc:  # schema failures must surface as health failures
        return (
            HealthCheck("Depth chart", False, f"{type(exc).__name__}: {exc}"),
            None,
            None,
        )

    if snapshot.state in {DepthSnapshotState.MISSING, DepthSnapshotState.UNSUPPORTED_SEASON}:
        return (
            HealthCheck(
                "Depth chart",
                False,
                snapshot.detail or snapshot.state.value,
            ),
            snapshot,
            None,
        )

    players = tuple(
        player for roster in operational.lineup.rosters for player in roster.players
    )
    joined = depth_joiner(players, snapshot)
    age = (
        f"{snapshot.age_hours:.1f}h old"
        if snapshot.age_hours is not None
        else "age unknown"
    )
    stamp = (
        snapshot.snapshot_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if snapshot.snapshot_at is not None
        else "unknown"
    )
    label = "STALE" if snapshot.state is DepthSnapshotState.STALE else "OK"
    detail = (
        f"{label} — {len(snapshot.teams)} teams — snapshot {stamp} ({age})"
    )
    # Stale data remains usable with an explicit limitation, matching analysis rules.
    return HealthCheck("Depth chart", True, detail), snapshot, joined


def _check_unresolved_depth_joins(
    joined: OwnedDepthJoinResult | None,
    *,
    operational_error: str | None,
) -> HealthCheck:
    if joined is None:
        if operational_error:
            return HealthCheck(
                "Unresolved owned depth joins",
                False,
                operational_error,
            )
        return HealthCheck("Unresolved owned depth joins", True, "0 (depth disabled or unused)")
    unresolved = sum(
        1 for issue in joined.issues if issue.state in UNRESOLVED_DEPTH_JOIN_STATES
    )
    return HealthCheck(
        "Unresolved owned depth joins",
        unresolved == 0,
        str(unresolved),
    )


def _check_status_cache(cache: StatusCache, operational) -> HealthCheck:
    if not cache.status_dir.is_dir():
        return HealthCheck("Status cache", True, "empty")

    game_ids: list[str] = []
    if operational is not None:
        game_ids = sorted(
            {game.game_id for game in operational.kickoff_plan.relevant_games}
        )
    if not game_ids:
        game_ids = sorted(
            path.name
            for path in cache.status_dir.iterdir()
            if path.is_dir()
        )

    complete = 0
    ages: list[int] = []
    now = datetime.now(timezone.utc)
    for game_id in game_ids:
        snapshot = cache.load_latest(game_id)
        if snapshot is None:
            continue
        ages.append(int((now - snapshot.cached_at).total_seconds()))
        if any(report.report_state is ReportState.COMPLETE for report in snapshot.reports):
            complete += 1

    if not ages:
        return HealthCheck("Status cache", True, "no snapshots for relevant games")
    newest_age = min(ages)
    return HealthCheck(
        "Status cache",
        True,
        f"{complete} complete snapshot(s); newest age {newest_age}s",
    )


def _check_smtp(
    config: AppConfig,
    environment: EnvironmentConfig,
    *,
    notifier: SMTPEmailNotifier | None,
) -> HealthCheck:
    try:
        smtp = notifier or SMTPEmailNotifier(config.email, environment)
        smtp.verify_connection()
    except (ConfigError, EmailDeliveryError, ValueError) as exc:
        return HealthCheck("SMTP", False, f"{type(exc).__name__}: {exc}")
    return HealthCheck(
        "SMTP",
        True,
        f"{config.email.smtp_host}:{config.email.smtp_port} authenticated",
    )


def _check_next_job(
    config: AppConfig,
    operational,
    *,
    checked_at: datetime,
    operational_error: str | None,
) -> HealthCheck:
    if operational is None:
        return HealthCheck(
            "Next job",
            False,
            operational_error or "operational snapshot unavailable",
        )
    plan = build_game_day_plan(
        operational.kickoff_plan,
        config.alerts,
        planned_at=checked_at,
        display_timezone=config.timezone_info,
    )
    if not plan.jobs:
        return HealthCheck("Next job", True, f"none remaining on {plan.game_date.isoformat()}")
    job = plan.jobs[0]
    local = job.run_at.astimezone(config.timezone_info)
    return HealthCheck(
        "Next job",
        True,
        f"{job.job_id} at {_format_local(local, config.timezone_info)}",
    )


def _injury_week(config: AppConfig, operational) -> tuple[int, int]:
    if operational is None:
        return config.season, 1
    weeks = {
        operational.game_weeks.get(game.game_id)
        for window in operational.kickoff_plan.windows
        for game in window.games
    }
    weeks.discard(None)
    if len(weeks) == 1:
        return next(iter(weeks))  # type: ignore[return-value]
    if weeks:
        return min(weeks)  # type: ignore[type-var]
    return config.season, 1


def _format_local(moment: datetime, display_timezone: ZoneInfo) -> str:
    local = moment.astimezone(display_timezone)
    return local.strftime("%a %I:%M %p %Z").lstrip("0").replace(" 0", " ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.health")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--cache-dir", type=Path, default=Path("cache"))
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        environment = load_environment(env_file=args.env_file)
        report = collect_health_report(
            config,
            environment,
            cache_dir=args.cache_dir,
        )
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(render_health_report(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
