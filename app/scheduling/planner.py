"""Build deterministic jobs for one local NFL game day."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

from app.config import AlertsConfig
from app.models import RelevantGame
from app.nfl.schedule import KickoffPlan, KickoffWindow


class PlannedJobKind(str, Enum):
    """The two stages that operate on a relevant kickoff window."""

    PREFETCH = "prefetch"
    FINAL = "final"


@dataclass(frozen=True)
class PlannedJob:
    """One future scheduler invocation for an exact kickoff window."""

    job_id: str
    kind: PlannedJobKind
    run_at: datetime
    kickoff: datetime
    minutes_before_kickoff: int
    games: tuple[RelevantGame, ...]


@dataclass(frozen=True)
class GameDayPlan:
    """Future and missed jobs for one date in the configured display timezone."""

    planned_at: datetime
    game_date: date
    timezone: str
    windows: tuple[KickoffWindow, ...]
    jobs: tuple[PlannedJob, ...]
    missed_jobs: tuple[PlannedJob, ...]

    @property
    def games(self) -> tuple[RelevantGame, ...]:
        return tuple(game for window in self.windows for game in window.games)


def build_game_day_plan(
    kickoff_plan: KickoffPlan,
    alerts: AlertsConfig,
    *,
    planned_at: datetime,
    display_timezone: ZoneInfo,
    game_date: date | None = None,
) -> GameDayPlan:
    """Create T-minus jobs for relevant games on one local calendar date.

    Jobs whose run time has already passed remain visible as missed diagnostics;
    callers should submit only ``jobs`` to a scheduler.
    """

    if planned_at.tzinfo is None:
        raise ValueError("planned_at must be timezone-aware")

    planned_at_utc = planned_at.astimezone(timezone.utc)
    selected_date = game_date or planned_at.astimezone(display_timezone).date()
    windows = tuple(
        window
        for window in kickoff_plan.windows
        if window.kickoff.astimezone(display_timezone).date() == selected_date
    )

    candidates: list[PlannedJob] = []
    for window in windows:
        for minutes in sorted(
            set(alerts.prefetch_attempts_minutes_before_kickoff), reverse=True
        ):
            candidates.append(
                _job(window, PlannedJobKind.PREFETCH, minutes_before=minutes)
            )
        candidates.append(
            _job(
                window,
                PlannedJobKind.FINAL,
                minutes_before=alerts.email_minutes_before_kickoff,
            )
        )

    candidates.sort(
        key=lambda job: (job.run_at, job.kickoff, job.kind.value, job.job_id)
    )
    jobs = tuple(job for job in candidates if job.run_at >= planned_at_utc)
    missed = tuple(job for job in candidates if job.run_at < planned_at_utc)
    return GameDayPlan(
        planned_at=planned_at_utc,
        game_date=selected_date,
        timezone=display_timezone.key,
        windows=windows,
        jobs=jobs,
        missed_jobs=missed,
    )


def render_game_day_plan(plan: GameDayPlan) -> str:
    """Render a plan with exact execution and kickoff times."""

    display_timezone = ZoneInfo(plan.timezone)
    lines = [
        f"Game-day plan: {plan.game_date.isoformat()} ({plan.timezone})",
        f"Planned at: {_format_time(plan.planned_at, display_timezone)}",
        f"Relevant kickoff windows: {len(plan.windows)}",
        f"Relevant games: {len(plan.games)}",
        f"Scheduled jobs: {len(plan.jobs)}",
        f"Missed jobs: {len(plan.missed_jobs)}",
    ]
    if plan.jobs:
        lines.append("Scheduled jobs:")
        lines.extend(_render_job(job, display_timezone) for job in plan.jobs)
    else:
        lines.append("Scheduled jobs: none")
    if plan.missed_jobs:
        lines.append("Missed jobs (not scheduled):")
        lines.extend(_render_job(job, display_timezone) for job in plan.missed_jobs)
    return "\n".join(lines)


def _job(
    window: KickoffWindow,
    kind: PlannedJobKind,
    *,
    minutes_before: int,
) -> PlannedJob:
    kickoff = window.kickoff.astimezone(timezone.utc)
    run_at = kickoff - timedelta(minutes=minutes_before)
    window_key = kickoff.strftime("%Y%m%dT%H%M%SZ")
    return PlannedJob(
        job_id=f"{kind.value}-{window_key}-t{minutes_before}",
        kind=kind,
        run_at=run_at,
        kickoff=kickoff,
        minutes_before_kickoff=minutes_before,
        games=window.games,
    )


def _render_job(job: PlannedJob, display_timezone: ZoneInfo) -> str:
    games = ", ".join(f"{game.away_team}@{game.home_team}" for game in job.games)
    return (
        f"  {_format_time(job.run_at, display_timezone)} — {job.kind.value} "
        f"T-{job.minutes_before_kickoff} — kickoff "
        f"{_format_time(job.kickoff, display_timezone)} — {games}"
    )


def _format_time(value: datetime, display_timezone: ZoneInfo) -> str:
    return value.astimezone(display_timezone).strftime("%Y-%m-%d %I:%M %p %Z")
