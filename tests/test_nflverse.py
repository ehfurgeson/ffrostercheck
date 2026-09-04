import json
from pathlib import Path
from typing import Callable

import polars as pl
import pytest

from app.nfl.nflverse import (
    DatasetState,
    NFLVerseLoadError,
    NFLVerseSchemaError,
    NFLVerseSource,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "nflverse"


def _frame(name: str) -> pl.DataFrame:
    rows = json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return pl.DataFrame(rows)


class FixtureLoader:
    def __init__(
        self,
        *,
        schedules: Callable[[], pl.DataFrame] | None = None,
        rosters: Callable[[], pl.DataFrame] | None = None,
    ) -> None:
        self._schedules = schedules or (lambda: _frame("schedules"))
        self._rosters = rosters or (lambda: _frame("rosters"))
        self.calls: list[tuple[str, int | None]] = []

    def load_schedules(self, season: int) -> pl.DataFrame:
        self.calls.append(("schedules", season))
        return self._schedules()

    def load_players(self) -> pl.DataFrame:
        self.calls.append(("players", None))
        return _frame("players")

    def load_rosters(self, season: int) -> pl.DataFrame:
        self.calls.append(("rosters", season))
        return self._rosters()

    def load_ff_playerids(self) -> pl.DataFrame:
        self.calls.append(("fantasy_player_ids", None))
        return _frame("fantasy_player_ids")


def test_core_datasets_follow_the_offline_source_contracts() -> None:
    loader = FixtureLoader()

    snapshot = NFLVerseSource(loader).load_snapshot(2026)

    assert snapshot.unavailable_datasets == ()
    assert snapshot.schedules.frame is not None
    assert snapshot.schedules.frame.height == 1
    assert snapshot.players.available
    assert snapshot.rosters.available
    assert snapshot.fantasy_player_ids.available
    assert loader.calls == [
        ("schedules", 2026),
        ("players", None),
        ("rosters", 2026),
        ("fantasy_player_ids", None),
    ]


def test_unsupported_roster_season_does_not_block_other_datasets() -> None:
    def unsupported_rosters() -> pl.DataFrame:
        raise ValueError("Season must be between 1920 and 2025")

    loader = FixtureLoader(rosters=unsupported_rosters)

    snapshot = NFLVerseSource(loader).load_snapshot(2026)

    assert snapshot.rosters.state is DatasetState.UNSUPPORTED_SEASON
    assert snapshot.rosters.frame is None
    assert snapshot.unavailable_datasets == ("rosters",)
    assert snapshot.schedules.available
    assert snapshot.players.available
    assert snapshot.fantasy_player_ids.available
    assert loader.calls[-1] == ("fantasy_player_ids", None)


def test_empty_seasonal_dataset_is_explicitly_unavailable() -> None:
    empty_schedules = lambda: _frame("schedules").clear()

    snapshot = NFLVerseSource(FixtureLoader(schedules=empty_schedules)).load_snapshot(2027)

    assert snapshot.schedules.state is DatasetState.UNSUPPORTED_SEASON
    assert snapshot.schedules.detail == "No schedules rows are available for season 2027"
    assert snapshot.players.available


def test_missing_required_column_fails_the_source_contract() -> None:
    incomplete_schedules = lambda: _frame("schedules").drop("game_id")

    with pytest.raises(NFLVerseSchemaError, match="schedules.*game_id"):
        NFLVerseSource(FixtureLoader(schedules=incomplete_schedules)).load_snapshot(2026)


def test_parse_error_is_not_mislabeled_as_an_unsupported_season() -> None:
    def invalid_schedules() -> pl.DataFrame:
        raise ValueError("Failed to parse data")

    with pytest.raises(NFLVerseLoadError, match="Unable to load nflverse schedules"):
        NFLVerseSource(FixtureLoader(schedules=invalid_schedules)).load_snapshot(2026)
