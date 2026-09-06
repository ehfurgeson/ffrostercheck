from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.analysis import StatusSubject
from app.models import GameSourceReport, RelevantGame, ReportState
from app.scheduling import (
    PlannedJob,
    PlannedJobKind,
    PrefetchExecutionError,
    execute_prefetch_job,
    render_prefetch_execution,
)
from app.storage import StatusCache


KICKOFF = datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc)
RUN_AT = datetime(2026, 9, 13, 15, 25, tzinfo=timezone.utc)


def _game(game_id: str = "2026_01_BAL_BUF", away: str = "BAL", home: str = "BUF"):
    return RelevantGame(game_id, home, away, KICKOFF, ())


def _job(*games: RelevantGame, kind: PlannedJobKind = PlannedJobKind.PREFETCH):
    return PlannedJob("prefetch-window", kind, RUN_AT, KICKOFF, 95, games)


def _report(game: RelevantGame, source: str, state: ReportState):
    teams = frozenset({game.home_team, game.away_team})
    return GameSourceReport(
        source=source,
        game_id=game.game_id,
        report_state=state,
        expected_teams=teams,
        parsed_teams=teams if state is ReportState.COMPLETE else frozenset(),
        player_results=(),
        retrieved_at=RUN_AT,
    )


def _reports(game: RelevantGame, inactives: ReportState, injuries: ReportState):
    return (
        _report(game, "nfl_inactives", inactives),
        _report(game, "nfl_injuries", injuries),
    )


def test_prefetch_fetches_and_caches_each_game_without_notification(tmp_path: Path) -> None:
    games = (_game(), _game("2026_01_PIT_CLE", "PIT", "CLE"))
    calls: list[str] = []

    execution = execute_prefetch_job(
        _job(*games),
        cache=StatusCache(tmp_path),
        fetch_reports=lambda game: calls.append(game.game_id)
        or _reports(game, ReportState.COMPLETE, ReportState.COMPLETE),
        subjects=(
            StatusSubject("bal-player", "Baltimore Player", "BAL", "WR"),
            StatusSubject("pit-player", "Pittsburgh Player", "PIT", "RB"),
        ),
        executed_at=RUN_AT,
    )

    assert calls == [game.game_id for game in games]
    assert execution.complete is True
    assert execution.needs_retry is False
    assert all(result.snapshot is not None for result in execution.game_results)
    assert [len(result.snapshot.statuses) for result in execution.game_results] == [1, 1]
    assert "Notification sent: no" in render_prefetch_execution(execution)


def test_incomplete_snapshot_is_cached_and_retried_at_next_attempt(tmp_path: Path) -> None:
    game = _game()
    cache = StatusCache(tmp_path)
    states = [ReportState.NOT_YET_PUBLISHED, ReportState.COMPLETE]

    def fetch(current_game: RelevantGame):
        return _reports(current_game, states.pop(0), ReportState.COMPLETE)

    first = execute_prefetch_job(
        _job(game), cache=cache, fetch_reports=fetch, executed_at=RUN_AT
    )
    second = execute_prefetch_job(
        _job(game),
        cache=cache,
        fetch_reports=fetch,
        executed_at=datetime(2026, 9, 13, 15, 45, tzinfo=timezone.utc),
    )

    assert first.complete is False
    assert first.needs_retry is True
    assert first.game_results[0].snapshot is not None
    assert second.complete is True
    assert second.game_results[0].attempted is True
    assert not states


def test_complete_snapshot_skips_later_prefetch_attempt(tmp_path: Path) -> None:
    game = _game()
    cache = StatusCache(tmp_path)
    first = execute_prefetch_job(
        _job(game),
        cache=cache,
        fetch_reports=lambda current: _reports(
            current, ReportState.COMPLETE, ReportState.COMPLETE
        ),
        executed_at=RUN_AT,
    )

    later = execute_prefetch_job(
        _job(game),
        cache=cache,
        fetch_reports=lambda _game: pytest.fail("complete game should not refetch"),
        executed_at=datetime(2026, 9, 13, 15, 45, tzinfo=timezone.utc),
    )

    assert later.complete is True
    assert later.game_results[0].attempted is False
    assert later.game_results[0].snapshot == first.game_results[0].snapshot


def test_one_game_failure_does_not_prevent_other_game_from_caching(tmp_path: Path) -> None:
    broken = _game()
    healthy = _game("2026_01_PIT_CLE", "PIT", "CLE")
    cache = StatusCache(tmp_path)

    def fetch(game: RelevantGame):
        if game is broken:
            raise RuntimeError("temporary source outage")
        return _reports(game, ReportState.COMPLETE, ReportState.COMPLETE)

    execution = execute_prefetch_job(
        _job(broken, healthy), cache=cache, fetch_reports=fetch, executed_at=RUN_AT
    )

    assert execution.complete is False
    assert execution.needs_retry is True
    assert "temporary source outage" in (execution.game_results[0].error or "")
    assert cache.load_latest(broken.game_id) is None
    assert cache.load_latest(healthy.game_id) is not None


def test_prefetch_rejects_final_jobs_and_naive_execution_times(tmp_path: Path) -> None:
    with pytest.raises(PrefetchExecutionError, match="Only prefetch"):
        execute_prefetch_job(
            _job(_game(), kind=PlannedJobKind.FINAL),
            cache=StatusCache(tmp_path),
            fetch_reports=lambda game: (),
            executed_at=RUN_AT,
        )
    with pytest.raises(PrefetchExecutionError, match="timezone-aware"):
        execute_prefetch_job(
            _job(_game()),
            cache=StatusCache(tmp_path),
            fetch_reports=lambda game: (),
            executed_at=datetime(2026, 9, 13, 15, 25),
        )


def test_mismatched_report_is_not_cached(tmp_path: Path) -> None:
    game = _game()
    cache = StatusCache(tmp_path)
    other = _game("other")

    execution = execute_prefetch_job(
        _job(game),
        cache=cache,
        fetch_reports=lambda _game: _reports(
            other, ReportState.COMPLETE, ReportState.COMPLETE
        ),
        executed_at=RUN_AT,
    )

    assert execution.needs_retry is True
    assert "other game IDs" in (execution.game_results[0].error or "")
    assert cache.load_latest(game.game_id) is None
