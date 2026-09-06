from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.analysis import StatusSubject, combine_official_statuses
from app.models import (
    FantasyLeague,
    FantasyPlatform,
    FantasyPlayer,
    FantasyRoster,
    GameDayState,
    GameSourceReport,
    LineupRefreshEvidence,
    RelevantGame,
    ReportState,
    RosterEligibility,
    SourceResult,
)
from app.scheduling import (
    FinalExecutionError,
    FinalLineupSnapshot,
    PlannedJob,
    PlannedJobKind,
    execute_final_job,
    render_final_execution,
)
from app.storage import StatusCache


KICKOFF = datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc)
DECISION_AT = KICKOFF - timedelta(minutes=5)
GAME = RelevantGame("2026_01_BAL_BUF", "BUF", "BAL", KICKOFF, ())


class RecordingNotifier:
    def __init__(self) -> None:
        self.messages = []

    def send_game_alert(self, text_email, html_email=None) -> None:
        self.messages.append((text_email, html_email))


def _job(kind: PlannedJobKind = PlannedJobKind.FINAL) -> PlannedJob:
    return PlannedJob(
        "final-20260913T170000Z-t5",
        kind,
        DECISION_AT,
        KICKOFF,
        5,
        (GAME,),
    )


def _league() -> FantasyLeague:
    return FantasyLeague(
        id="league-1",
        name="League One",
        nickname="One",
        platform=FantasyPlatform.SLEEPER,
        roster_id="1",
        roster_rules={"roster_positions": ("RB", "BN")},
        scoring_settings={},
    )


def _player(player_id: str, *, starter: bool) -> FantasyPlayer:
    league = _league()
    return FantasyPlayer(
        platform_player_id=player_id,
        name=player_id.title(),
        nfl_team="BAL",
        position="RB",
        league_id=league.id,
        league_name=league.name,
        platform=league.platform,
        lineup_slot="RB" if starter else "BN",
        eligible_slots=("RB",),
        is_starter=starter,
        canonical_player_id=player_id,
    )


def _snapshot(*, swapped: bool = False) -> FinalLineupSnapshot:
    starter = _player("bench" if swapped else "starter", starter=True)
    bench = _player("starter" if swapped else "bench", starter=False)
    roster = FantasyRoster(_league(), "My Team", (starter, bench))
    return FinalLineupSnapshot(
        rosters=(roster,),
        subjects=tuple(
            StatusSubject(
                player.canonical_player_id or "",
                player.name,
                player.nfl_team,
                player.position,
                RosterEligibility.ELIGIBLE,
            )
            for player in roster.players
        ),
        kickoffs_by_canonical_player_id={"starter": KICKOFF, "bench": KICKOFF},
        refreshed_at=DECISION_AT,
        refresh_evidence=(
            LineupRefreshEvidence("sleeper", DECISION_AT, 240, "response may be cached"),
        ),
    )


def _report(
    source: str,
    state: ReportState = ReportState.COMPLETE,
    *,
    inactive_id: str | None = "starter",
) -> GameSourceReport:
    results = ()
    if source == "nfl_inactives" and inactive_id:
        results = (
            SourceResult(
                source=source,
                source_url="https://www.nfl.com/inactives/",
                success=True,
                report_state=state,
                retrieved_at=DECISION_AT,
                game_day_state=GameDayState.INACTIVE,
                player_name=inactive_id.title(),
                nfl_team="BAL",
                position="RB",
                canonical_player_id=inactive_id,
            ),
        )
    teams = frozenset({"BAL", "BUF"})
    return GameSourceReport(
        source=source,
        game_id=GAME.game_id,
        report_state=state,
        expected_teams=teams,
        parsed_teams=teams if state is ReportState.COMPLETE else frozenset(),
        player_results=results,
        retrieved_at=DECISION_AT,
        errors=("source unavailable",) if state is ReportState.FAILED else (),
    )


def _official_reports(*, inactive_id: str | None = "starter"):
    return (
        _report("nfl_inactives", inactive_id=inactive_id),
        _report("nfl_injuries", inactive_id=None),
    )


def test_final_job_refreshes_lineup_then_sends_one_complete_window(tmp_path: Path) -> None:
    refresh_calls = 0
    notifier = RecordingNotifier()

    def refresh() -> FinalLineupSnapshot:
        nonlocal refresh_calls
        refresh_calls += 1
        return _snapshot(swapped=True)

    execution = execute_final_job(
        _job(),
        cache=StatusCache(tmp_path),
        refresh_lineups=refresh,
        fetch_official_reports=lambda _game: _official_reports(inactive_id="starter"),
        notifier=notifier,
        decision_at=DECISION_AT,
        display_timezone=ZoneInfo("America/New_York"),
    )

    assert refresh_calls == 1
    assert len(notifier.messages) == 1
    assert "Bench — STARTING" in execution.text_email.body
    assert "Starter — BENCH" in execution.text_email.body
    assert "Sleeper lineup:" in execution.text_email.body
    assert "HTTP cache age 240 seconds" in execution.text_email.body
    assert "Notification sent: yes" in render_final_execution(execution)


