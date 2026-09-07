import json
import logging
from datetime import date, datetime, timedelta, timezone
from io import StringIO

from app.models import (
    Confidence,
    GameDayState,
    GameSourceReport,
    InjuryDesignation,
    NFLPlayerStatus,
    ReportState,
    RosterEligibility,
)
from app.scheduling import (
    GameDayPlan,
    PlannedJob,
    PlannedJobKind,
    render_game_day_service,
    run_game_day_service,
)
from app.structured_logging import (
    configure_structured_logging,
    emit,
    is_sensitive_key,
    player_status_fields,
    redact_mapping,
    source_report_fields,
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


def test_redacts_sensitive_credential_keys() -> None:
    payload = redact_mapping(
        {
            "espn_s2": "secret-cookie",
            "ESPN_SWID": "{abc}",
            "smtp_app_password": "app-pass",
            "Authorization": "Bearer xyz",
            "game_id": "2026_01_GB_CHI",
            "nested": {"password": "hidden", "source": "nfl_inactives"},
        }
    )

    assert payload["espn_s2"] == "[REDACTED]"
    assert payload["ESPN_SWID"] == "[REDACTED]"
    assert payload["smtp_app_password"] == "[REDACTED]"
    assert payload["Authorization"] == "[REDACTED]"
    assert payload["game_id"] == "2026_01_GB_CHI"
    assert payload["nested"]["password"] == "[REDACTED]"
    assert payload["nested"]["source"] == "nfl_inactives"
    assert is_sensitive_key("Cookie")


def test_configure_structured_logging_emits_json_lines() -> None:
    stream = StringIO()
    logger = configure_structured_logging(stream=stream)

    emit(
        "job.complete",
        logger=logger,
        job_id="final-5",
        latency_ms=12,
        success=True,
        espn_s2="should-not-appear",
    )

    payload = json.loads(stream.getvalue().strip())
    assert payload["event"] == "job.complete"
    assert payload["job_id"] == "final-5"
    assert payload["latency_ms"] == 12
    assert payload["success"] is True
    assert payload["espn_s2"] == "[REDACTED]"
    assert "should-not-appear" not in stream.getvalue()
    assert payload["timestamp"].endswith("Z")


def test_source_and_status_field_helpers_are_json_safe() -> None:
    retrieved = datetime(2026, 9, 7, 16, 0, tzinfo=timezone.utc)
    report = GameSourceReport(
        source="nfl_inactives",
        game_id="2026_01_GB_CHI",
        report_state=ReportState.COMPLETE,
        expected_teams=frozenset({"GB", "CHI"}),
        parsed_teams=frozenset({"GB", "CHI"}),
        player_results=(),
        retrieved_at=retrieved,
        http_cache_age_seconds=8,
    )
    status = NFLPlayerStatus(
        canonical_player_id="00-0036360",
        roster_eligibility=RosterEligibility.ELIGIBLE,
        game_day_state=GameDayState.ACTIVE,
        injury_designation=InjuryDesignation.QUESTIONABLE,
        confidence=Confidence.OFFICIAL,
        decision_at=retrieved,
        nfl_team="NE",
        position="RB",
    )

    assert source_report_fields(report)["parsed_teams"] == ["CHI", "GB"]
    assert player_status_fields(status)["injury_designation"] == "questionable"
    json.dumps(source_report_fields(report))
    json.dumps(player_status_fields(status))


def test_service_emits_structured_job_lifecycle_events() -> None:
    stream = StringIO()
    configure_structured_logging(stream=stream)
    fake = FakeTime()

    def fail(_job: PlannedJob, _at: datetime) -> None:
        raise RuntimeError("boom")

    result = run_game_day_service(
        _plan(_job(PlannedJobKind.PREFETCH, 0), _job(PlannedJobKind.FINAL, 1)),
        execute_prefetch=lambda _job, _at: None,
        execute_final=fail,
        clock=fake.now,
        sleep=fake.sleep,
    )

    events = [json.loads(line)["event"] for line in stream.getvalue().splitlines()]
    assert events[0] == "game_day.started"
    assert "job.started" in events
    assert "job.complete" in events
    assert "job.failed" in events
    assert events[-1] == "game_day.finished"
    assert result.jobs[1].state == "failed"
    assert "boom" in (result.jobs[1].error or "")
    assert "Failures: 1" in render_game_day_service(result)


def test_unconfigured_emit_does_not_raise() -> None:
    logging.getLogger("fantasy_watchdog").handlers.clear()
    emit("noop.event", success=True)
