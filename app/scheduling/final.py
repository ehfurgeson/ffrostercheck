"""Execute one final T−5 kickoff-window alert."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from typing import Callable, Mapping, Sequence

from app.analysis import (
    StatusSubject,
    analyze_depth_opportunities,
    combine_official_statuses,
    find_valid_bench_substitutes,
    map_nfl_statuses_to_fantasy_leagues,
    parse_league_roster_eligibility,
)
from app.models import (
    FantasyRoster,
    GameSourceReport,
    LineupRefreshEvidence,
    NFLPlayerStatus,
    RelevantGame,
    ReportState,
)
from app.nfl.depth_relations import OwnedDepthRelations
from app.nfl.identity import normalize_team
from app.nfl.sources.nfl_inactives import NFLInactivesSource
from app.nfl.sources.nfl_injuries import NFLInjuryReportSource
from app.notification import (
    EmailNotifier,
    HtmlEmail,
    TextEmail,
    build_html_email,
    build_text_email,
)
from app.scheduling.planner import PlannedJob, PlannedJobKind
from app.storage import StatusCache, StatusCacheError, StatusResolution


class FinalExecutionError(ValueError):
    """Raised when a final job or refreshed lineup snapshot is unsafe."""


@dataclass(frozen=True)
class FinalLineupSnapshot:
    """Refreshed and re-resolved fantasy state used by one final decision."""

    rosters: tuple[FantasyRoster, ...]
    subjects: tuple[StatusSubject, ...]
    kickoffs_by_canonical_player_id: Mapping[str, datetime]
    refreshed_at: datetime
    refresh_evidence: tuple[LineupRefreshEvidence, ...]
    depth_relations: OwnedDepthRelations | None = None


@dataclass(frozen=True)
class FinalExecution:
    """Delivered output and evidence for one exact kickoff window."""

    job: PlannedJob
    decision_at: datetime
    lineup_snapshot: FinalLineupSnapshot
    status_resolutions: tuple[StatusResolution, ...]
    text_email: TextEmail
    html_email: HtmlEmail
    source_reports: tuple[GameSourceReport, ...]

    @property
    def used_cache(self) -> bool:
        return any(resolution.used_cache for resolution in self.status_resolutions)


LineupRefresher = Callable[[], FinalLineupSnapshot]
ReportFetcher = Callable[[RelevantGame], Sequence[GameSourceReport]]


def execute_final_job(
    job: PlannedJob,
    *,
    cache: StatusCache,
    refresh_lineups: LineupRefresher,
    fetch_official_reports: ReportFetcher,
    notifier: EmailNotifier,
    decision_at: datetime,
    display_timezone: tzinfo,
    fetch_fallback_reports: ReportFetcher | None = None,
) -> FinalExecution:
    """Refresh, resolve, analyze, render, and deliver one T−5 alert."""

    _validate_final_job(job, decision_at)
    decision_at = decision_at.astimezone(timezone.utc)
    snapshot = refresh_lineups()
    _validate_lineup_snapshot(snapshot, decision_at)
    teams = {
        normalize_team(team)
        for game in job.games
        for team in (game.home_team, game.away_team)
    }
    subjects = tuple(
        subject for subject in snapshot.subjects if normalize_team(subject.nfl_team) in teams
    )
    resolutions: list[StatusResolution] = []
    reports: list[GameSourceReport] = []
    statuses: list[NFLPlayerStatus] = []
    for game in job.games:
        game_subjects = _subjects_for_game(subjects, game)
        try:
            resolution = cache.resolve_final(
                game_id=game.game_id,
                fetch_reports=lambda game=game: _validated_reports(
                    game, fetch_official_reports(game)
                ),
                subjects=game_subjects,
                decision_at=decision_at,
            )
        except StatusCacheError as exc:
            failed = _failed_resolution_report(game, decision_at, exc)
            resolution = cache.resolve_final(
                game_id=game.game_id,
                fetch_reports=lambda failed=failed: (failed,),
                subjects=game_subjects,
                decision_at=decision_at,
            )
        resolutions.append(resolution)
        game_reports = list(resolution.reports)
        if fetch_fallback_reports is not None and (
            not resolution.origin_fresh or not _official_reports_complete(game_reports)
        ):
            try:
                fallback_reports = _validated_reports(
                    game,
                    fetch_fallback_reports(game),
                    existing_sources={report.source for report in game_reports},
                )
            except Exception as exc:
                fallback_reports = (_failed_fallback_report(game, decision_at, exc),)
            game_reports.extend(fallback_reports)
        reports.extend(game_reports)
        if resolution.used_cache and resolution.errors:
            reports.append(
                _refresh_limitation_report(game, decision_at, resolution.errors)
            )
        statuses.extend(
            combine_official_statuses(
                game_subjects,
                game_reports,
                decision_at=decision_at,
                origin_fresh=resolution.origin_fresh,
            )
        )

    window_rosters = _window_rosters(snapshot.rosters, teams)
    mapping = map_nfl_statuses_to_fantasy_leagues(window_rosters, statuses)
    opportunities = (
        analyze_depth_opportunities(snapshot.depth_relations, statuses).opportunities
        if snapshot.depth_relations is not None
        else ()
    )
    replacement_options = tuple(
        option
        for league_statuses in mapping.leagues
        for option in find_valid_bench_substitutes(
            league_statuses,
            parse_league_roster_eligibility(league_statuses.league),
            opportunities,
            kickoffs_by_canonical_player_id=snapshot.kickoffs_by_canonical_player_id,
            decision_at=decision_at,
        )
    )
    text_email = build_text_email(
        job.kickoff,
        mapping.leagues,
        replacement_options,
        decision_at=decision_at,
        source_reports=reports,
        display_timezone=display_timezone,
        minutes_before_kickoff=job.minutes_before_kickoff,
        lineup_refreshes=snapshot.refresh_evidence,
    )
    html_email = build_html_email(
        job.kickoff,
        mapping.leagues,
        replacement_options,
        decision_at=decision_at,
        source_reports=reports,
        display_timezone=display_timezone,
        minutes_before_kickoff=job.minutes_before_kickoff,
        lineup_refreshes=snapshot.refresh_evidence,
    )
    notifier.send_game_alert(text_email, html_email)
    return FinalExecution(
        job=job,
        decision_at=decision_at,
        lineup_snapshot=snapshot,
        status_resolutions=tuple(resolutions),
        text_email=text_email,
        html_email=html_email,
        source_reports=tuple(reports),
    )


def execute_official_final_job(
    job: PlannedJob,
    *,
    season: int,
    week: int,
    cache: StatusCache,
    refresh_lineups: LineupRefresher,
    notifier: EmailNotifier,
    decision_at: datetime,
    display_timezone: tzinfo,
    fetch_fallback_reports: ReportFetcher | None = None,
    inactives_source: NFLInactivesSource | None = None,
    injuries_source: NFLInjuryReportSource | None = None,
) -> FinalExecution:
    """Execute a final job using the two primary NFL.com sources."""

    with ExitStack() as stack:
        inactives = inactives_source or stack.enter_context(NFLInactivesSource())
        injuries = injuries_source or stack.enter_context(
            NFLInjuryReportSource(season=season, week=week)
        )
        return execute_final_job(
            job,
            cache=cache,
            refresh_lineups=refresh_lineups,
            fetch_official_reports=lambda game: (
                inactives.fetch_game(game),
                injuries.fetch_game(game),
            ),
            fetch_fallback_reports=fetch_fallback_reports,
            notifier=notifier,
            decision_at=decision_at,
            display_timezone=display_timezone,
        )


def render_final_execution(execution: FinalExecution) -> str:
    """Render a concise delivery record with refresh/cache disclosure."""

    return "\n".join(
        (
            f"Final job: {execution.job.job_id}",
            f"Decision: {execution.decision_at.isoformat().replace('+00:00', 'Z')}",
            f"Lineups refreshed: {len(execution.lineup_snapshot.refresh_evidence)} provider(s)",
            f"Official refreshes attempted: {len(execution.status_resolutions)}",
            f"Used T-90 cache: {'yes' if execution.used_cache else 'no'}",
            "Notification sent: yes",
        )
    )


def _validate_final_job(job: PlannedJob, decision_at: datetime) -> None:
    if job.kind is not PlannedJobKind.FINAL:
        raise FinalExecutionError("Only final jobs can use the final executor")
    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        raise FinalExecutionError("decision_at must be timezone-aware")
    if not job.games:
        raise FinalExecutionError("Final job must contain at least one game")


def _validate_lineup_snapshot(snapshot: FinalLineupSnapshot, decision_at: datetime) -> None:
    if snapshot.refreshed_at.tzinfo is None or snapshot.refreshed_at.utcoffset() is None:
        raise FinalExecutionError("lineup refreshed_at must be timezone-aware")
    if snapshot.refreshed_at > decision_at:
        raise FinalExecutionError("lineup refreshed_at cannot be after decision_at")
    if not snapshot.rosters:
        raise FinalExecutionError("final lineup refresh returned no rosters")
    if not snapshot.refresh_evidence:
        raise FinalExecutionError("final lineup refresh returned no freshness evidence")
    evidence_sources = {evidence.source.casefold() for evidence in snapshot.refresh_evidence}
    missing_platforms = sorted(
        {
            roster.league.platform.value
            for roster in snapshot.rosters
            if roster.league.platform.value not in evidence_sources
        }
    )
    if missing_platforms:
        raise FinalExecutionError(
            "final lineup refresh is missing freshness evidence for: "
            + ", ".join(missing_platforms)
        )
    for evidence in snapshot.refresh_evidence:
        if evidence.retrieved_at.tzinfo is None or evidence.retrieved_at.utcoffset() is None:
            raise FinalExecutionError("lineup refresh evidence must be timezone-aware")
        if evidence.retrieved_at > decision_at:
            raise FinalExecutionError("lineup refresh retrieval cannot be after decision_at")
        if evidence.http_cache_age_seconds is not None and evidence.http_cache_age_seconds < 0:
            raise FinalExecutionError("lineup refresh HTTP cache age must be at least 0")


def _subjects_for_game(
    subjects: Sequence[StatusSubject], game: RelevantGame
) -> tuple[StatusSubject, ...]:
    teams = {normalize_team(game.home_team), normalize_team(game.away_team)}
    return tuple(subject for subject in subjects if normalize_team(subject.nfl_team) in teams)


def _window_rosters(
    rosters: Sequence[FantasyRoster], teams: set[str | None]
) -> tuple[FantasyRoster, ...]:
    window_rosters: list[FantasyRoster] = []
    for roster in rosters:
        players = tuple(
            player for player in roster.players if normalize_team(player.nfl_team) in teams
        )
        if not players:
            continue
        window_rosters.append(
            FantasyRoster(
                league=roster.league,
                team_name=roster.team_name,
                players=players,
            )
        )
    return tuple(window_rosters)


def _official_reports_complete(reports: Sequence[GameSourceReport]) -> bool:
    states = {report.source: report.report_state for report in reports}
    return all(
        states.get(source) is ReportState.COMPLETE
        for source in ("nfl_inactives", "nfl_injuries")
    )


def _failed_resolution_report(
    game: RelevantGame, decision_at: datetime, error: Exception
) -> GameSourceReport:
    teams = frozenset({game.home_team, game.away_team})
    return GameSourceReport(
        source="official_refresh",
        game_id=game.game_id,
        report_state=ReportState.FAILED,
        expected_teams=teams,
        parsed_teams=frozenset(),
        player_results=(),
        retrieved_at=decision_at,
        errors=(str(error),),
    )


def _failed_fallback_report(
    game: RelevantGame, decision_at: datetime, error: Exception
) -> GameSourceReport:
    teams = frozenset({game.home_team, game.away_team})
    return GameSourceReport(
        source="fallback_refresh",
        game_id=game.game_id,
        report_state=ReportState.FAILED,
        expected_teams=teams,
        parsed_teams=frozenset(),
        player_results=(),
        retrieved_at=decision_at,
        errors=(f"{type(error).__name__}: {error}",),
    )


def _refresh_limitation_report(
    game: RelevantGame,
    decision_at: datetime,
    errors: Sequence[str],
) -> GameSourceReport:
    teams = frozenset({game.home_team, game.away_team})
    return GameSourceReport(
        source="official_refresh",
        game_id=game.game_id,
        report_state=ReportState.FAILED,
        expected_teams=teams,
        parsed_teams=frozenset(),
        player_results=(),
        retrieved_at=decision_at,
        errors=tuple(errors),
    )


def _validated_reports(
    game: RelevantGame,
    reports: Sequence[GameSourceReport],
    *,
    existing_sources: set[str] | None = None,
) -> tuple[GameSourceReport, ...]:
    validated = tuple(reports)
    wrong_games = sorted(
        {report.game_id for report in validated if report.game_id != game.game_id}
    )
    if wrong_games:
        raise FinalExecutionError(
            f"Reports for {game.game_id} contained other game IDs: "
            + ", ".join(wrong_games)
        )
    sources = set(existing_sources or ())
    for report in validated:
        if report.source in sources:
            raise FinalExecutionError(
                f"Duplicate report source for {game.game_id}: {report.source}"
            )
        sources.add(report.source)
    return validated
