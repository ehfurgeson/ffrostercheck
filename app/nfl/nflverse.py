"""Validated access to the nflverse datasets used by Fantasy Watchdog."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

import nflreadpy as nfl


SCHEDULE_COLUMNS = frozenset(
    {
        "game_id",
        "season",
        "game_type",
        "week",
        "gameday",
        "gametime",
        "away_team",
        "home_team",
    }
)
PLAYER_COLUMNS = frozenset(
    {
        "gsis_id",
        "display_name",
        "position",
        "latest_team",
        "espn_id",
    }
)
ROSTER_COLUMNS = frozenset(
    {
        "season",
        "team",
        "position",
        "full_name",
        "gsis_id",
        "espn_id",
        "sleeper_id",
    }
)
FF_PLAYER_ID_COLUMNS = frozenset(
    {
        "gsis_id",
        "name",
        "position",
        "team",
        "espn_id",
        "sleeper_id",
    }
)


class DataFrameLike(Protocol):
    """The small part of the Polars DataFrame interface this adapter needs."""

    columns: list[str]
    height: int


class NFLReadPyLike(Protocol):
    """Injectable nflreadpy surface used by offline tests."""

    def load_schedules(self, seasons: int) -> DataFrameLike: ...

    def load_players(self) -> DataFrameLike: ...

    def load_rosters(self, seasons: int) -> DataFrameLike: ...

    def load_ff_playerids(self) -> DataFrameLike: ...


class DatasetState(str, Enum):
    AVAILABLE = "available"
    UNSUPPORTED_SEASON = "unsupported_season"


@dataclass(frozen=True)
class DatasetLoad:
    """One validated dataset or an explicit season-availability result."""

    name: str
    state: DatasetState
    frame: DataFrameLike | None
    season: int | None = None
    detail: str | None = None

    @property
    def available(self) -> bool:
        return self.state is DatasetState.AVAILABLE


@dataclass(frozen=True)
class NFLVerseSnapshot:
    """The independently loaded nflverse inputs needed by Phase 2."""

    schedules: DatasetLoad
    players: DatasetLoad
    rosters: DatasetLoad
    fantasy_player_ids: DatasetLoad

    @property
    def unavailable_datasets(self) -> tuple[str, ...]:
        return tuple(
            dataset.name
            for dataset in (
                self.schedules,
                self.players,
                self.rosters,
                self.fantasy_player_ids,
            )
            if not dataset.available
        )


class NFLVerseLoadError(RuntimeError):
    """Raised when nflreadpy fails for a reason other than season availability."""


class NFLVerseSchemaError(NFLVerseLoadError):
    """Raised when an upstream frame no longer satisfies its data contract."""


class NFLVerseSource:
    """Load and validate nflreadpy data without leaking its API downstream."""

    def __init__(self, loader: NFLReadPyLike = nfl) -> None:
        self._loader = loader

    def load_snapshot(self, season: int) -> NFLVerseSnapshot:
        """Load each core dataset, isolating unsupported-season results."""

        return NFLVerseSnapshot(
            schedules=self._load(
                "schedules",
                lambda: self._loader.load_schedules(season),
                SCHEDULE_COLUMNS,
                season=season,
            ),
            players=self._load("players", self._loader.load_players, PLAYER_COLUMNS),
            rosters=self._load(
                "rosters",
                lambda: self._loader.load_rosters(season),
                ROSTER_COLUMNS,
                season=season,
            ),
            fantasy_player_ids=self._load(
                "fantasy_player_ids",
                self._loader.load_ff_playerids,
                FF_PLAYER_ID_COLUMNS,
            ),
        )

    @staticmethod
    def _load(
        name: str,
        load: Callable[[], DataFrameLike],
        required_columns: frozenset[str],
        *,
        season: int | None = None,
    ) -> DatasetLoad:
        try:
            frame = load()
        except ValueError as exc:
            if season is not None and _is_unsupported_season(exc):
                return DatasetLoad(
                    name=name,
                    state=DatasetState.UNSUPPORTED_SEASON,
                    frame=None,
                    season=season,
                    detail=str(exc),
                )
            raise NFLVerseLoadError(f"Unable to load nflverse {name}: {exc}") from exc
        except Exception as exc:
            raise NFLVerseLoadError(f"Unable to load nflverse {name}: {exc}") from exc

        columns = getattr(frame, "columns", None)
        height = getattr(frame, "height", None)
        if not isinstance(columns, list) or not isinstance(height, int):
            raise NFLVerseSchemaError(f"nflverse {name} did not return a Polars-like frame")
        if season is not None and height == 0:
            return DatasetLoad(
                name=name,
                state=DatasetState.UNSUPPORTED_SEASON,
                frame=None,
                season=season,
                detail=f"No {name} rows are available for season {season}",
            )

        missing = sorted(required_columns.difference(columns))
        if missing:
            raise NFLVerseSchemaError(
                f"nflverse {name} is missing required columns: {', '.join(missing)}"
            )
        return DatasetLoad(
            name=name,
            state=DatasetState.AVAILABLE,
            frame=frame,
            season=season,
        )


def _is_unsupported_season(exc: ValueError) -> bool:
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            "season must be between",
            "season is not available",
            "season not available",
            "unsupported season",
        )
    )
