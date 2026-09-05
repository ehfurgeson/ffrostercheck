import json
from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl
import pytest

from app.nfl.depth_chart import (
    DepthSnapshotState,
    load_latest_depth_snapshot,
    render_depth_snapshot,
)
from app.nfl.nflverse import DatasetLoad, DatasetState, NFLVerseSchemaError, NFLVerseSource


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "depth_charts" / "snapshots.json"
DECISION_AT = datetime(2026, 9, 4, 18, 0, tzinfo=timezone.utc)


def _rows() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class _DepthChartLoader:
    def __init__(self, load_depth_charts) -> None:
        self._load_depth_charts = load_depth_charts

    def load_depth_charts(self, seasons: int) -> pl.DataFrame:
        return self._load_depth_charts()


def test_latest_snapshot_at_or_before_decision_time_is_selected_once() -> None:
    snapshot = load_latest_depth_snapshot(
        2026,
        as_of=DECISION_AT,
        rows=_rows(),
    )

    assert snapshot.state is DepthSnapshotState.AVAILABLE
    assert snapshot.snapshot_at == datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    assert snapshot.teams == frozenset({"CHI", "NE"})
    assert {row.player_name for row in snapshot.rows} == {
        "Rome Odunze",
        "Zavion Thomas",
        "Jahdae Walker",
        "Rhamondre Stevenson",
        "TreVeyon Henderson",
    }
    assert all(row.snapshot_at == snapshot.snapshot_at for row in snapshot.rows)
    walker = next(row for row in snapshot.rows if row.player_name == "Jahdae Walker")
    assert walker.position_slot == 2
    assert walker.source_rank == 5


def test_future_and_older_snapshots_are_not_scanned_into_the_index() -> None:
    snapshot = load_latest_depth_snapshot(
        2026,
        as_of=DECISION_AT,
        rows=_rows(),
    )

    names = {row.player_name for row in snapshot.rows}
    assert "Future Snapshot" not in names
    assert {row.snapshot_at for row in snapshot.rows} == {
        datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    }


def test_date_only_timestamps_are_usable_when_they_are_the_latest() -> None:
    snapshot = load_latest_depth_snapshot(
        2026,
        as_of=datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc),
        rows=_rows(),
    )

    assert snapshot.state is DepthSnapshotState.AVAILABLE
    assert snapshot.snapshot_at == datetime(2026, 9, 3, tzinfo=timezone.utc)
    assert [row.player_name for row in snapshot.rows] == ["Rhamondre Stevenson"]


def test_missing_snapshot_is_a_source_limitation() -> None:
    snapshot = load_latest_depth_snapshot(
        2026,
        as_of=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        rows=_rows(),
    )

    assert snapshot.state is DepthSnapshotState.MISSING
    assert snapshot.rows == ()
    assert snapshot.snapshot_at is None
    assert "no depth-chart snapshot at or before" in (snapshot.detail or "")


def test_stale_snapshot_keeps_rows_and_discloses_the_age_limit() -> None:
    snapshot = load_latest_depth_snapshot(
        2026,
        as_of=datetime(2026, 9, 7, 18, 0, tzinfo=timezone.utc),
        max_age_hours=30,
        rows=_rows(),
    )

    assert snapshot.state is DepthSnapshotState.STALE
    assert snapshot.has_rows
    assert snapshot.snapshot_at == datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
    assert snapshot.age_hours == 30.0
    rendered = render_depth_snapshot(snapshot)
    assert "Depth snapshot: stale" in rendered
    assert "max age is 30 hours" in rendered


def test_snapshot_just_inside_max_age_stays_available() -> None:
    snapshot = load_latest_depth_snapshot(
        2026,
        as_of=datetime(2026, 9, 7, 17, 59, 59, tzinfo=timezone.utc),
        max_age_hours=30,
        rows=_rows(),
    )

    assert snapshot.state is DepthSnapshotState.AVAILABLE
    assert snapshot.snapshot_at == datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def test_unsupported_season_is_a_source_limitation() -> None:
    dataset = DatasetLoad(
        name="depth_charts",
        state=DatasetState.UNSUPPORTED_SEASON,
        frame=None,
        season=2026,
        detail="Season must be between 2001 and 2025",
    )
    snapshot = load_latest_depth_snapshot(
        2026,
        as_of=DECISION_AT,
        dataset=dataset,
    )

    assert snapshot.state is DepthSnapshotState.UNSUPPORTED_SEASON
    assert snapshot.rows == ()
    assert "Season must be between 2001 and 2025" in (snapshot.detail or "")


def test_download_failure_is_unsupported_not_an_application_error() -> None:
    def missing() -> None:
        raise ConnectionError("Failed to download depth_charts_2026.parquet: 404")

    snapshot = load_latest_depth_snapshot(
        2026,
        as_of=DECISION_AT,
        dataset=NFLVerseSource(_DepthChartLoader(missing)).load_depth_charts(2026),
    )

    assert snapshot.state is DepthSnapshotState.UNSUPPORTED_SEASON
    assert "404" in (snapshot.detail or "")


def test_schema_failure_still_raises() -> None:
    def incomplete() -> pl.DataFrame:
        return pl.DataFrame(_rows()).drop("dt")

    with pytest.raises(NFLVerseSchemaError, match="depth_charts.*dt"):
        NFLVerseSource(_DepthChartLoader(incomplete)).load_depth_charts(2026)


def test_naive_as_of_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        load_latest_depth_snapshot(2026, as_of=datetime(2026, 9, 4, 18, 0), rows=_rows())


def test_polars_date_values_are_accepted() -> None:
    snapshot = load_latest_depth_snapshot(
        2026,
        as_of=datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc),
        rows=[
            {
                "dt": date(2026, 9, 3),
                "team": "NE",
                "player_name": "Rhamondre Stevenson",
                "espn_id": 4241457,
                "gsis_id": "00-0036360",
                "pos_grp": "Offense",
                "pos_abb": "RB",
                "pos_slot": 1.0,
                "pos_rank": 1.0,
            }
        ],
    )

    assert snapshot.state is DepthSnapshotState.AVAILABLE
    assert snapshot.rows[0].espn_id == "4241457"
    assert snapshot.rows[0].position_slot == 1


def test_empty_rows_are_missing_not_an_application_error() -> None:
    snapshot = load_latest_depth_snapshot(2026, as_of=DECISION_AT, rows=[])

    assert snapshot.state is DepthSnapshotState.MISSING
    assert snapshot.rows == ()
