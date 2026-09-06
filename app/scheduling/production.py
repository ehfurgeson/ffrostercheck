"""Production composition for the supervised game-day runner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from app.analysis import build_status_scope, subjects_from_fantasy_players
from app.config import AppConfig, EnvironmentConfig, load_config, load_environment
from app.fantasy import ESPNClient, FantasyManager, SleeperClient
from app.models import LineupRefreshEvidence, RelevantGame
from app.nfl import (
    KickoffPlan,
    NFLVerseSource,
    NFLVerseStatusSource,
    PlayerIdentityResolver,
    SleeperStatusSource,
    assign_next_games,
    build_owned_depth_relations,
    group_kickoff_windows,
    join_owned_skill_players,
    load_latest_depth_snapshot,
    map_rosters_to_nfl,
    parse_nfl_schedule,
)
from app.notification import SMTPEmailNotifier
from app.scheduling.final import FinalLineupSnapshot, execute_official_final_job
from app.scheduling.planner import PlannedJob, build_game_day_plan
from app.scheduling.prefetch import execute_official_prefetch_job
from app.scheduling.service import GameDayServiceResult, run_game_day_service
from app.storage import StatusCache


class ProductionServiceError(RuntimeError):
    """Raised when live inputs cannot form a safe game-day execution plan."""


@dataclass(frozen=True)
class OperationalSnapshot:
    """Resolved live state shared by planning or one final lineup refresh."""

    lineup: FinalLineupSnapshot
    kickoff_plan: KickoffPlan
    game_weeks: Mapping[str, tuple[int, int]]


def run_production_game_day(
    *,
    config_path: Path,
    env_file: Path,
    cache_dir: Path,
    started_at: datetime | None = None,
) -> GameDayServiceResult:
    """Plan today's relevant windows, wait, and execute them under systemd."""

    config = load_config(config_path)
    environment = load_environment(env_file=env_file)
    start = (started_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    initial = _load_operational_snapshot(config, environment, decision_at=start)
    plan = build_game_day_plan(
        initial.kickoff_plan,
        config.alerts,
        planned_at=start,
        display_timezone=config.timezone_info,
    )
    cache = StatusCache(cache_dir)

    def week_for(job: PlannedJob) -> tuple[int, int]:
        values = {initial.game_weeks.get(game.game_id) for game in job.games}
        values.discard(None)
        if len(values) != 1:
            raise ProductionServiceError(
                f"Job {job.job_id} does not map to exactly one NFL season/week"
            )
        return next(iter(values))  # type: ignore[return-value]

    def prefetch(job: PlannedJob, executed_at: datetime) -> None:
        season, week = week_for(job)
        execution = execute_official_prefetch_job(
            job,
            season=season,
            week=week,
            cache=cache,
            subjects=initial.lineup.subjects,
            executed_at=executed_at,
        )
        if execution.needs_retry:
            # Later planned attempts remain scheduled; this makes the current
            # incomplete attempt visible to systemd without terminating the day.
            return

    def final(job: PlannedJob, decision_at: datetime) -> None:
        season, week = week_for(job)
        refreshed = _load_operational_snapshot(
            config,
            environment,
            decision_at=decision_at,
            schedule_as_of=start,
        ).lineup
        final_decision_at = max(
            datetime.now(timezone.utc),
            refreshed.refreshed_at.astimezone(timezone.utc),
        )

        def fallbacks(game: RelevantGame):
            with (
                SleeperStatusSource() as sleeper,
                NFLVerseStatusSource(season=season, week=week) as nflverse,
            ):
                return sleeper.fetch_game(game), nflverse.fetch_game(game)

        execute_official_final_job(
            job,
            season=season,
            week=week,
            cache=cache,
            refresh_lineups=lambda: refreshed,
            fetch_fallback_reports=fallbacks,
            notifier=SMTPEmailNotifier(config.email, environment),
            decision_at=final_decision_at,
            display_timezone=config.timezone_info,
        )

    return run_game_day_service(
        plan,
        execute_prefetch=prefetch,
        execute_final=final,
    )


def _load_operational_snapshot(
    config: AppConfig,
    environment: EnvironmentConfig,
    *,
    decision_at: datetime,
    schedule_as_of: datetime | None = None,
) -> OperationalSnapshot:
    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        raise ProductionServiceError("decision_at must be timezone-aware")
    decision_at = decision_at.astimezone(timezone.utc)
    assignment_time = schedule_as_of or decision_at
    if assignment_time.tzinfo is None or assignment_time.utcoffset() is None:
        raise ProductionServiceError("schedule_as_of must be timezone-aware")
    assignment_time = assignment_time.astimezone(timezone.utc)

    with (
        SleeperClient() as sleeper,
        ESPNClient(swid=environment.espn_swid, espn_s2=environment.espn_s2) as espn,
    ):
        rosters = FantasyManager(sleeper=sleeper, espn=espn).get_all_rosters(
            config, environment
        )
        refresh_evidence = _refresh_evidence(sleeper, espn, rosters)

    nflverse = NFLVerseSource()
    core = nflverse.load_snapshot(config.season)
    required = {
        "schedules": core.schedules.frame,
        "players": core.players.frame,
        "rosters": core.rosters.frame,
        "fantasy player IDs": core.fantasy_player_ids.frame,
    }
    missing = [name for name, frame in required.items() if frame is None]
    if missing:
        raise ProductionServiceError(
            "Cannot build live game-day state; unavailable nflverse data: "
            + ", ".join(missing)
        )
    resolver = PlayerIdentityResolver.from_nflverse(
        fantasy_player_ids=required["fantasy player IDs"],
        players=required["players"],
        rosters=required["rosters"],
    )
    mapping = map_rosters_to_nfl(rosters, resolver)
    if mapping.unresolved:
        names = ", ".join(item.player_name for item in mapping.unresolved)
        raise ProductionServiceError(f"Unresolved fantasy players block deployment: {names}")
    schedule = parse_nfl_schedule(required["schedules"])
    assignments = assign_next_games(mapping.rosters, schedule, as_of=assignment_time)
    if assignments.data_errors:
        raise ProductionServiceError("NFL schedule contains errors for owned players")
    kickoff_plan = group_kickoff_windows(assignments)
    players = tuple(player for roster in mapping.rosters for player in roster.players)

    depth_relations = None
    if config.depth_chart.enabled:
        depth = load_latest_depth_snapshot(
            config.season,
            as_of=decision_at,
            max_age_hours=config.depth_chart.max_age_hours,
            nflverse=nflverse,
        )
        joined = join_owned_skill_players(players, depth, identities=resolver.identities)
        depth_relations = build_owned_depth_relations(joined)
        subjects = build_status_scope(players, depth_relations).subjects
    else:
        subjects = subjects_from_fantasy_players(players)

    kickoffs = {
        item.player.canonical_player_id: item.game.kickoff
        for item in assignments.matched
        if item.game is not None and item.player.canonical_player_id is not None
    }
    game_weeks = {game.game_id: (game.season, game.week) for game in schedule.games}
    refreshed_at = max(
        (item.retrieved_at for item in refresh_evidence),
        default=decision_at,
    )
    lineup = FinalLineupSnapshot(
        rosters=mapping.rosters,
        subjects=subjects,
        kickoffs_by_canonical_player_id=kickoffs,
        refreshed_at=refreshed_at,
        refresh_evidence=refresh_evidence,
        depth_relations=depth_relations,
    )
    return OperationalSnapshot(lineup, kickoff_plan, game_weeks)


def _refresh_evidence(
    sleeper: SleeperClient,
    espn: ESPNClient,
    rosters,
) -> tuple[LineupRefreshEvidence, ...]:
    evidence: list[LineupRefreshEvidence] = []
    platforms = {roster.league.platform.value for roster in rosters}
    if "sleeper" in platforms:
        metadata = tuple(sleeper.response_metadata.values())
        if not metadata:
            raise ProductionServiceError("Sleeper returned no response freshness metadata")
        ages = [
            item.cache_age_seconds
            for item in metadata
            if item.cache_age_seconds is not None
        ]
        evidence.append(
            LineupRefreshEvidence(
                "sleeper",
                max(item.retrieved_at for item in metadata),
                max(ages) if ages else None,
                "provider responses may be edge-cached",
            )
        )
    if "espn" in platforms:
        metadata = espn.response_metadata
        if metadata is None:
            raise ProductionServiceError("ESPN returned no response freshness metadata")
        evidence.append(
            LineupRefreshEvidence(
                "espn", metadata.retrieved_at, metadata.cache_age_seconds
            )
        )
    return tuple(evidence)
