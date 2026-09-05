from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.analysis.availability import StatusSubject, combine_official_statuses
from app.models import (
    Confidence,
    GameDayState,
    GameSourceReport,
    InjuryDesignation,
    ReportState,
    RosterEligibility,
    SourceResult,
)
from app.storage.cache import (
    StatusCache,
    StatusCacheError,
    StatusFreshness,
    render_cached_snapshot,
    render_status_resolution,
)


PREFETCH_AT = datetime(2026, 1, 4, 16, 30, tzinfo=timezone.utc)
DECISION_AT = datetime(2026, 1, 4, 17, 55, tzinfo=timezone.utc)
GAME_ID = "2025_18_TEN_JAX"


def _subject() -> StatusSubject:
    return StatusSubject(
        canonical_player_id="00-001",
        name="Amani Hooker",
        nfl_team="TEN",
        position="S",
        roster_eligibility=RosterEligibility.ELIGIBLE,
    )


def _result(
    *,
    retrieved_at: datetime = PREFETCH_AT,
    published_at: datetime | None = None,
    source_updated_at: datetime | None = None,
    http_cache_age_seconds: int | None = 12,
    game_day_state: GameDayState | None = GameDayState.INACTIVE,
) -> SourceResult:
    return SourceResult(
        source="nfl_inactives",
        source_url="https://www.nfl.com/news/week-18-inactives",
        success=True,
        report_state=ReportState.COMPLETE,
        retrieved_at=retrieved_at,
        game_day_state=game_day_state,
        detail="Officially inactive",
        player_name="Amani Hooker",
        nfl_team="TEN",
        position="S",
        published_at=published_at or datetime(2026, 1, 4, 16, 20, tzinfo=timezone.utc),
        source_updated_at=source_updated_at
        or datetime(2026, 1, 4, 16, 25, tzinfo=timezone.utc),
        http_cache_age_seconds=http_cache_age_seconds,
        raw_content_hash="abc123",
    )


def _report(
    *results: SourceResult,
    report_state: ReportState = ReportState.COMPLETE,
    retrieved_at: datetime = PREFETCH_AT,
    errors: tuple[str, ...] = (),
) -> GameSourceReport:
    return GameSourceReport(
        source="nfl_inactives",
        game_id=GAME_ID,
        report_state=report_state,
        expected_teams=frozenset({"TEN", "JAX"}),
        parsed_teams=frozenset({"TEN", "JAX"}) if report_state is ReportState.COMPLETE else frozenset(),
        player_results=results,
        retrieved_at=retrieved_at,
        source_updated_at=datetime(2026, 1, 4, 16, 25, tzinfo=timezone.utc),
        errors=errors,
        source_url="https://www.nfl.com/news/week-18-inactives",
        published_at=datetime(2026, 1, 4, 16, 20, tzinfo=timezone.utc),
        http_cache_age_seconds=12,
        raw_content_hash="abc123",
    )


def _failed_report() -> GameSourceReport:
    return GameSourceReport(
        source="nfl_inactives",
        game_id=GAME_ID,
        report_state=ReportState.FAILED,
        expected_teams=frozenset({"TEN", "JAX"}),
        parsed_teams=frozenset(),
        player_results=(),
        retrieved_at=DECISION_AT,
        errors=("landing 503",),
        source_url="https://www.nfl.com/inactives/",
    )


def _save_prefetch(cache: StatusCache, report: GameSourceReport | None = None):
    report = report or _report(_result())
    statuses = combine_official_statuses((_subject(),), (report,), decision_at=PREFETCH_AT)
    return cache.save_prefetch(
        game_id=GAME_ID,
        reports=(report,),
        statuses=statuses,
        cached_at=PREFETCH_AT,
    )


def test_prefetch_round_trip_keeps_source_timestamps_separate_from_decision_time(tmp_path: Path) -> None:
    cache = StatusCache(tmp_path)
    snapshot = _save_prefetch(cache)

    loaded = cache.load_latest(GAME_ID)
    assert loaded is not None
    assert loaded.origin_fresh is False
    assert loaded.cached_at == PREFETCH_AT
    report = loaded.reports[0]
    status = loaded.statuses[0]
    assert report.retrieved_at == PREFETCH_AT
    assert report.published_at == datetime(2026, 1, 4, 16, 20, tzinfo=timezone.utc)
    assert report.source_updated_at == datetime(2026, 1, 4, 16, 25, tzinfo=timezone.utc)
    assert report.http_cache_age_seconds == 12
    assert status.decision_at == PREFETCH_AT
    assert status.decision_at != report.published_at
    assert status.game_day_state is GameDayState.INACTIVE
    assert loaded.reports == snapshot.reports
    path = tmp_path / "status" / GAME_ID / "20260104T163000Z.json"
    assert path.is_file()
    rendered = render_cached_snapshot(loaded, path=path)
    assert "Origin fresh: no" in rendered
    assert "retrieved=2026-01-04T16:30:00Z" in rendered


def test_cached_complete_report_is_never_origin_fresh(tmp_path: Path) -> None:
    cache = StatusCache(tmp_path)
    _save_prefetch(cache)

    loaded = cache.load_latest(GAME_ID)
    assert loaded is not None
    assert loaded.reports[0].report_state is ReportState.COMPLETE
    assert loaded.origin_fresh is False


def test_final_lookup_without_refresh_is_rejected_even_when_cache_is_complete(tmp_path: Path) -> None:
    cache = StatusCache(tmp_path)
    _save_prefetch(cache)

    with pytest.raises(StatusCacheError, match="not origin-fresh"):
        cache.resolve_final(
            game_id=GAME_ID,
            fetch_reports=lambda: (_report(_result()),),
            subjects=(_subject(),),
            decision_at=DECISION_AT,
            require_refresh=False,
        )