def test_failed_final_refresh_uses_prefetch_cache_and_still_sends(tmp_path: Path) -> None:
    cache = StatusCache(tmp_path)
    cached_at = DECISION_AT - timedelta(minutes=85)
    cached_reports = tuple(
        replace(report, retrieved_at=cached_at) for report in _official_reports()
    )
    cache.save_prefetch(
        game_id=GAME.game_id,
        reports=cached_reports,
        statuses=combine_official_statuses(
            _snapshot().subjects,
            cached_reports,
            decision_at=cached_at,
        ),
        cached_at=cached_at,
    )
    notifier = RecordingNotifier()

    execution = execute_final_job(
        _job(),
        cache=cache,
        refresh_lineups=_snapshot,
        fetch_official_reports=lambda _game: (
            _report("nfl_inactives", ReportState.FAILED, inactive_id=None),
            _report("nfl_injuries", ReportState.FAILED, inactive_id=None),
        ),
        notifier=notifier,
        decision_at=DECISION_AT,
        display_timezone=timezone.utc,
    )

    assert execution.used_cache is True
    assert all(
        status.confidence.name == "HIGH"
        for status in execution.status_resolutions[0].statuses
    )
    assert len(notifier.messages) == 1
    assert "Using cached official snapshot" in execution.text_email.body
    assert "Used T-90 cache: yes" in render_final_execution(execution)


def test_final_job_rejects_wrong_kind_before_refreshing(tmp_path: Path) -> None:
    with pytest.raises(FinalExecutionError, match="Only final"):
        execute_final_job(
            _job(PlannedJobKind.PREFETCH),
            cache=StatusCache(tmp_path),
            refresh_lineups=lambda: pytest.fail("must not refresh"),
            fetch_official_reports=lambda _game: (),
            notifier=RecordingNotifier(),
            decision_at=DECISION_AT,
            display_timezone=timezone.utc,
        )


def test_official_exception_without_cache_becomes_explicit_unknown_email(tmp_path: Path) -> None:
    notifier = RecordingNotifier()

    def fail(_game):
        raise TimeoutError("temporary outage")

    execution = execute_final_job(
        _job(),
        cache=StatusCache(tmp_path),
        refresh_lineups=_snapshot,
        fetch_official_reports=fail,
        notifier=notifier,
        decision_at=DECISION_AT,
        display_timezone=timezone.utc,
    )

    assert "Official Refresh: FAILED" in execution.text_email.body
    assert "game-day status unknown" in execution.text_email.body.lower()
    assert len(notifier.messages) == 1


def test_degraded_official_reports_consult_fallback_sources(tmp_path: Path) -> None:
    fallback_calls = 0

    def fallback(_game):
        nonlocal fallback_calls
        fallback_calls += 1
        return (_report("sleeper_status", inactive_id=None),)

    execution = execute_final_job(
        _job(),
        cache=StatusCache(tmp_path),
        refresh_lineups=_snapshot,
        fetch_official_reports=lambda _game: (
            _report("nfl_inactives", ReportState.PARTIAL, inactive_id=None),
            _report("nfl_injuries", inactive_id=None),
        ),
        fetch_fallback_reports=fallback,
        notifier=RecordingNotifier(),
        decision_at=DECISION_AT,
        display_timezone=timezone.utc,
    )

    assert fallback_calls == 1
    assert any(report.source == "sleeper_status" for report in execution.source_reports)
    assert "Sleeper status fallback: COMPLETE" in execution.text_email.body


def test_complete_official_reports_still_consult_roster_eligibility_source(
    tmp_path: Path,
) -> None:
    fallback_calls = 0

    def fallback(_game):
        nonlocal fallback_calls
        fallback_calls += 1
        return (_report("sleeper_status", inactive_id=None),)

    execute_final_job(
        _job(),
        cache=StatusCache(tmp_path),
        refresh_lineups=_snapshot,
        fetch_official_reports=lambda _game: _official_reports(),
        fetch_fallback_reports=fallback,
        notifier=RecordingNotifier(),
        decision_at=DECISION_AT,
        display_timezone=timezone.utc,
    )

    assert fallback_calls == 1
