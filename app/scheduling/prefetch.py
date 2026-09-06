"""Execute silent official-status prefetch jobs for relevant NFL games."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Sequence

from app.analysis.availability import StatusSubject, combine_official_statuses
from app.models import GameSourceReport, RelevantGame, ReportState
from app.nfl.identity import normalize_team
from app.nfl.sources.nfl_inactives import NFLInactivesSource
from app.nfl.sources.nfl_injuries import NFLInjuryReportSource
from app.scheduling.planner import PlannedJob, PlannedJobKind
from app.storage.cache import CachedStatusSnapshot, StatusCache


REQUIRED_OFFICIAL_SOURCES = frozenset({"nfl_inactives", "nfl_injuries"})


class PrefetchExecutionError(ValueError):
    """Raised when a scheduler job cannot be executed safely."""


@dataclass(frozen=True)
class PrefetchGameResult:
    """The result of one game's prefetch attempt."""

    game: RelevantGame
    attempted: bool
    complete: bool
    snapshot: CachedStatusSnapshot | None = None
    error: str | None = None


@dataclass(frozen=True)
class PrefetchExecution:
    """A silent prefetch execution across one exact kickoff window."""

    job: PlannedJob
    executed_at: datetime
    game_results: tuple[PrefetchGameResult, ...]

    @property
    def complete(self) -> bool:
        return bool(self.game_results) and all(result.complete for result in self.game_results)

    @property
    def needs_retry(self) -> bool:
        return not self.complete


ReportFetcher = Callable[[RelevantGame], Sequence[GameSourceReport]]


def execute_prefetch_job(
    job: PlannedJob,
    *,
    cache: StatusCache,
    fetch_reports: ReportFetcher,
    subjects: Sequence[StatusSubject] = (),
    executed_at: datetime,
) -> PrefetchExecution:
    """Fetch and cache official reports without sending a notification.

    A complete cached snapshot suppresses later retry attempts for that game. Any
    incomplete source state remains retryable at the next planned prefetch time.
    """

    if job.kind is not PlannedJobKind.PREFETCH:
        raise PrefetchExecutionError("Only prefetch jobs can use the prefetch executor")
    if executed_at.tzinfo is None:
        raise PrefetchExecutionError("executed_at must be timezone-aware")
    executed_at = executed_at.astimezone(timezone.utc)

    results: list[PrefetchGameResult] = []
    for game in job.games:
        cached = cache.load_latest(game.game_id)
        if cached is not None and _reports_complete(cached.reports):
            results.append(
                PrefetchGameResult(
                    game=game,
                    attempted=False,
                    complete=True,
                    snapshot=cached,
                )
            )
            continue

        try:
            reports = tuple(fetch_reports(game))
            _validate_reports(game, reports)
            game_subjects = _subjects_for_game(subjects, game)
            statuses = combine_official_statuses(
                game_subjects,
                reports,
                decision_at=executed_at,
            )
            snapshot = cache.save_prefetch(
                game_id=game.game_id,
                reports=reports,
                statuses=statuses,
                cached_at=executed_at,
            )
            results.append(
                PrefetchGameResult(
                    game=game,
                    attempted=True,
                    complete=_reports_complete(reports),
                    snapshot=snapshot,
                )
            )
        except Exception as exc:  # one source/game must not prevent other games from caching
            results.append(
                PrefetchGameResult(
                    game=game,
                    attempted=True,
                    complete=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    return PrefetchExecution(
        job=job,
        executed_at=executed_at,
        game_results=tuple(results),
    )


def execute_official_prefetch_job(
    job: PlannedJob,
    *,
    season: int,
    week: int,
    cache: StatusCache,
    subjects: Sequence[StatusSubject] = (),
    executed_at: datetime,
    inactives_source: NFLInactivesSource | None = None,
    injuries_source: NFLInjuryReportSource | None = None,
) -> PrefetchExecution:
    """Execute a planned prefetch using the two official NFL.com sources."""

    with ExitStack() as stack:
        inactives = inactives_source or stack.enter_context(NFLInactivesSource())
        injuries = injuries_source or stack.enter_context(
            NFLInjuryReportSource(season=season, week=week)
        )
        return execute_prefetch_job(
            job,
            cache=cache,
            fetch_reports=lambda game: (
                inactives.fetch_game(game),
                injuries.fetch_game(game),
            ),
            subjects=subjects,
            executed_at=executed_at,
        )


def render_prefetch_execution(execution: PrefetchExecution) -> str:
    """Render a concise operational record without implying an email was sent."""

    lines = [
        f"Prefetch job: {execution.job.job_id}",
        f"Executed at: {execution.executed_at.isoformat().replace('+00:00', 'Z')}",
        f"Games: {len(execution.game_results)}",
        f"Complete: {'yes' if execution.complete else 'no'}",
        f"Needs retry: {'yes' if execution.needs_retry else 'no'}",
        "Notification sent: no",
    ]
    for result in execution.game_results:
        action = "fetched" if result.attempted else "cached-complete"
        state = "complete" if result.complete else "incomplete"
        detail = f" — {result.error}" if result.error else ""
        lines.append(f"  {result.game.game_id}: {action}, {state}{detail}")
    return "\n".join(lines)


def _reports_complete(reports: Sequence[GameSourceReport]) -> bool:
    states = {report.source: report.report_state for report in reports}
    return all(states.get(source) is ReportState.COMPLETE for source in REQUIRED_OFFICIAL_SOURCES)


def _validate_reports(game: RelevantGame, reports: Sequence[GameSourceReport]) -> None:
    if not reports:
        raise PrefetchExecutionError(f"No official reports returned for {game.game_id}")
    wrong_game = sorted({report.game_id for report in reports if report.game_id != game.game_id})
    if wrong_game:
        raise PrefetchExecutionError(
            f"Official reports for {game.game_id} contained other game IDs: "
            + ", ".join(wrong_game)
        )
    duplicates = [
        source
        for source in {report.source for report in reports}
        if sum(report.source == source for report in reports) > 1
    ]
    if duplicates:
        raise PrefetchExecutionError(
            "Official reports contained duplicate sources: " + ", ".join(sorted(duplicates))
        )


def _subjects_for_game(
    subjects: Sequence[StatusSubject], game: RelevantGame
) -> tuple[StatusSubject, ...]:
    teams = {normalize_team(game.home_team), normalize_team(game.away_team)}
    return tuple(subject for subject in subjects if normalize_team(subject.nfl_team) in teams)
