from datetime import date, datetime, timedelta, timezone

from app.scheduling import (
    GameDayPlan,
    PlannedJob,
    PlannedJobKind,
    render_game_day_service,
    run_game_day_service,
)


NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


class FakeTime:
    def __init__(self) -> None:
        self.current = NOW
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)


def _job(kind: PlannedJobKind, minutes: int) -> PlannedJob:
    run_at = NOW + timedelta(minutes=minutes)
    return PlannedJob(
        job_id=f"{kind.value}-{minutes}",
        kind=kind,
        run_at=run_at,
        kickoff=run_at + timedelta(minutes=5),
        minutes_before_kickoff=5,
        games=(),
    )


def _plan(*jobs: PlannedJob) -> GameDayPlan:
    return GameDayPlan(
        planned_at=NOW,
        game_date=date(2026, 9, 13),
        timezone="America/New_York",
        windows=(),
        jobs=jobs,
        missed_jobs=(),
    )


def test_service_waits_and_dispatches_each_job_kind() -> None:
    fake = FakeTime()
    calls: list[tuple[str, str, datetime]] = []
    plan = _plan(_job(PlannedJobKind.PREFETCH, 1), _job(PlannedJobKind.FINAL, 2))

    result = run_game_day_service(
        plan,
        execute_prefetch=lambda job, at: calls.append(("prefetch", job.job_id, at)),
        execute_final=lambda job, at: calls.append(("final", job.job_id, at)),
        clock=fake.now,
        sleep=fake.sleep,
    )

    assert fake.sleeps == [60.0, 60.0]
    assert [call[:2] for call in calls] == [("prefetch", "prefetch-1"), ("final", "final-2")]
    assert [item.state for item in result.jobs] == ["complete", "complete"]


def test_service_isolates_failure_and_continues() -> None:
    fake = FakeTime()
    calls: list[str] = []

    def fail(job: PlannedJob, _at: datetime) -> None:
        calls.append(job.job_id)
        raise RuntimeError("source unavailable")

    result = run_game_day_service(
        _plan(_job(PlannedJobKind.PREFETCH, 0), _job(PlannedJobKind.FINAL, 1)),
        execute_prefetch=fail,
        execute_final=lambda job, _at: calls.append(job.job_id),
        clock=fake.now,
        sleep=fake.sleep,
    )

    assert calls == ["prefetch-0", "final-1"]
    assert [item.state for item in result.jobs] == ["failed", "complete"]
    assert "RuntimeError: source unavailable" in (result.jobs[0].error or "")
    assert "Failures: 1" in render_game_day_service(result)


def test_service_skips_stale_job_instead_of_sending_late_alert() -> None:
    stale = _job(PlannedJobKind.FINAL, 0)
    current = NOW + timedelta(minutes=3)
    called = False

    def execute(_job: PlannedJob, _at: datetime) -> None:
        nonlocal called
        called = True

    result = run_game_day_service(
        _plan(stale),
        execute_prefetch=execute,
        execute_final=execute,
        clock=lambda: current,
        sleep=lambda _seconds: None,
    )

    assert called is False
    assert result.jobs[0].state == "missed"


def test_service_rejects_naive_clock() -> None:
    try:
        run_game_day_service(
            _plan(_job(PlannedJobKind.FINAL, 0)),
            execute_prefetch=lambda _job, _at: None,
            execute_final=lambda _job, _at: None,
            clock=lambda: datetime(2026, 9, 13, 12, 0),
            sleep=lambda _seconds: None,
        )
    except ValueError as exc:
        assert "timezone-aware" in str(exc)
    else:
        raise AssertionError("naive clock should fail")
