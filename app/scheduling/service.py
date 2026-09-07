"""Run a planned game day in one supervised process."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.scheduling.planner import GameDayPlan, PlannedJob, PlannedJobKind
from app.structured_logging import emit, get_logger


JobExecutor = Callable[[PlannedJob, datetime], None]
Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]


@dataclass(frozen=True)
class ServiceJobResult:
    """Operational outcome for one planned invocation."""

    job: PlannedJob
    state: str
    handled_at: datetime
    error: str | None = None
    latency_ms: int | None = None


@dataclass(frozen=True)
class GameDayServiceResult:
    """All job outcomes from one local game-day service run."""

    plan: GameDayPlan
    jobs: tuple[ServiceJobResult, ...]

    @property
    def failed(self) -> tuple[ServiceJobResult, ...]:
        return tuple(result for result in self.jobs if result.state != "complete")


def run_game_day_service(
    plan: GameDayPlan,
    *,
    execute_prefetch: JobExecutor,
    execute_final: JobExecutor,
    clock: Clock | None = None,
    sleep: Sleeper = time.sleep,
    late_tolerance: timedelta = timedelta(minutes=2),
) -> GameDayServiceResult:
    """Wait for and execute each future job, isolating individual failures.

    A small lateness allowance absorbs process wake-up jitter. Jobs outside that
    allowance are reported as missed instead of sending stale alerts.
    """

    if late_tolerance < timedelta(0):
        raise ValueError("late_tolerance must not be negative")
    now = clock or (lambda: datetime.now(timezone.utc))
    logger = get_logger("scheduling.service")
    emit(
        "game_day.started",
        logger=logger,
        game_date=plan.game_date.isoformat(),
        timezone=plan.timezone,
        scheduled_jobs=len(plan.jobs),
        missed_jobs=len(plan.missed_jobs),
    )
    results: list[ServiceJobResult] = []
    for job in plan.jobs:
        current = _aware_utc(now(), "clock")
        delay = (job.run_at - current).total_seconds()
        if delay > 0:
            sleep(delay)
            current = _aware_utc(now(), "clock")
        if current - job.run_at > late_tolerance:
            results.append(ServiceJobResult(job, "missed", current))
            emit(
                "job.missed",
                logger=logger,
                job_id=job.job_id,
                kind=job.kind.value,
                run_at=job.run_at,
                kickoff=job.kickoff,
                game_ids=[game.game_id for game in job.games],
                handled_at=current,
                success=False,
            )
            continue

        executor = execute_prefetch if job.kind is PlannedJobKind.PREFETCH else execute_final
        emit(
            "job.started",
            logger=logger,
            job_id=job.job_id,
            kind=job.kind.value,
            run_at=job.run_at,
            kickoff=job.kickoff,
            game_ids=[game.game_id for game in job.games],
            handled_at=current,
        )
        started = time.perf_counter()
        try:
            executor(job, current)
        except Exception as exc:  # keep later kickoff windows alive under supervision
            latency_ms = int((time.perf_counter() - started) * 1000)
            results.append(
                ServiceJobResult(
                    job,
                    "failed",
                    current,
                    f"{type(exc).__name__}: {exc}",
                    latency_ms,
                )
            )
            emit(
                "job.failed",
                logger=logger,
                job_id=job.job_id,
                kind=job.kind.value,
                game_ids=[game.game_id for game in job.games],
                handled_at=current,
                latency_ms=latency_ms,
                success=False,
                exception_type=type(exc).__name__,
                error=str(exc),
            )
        else:
            latency_ms = int((time.perf_counter() - started) * 1000)
            results.append(ServiceJobResult(job, "complete", current, latency_ms=latency_ms))
            emit(
                "job.complete",
                logger=logger,
                job_id=job.job_id,
                kind=job.kind.value,
                game_ids=[game.game_id for game in job.games],
                handled_at=current,
                latency_ms=latency_ms,
                success=True,
            )
    result = GameDayServiceResult(plan, tuple(results))
    emit(
        "game_day.finished",
        logger=logger,
        game_date=plan.game_date.isoformat(),
        jobs_handled=len(result.jobs),
        failures=len(result.failed),
        success=not result.failed,
    )
    return result


def render_game_day_service(result: GameDayServiceResult) -> str:
    """Render one concise line per operational job outcome."""

    lines = [
        f"Game-day service: {result.plan.game_date.isoformat()} ({result.plan.timezone})",
        f"Jobs handled: {len(result.jobs)}",
        f"Failures: {len(result.failed)}",
    ]
    for item in result.jobs:
        detail = f" — {item.error}" if item.error else ""
        latency = f" ({item.latency_ms} ms)" if item.latency_ms is not None else ""
        lines.append(
            f"  {item.job.job_id}: {item.state} at "
            f"{item.handled_at.isoformat().replace('+00:00', 'Z')}{latency}{detail}"
        )
    return "\n".join(lines)


def _aware_utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must return timezone-aware datetimes")
    return value.astimezone(timezone.utc)