def test_successful_final_refresh_is_origin_fresh_and_ignores_cache(tmp_path: Path) -> None:
    cache = StatusCache(tmp_path)
    _save_prefetch(cache)
    live = _report(
        retrieved_at=DECISION_AT,
        report_state=ReportState.COMPLETE,
    )

    resolution = cache.resolve_final(
        game_id=GAME_ID,
        fetch_reports=lambda: (live,),
        subjects=(_subject(),),
        decision_at=DECISION_AT,
    )

    assert resolution.origin_fresh is True
    assert resolution.freshness is StatusFreshness.ORIGIN_FRESH
    assert resolution.used_cache is False
    assert resolution.refresh_attempted is True
    assert resolution.reports[0].retrieved_at == DECISION_AT
    assert resolution.statuses[0].decision_at == DECISION_AT
    assert resolution.statuses[0].game_day_state is GameDayState.ACTIVE
    assert resolution.cache_age_seconds is None


def test_failed_final_refresh_uses_cache_without_calling_it_origin_fresh(tmp_path: Path) -> None:
    cache = StatusCache(tmp_path)
    _save_prefetch(cache)

    resolution = cache.resolve_final(
        game_id=GAME_ID,
        fetch_reports=lambda: (_failed_report(),),
        subjects=(_subject(),),
        decision_at=DECISION_AT,
    )

    assert resolution.origin_fresh is False
    assert resolution.freshness is StatusFreshness.CACHED_AFTER_FAILED_REFRESH
    assert resolution.used_cache is True
    assert resolution.refresh_attempted is True
    assert resolution.cache_age_seconds == int((DECISION_AT - PREFETCH_AT).total_seconds())
    status = resolution.statuses[0]
    report = resolution.reports[0]
    assert status.decision_at == DECISION_AT
    assert report.retrieved_at == PREFETCH_AT
    assert report.published_at == datetime(2026, 1, 4, 16, 20, tzinfo=timezone.utc)
    assert report.http_cache_age_seconds == 12
    assert status.game_day_state is GameDayState.INACTIVE
    assert status.confidence is Confidence.OFFICIAL
    rendered = render_status_resolution(resolution)
    assert "Origin fresh: no" in rendered
    assert "Local cache age: 5100 seconds" in rendered
    assert "not origin-fresh" in rendered


def test_failed_refresh_exception_falls_back_to_cache(tmp_path: Path) -> None:
    cache = StatusCache(tmp_path)
    _save_prefetch(cache)

    def boom() -> tuple[GameSourceReport, ...]:
        raise TimeoutError("nfl.com timed out")

    resolution = cache.resolve_final(
        game_id=GAME_ID,
        fetch_reports=boom,
        subjects=(_subject(),),
        decision_at=DECISION_AT,
    )

    assert resolution.used_cache is True
    assert resolution.origin_fresh is False
    assert "TimeoutError" in " ".join(resolution.errors)


def test_failed_refresh_without_cache_keeps_live_failure(tmp_path: Path) -> None:
    cache = StatusCache(tmp_path)
    live = _failed_report()

    resolution = cache.resolve_final(
        game_id=GAME_ID,
        fetch_reports=lambda: (live,),
        subjects=(_subject(),),
        decision_at=DECISION_AT,
    )

    assert resolution.origin_fresh is True
    assert resolution.used_cache is False
    assert resolution.reports[0].report_state is ReportState.FAILED
    assert resolution.statuses[0].game_day_state is GameDayState.UNKNOWN


def test_failed_refresh_exception_without_cache_is_explicit(tmp_path: Path) -> None:
    cache = StatusCache(tmp_path)

    def boom() -> tuple[GameSourceReport, ...]:
        raise TimeoutError("offline")

    with pytest.raises(StatusCacheError, match="no cached snapshot exists"):
        cache.resolve_final(
            game_id=GAME_ID,
            fetch_reports=boom,
            subjects=(_subject(),),
            decision_at=DECISION_AT,
        )


def test_latest_snapshot_is_selected_by_cached_at(tmp_path: Path) -> None:
    cache = StatusCache(tmp_path)
    first = _report(_result(), retrieved_at=PREFETCH_AT)
    later = _report(_result(retrieved_at=PREFETCH_AT + timedelta(minutes=20)), retrieved_at=PREFETCH_AT + timedelta(minutes=20))
    cache.save_prefetch(
        game_id=GAME_ID,
        reports=(first,),
        statuses=combine_official_statuses((_subject(),), (first,), decision_at=PREFETCH_AT),
        cached_at=PREFETCH_AT,
    )
    cache.save_prefetch(
        game_id=GAME_ID,
        reports=(later,),
        statuses=combine_official_statuses(
            (_subject(),), (later,), decision_at=PREFETCH_AT + timedelta(minutes=20)
        ),
        cached_at=PREFETCH_AT + timedelta(minutes=20),
    )

    loaded = cache.load_latest(GAME_ID)
    assert loaded is not None
    assert loaded.cached_at == PREFETCH_AT + timedelta(minutes=20)
    assert loaded.reports[0].retrieved_at == PREFETCH_AT + timedelta(minutes=20)


def test_naive_timestamps_are_rejected(tmp_path: Path) -> None:
    cache = StatusCache(tmp_path)
    report = _report(_result())
    statuses = combine_official_statuses((_subject(),), (report,), decision_at=PREFETCH_AT)

    with pytest.raises(StatusCacheError, match="timezone-aware"):
        cache.save_prefetch(
            game_id=GAME_ID,
            reports=(report,),
            statuses=statuses,
            cached_at=datetime(2026, 1, 4, 16, 30),
        )
