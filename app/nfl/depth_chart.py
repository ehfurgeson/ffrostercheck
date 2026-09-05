"""Select the latest nflverse depth-chart snapshot at or before decision time."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

from app.nfl.identity import normalize_team
from app.nfl.nflverse import DatasetLoad, DatasetState, NFLVerseSource


class DepthSnapshotState(str, Enum):
    AVAILABLE = "available"
    STALE = "stale"
    MISSING = "missing"
    UNSUPPORTED_SEASON = "unsupported_season"


@dataclass(frozen=True)
class DepthChartRow:
    """One player row from a single timestamped depth snapshot."""

    snapshot_at: datetime
    team: str
    player_name: str
    espn_id: str | None
    gsis_id: str | None
    formation: str | None
    position: str | None
    position_slot: int | None
    source_rank: int | None


@dataclass(frozen=True)
class DepthChartSnapshot:
    """Latest snapshot at or before decision time, built once per run."""

    state: DepthSnapshotState
    season: int
    as_of: datetime
    max_age_hours: int
    snapshot_at: datetime | None = None
    rows: tuple[DepthChartRow, ...] = ()
    detail: str | None = None

    @property
    def has_rows(self) -> bool:
        return bool(self.rows)

    @property
    def teams(self) -> frozenset[str]:
        return frozenset(row.team for row in self.rows if row.team)

    @property
    def age_hours(self) -> float | None:
        if self.snapshot_at is None:
            return None
        return (self.as_of - self.snapshot_at).total_seconds() / 3600


def load_latest_depth_snapshot(
    season: int,
    *,
    as_of: datetime,
    max_age_hours: int = 30,
    nflverse: NFLVerseSource | None = None,
    dataset: DatasetLoad | None = None,
    rows: Sequence[Mapping[str, Any]] | None = None,
) -> DepthChartSnapshot:
    """Load depth charts once and keep only the latest usable snapshot.

    Missing, unsupported, or stale snapshots are returned as source limitations.
    Schema-contract failures still raise from the nflverse adapter.
    """

    _require_aware(as_of)
    if max_age_hours <= 0:
        raise ValueError("max_age_hours must be greater than 0")

    if rows is not None:
        return _select_from_rows(rows, season=season, as_of=as_of, max_age_hours=max_age_hours)

    loaded = dataset
    if loaded is None:
        loaded = (nflverse or NFLVerseSource()).load_depth_charts(season)
    if not loaded.available or loaded.frame is None:
        return DepthChartSnapshot(
            state=DepthSnapshotState.UNSUPPORTED_SEASON,
            season=season,
            as_of=as_of,
            max_age_hours=max_age_hours,
            detail=loaded.detail or f"nflverse depth charts are unavailable for season {season}",
        )
    return _select_from_rows(
        loaded.frame.iter_rows(named=True),
        season=season,
        as_of=as_of,
        max_age_hours=max_age_hours,
    )


def render_depth_snapshot(snapshot: DepthChartSnapshot) -> str:
    lines = [
        f"Depth snapshot: {snapshot.state.value}",
        f"Season: {snapshot.season}",
        f"Decision time: {snapshot.as_of.isoformat()}",
    ]
    if snapshot.snapshot_at is not None:
        lines.append(f"Snapshot at: {snapshot.snapshot_at.isoformat()}")
        age = snapshot.age_hours if snapshot.age_hours is not None else 0.0
        lines.append(f"Age: {age:.1f} hours (limit {snapshot.max_age_hours})")
    lines.append(f"Teams: {len(snapshot.teams)}")
    lines.append(f"Rows: {len(snapshot.rows)}")
    if snapshot.detail:
        lines.append(f"  limitation: {snapshot.detail}")
    return "\n".join(lines)


def _select_from_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    season: int,
    as_of: datetime,
    max_age_hours: int,
) -> DepthChartSnapshot:
    latest_at: datetime | None = None
    selected: list[DepthChartRow] = []
    for raw in rows:
        parsed = _parse_row(raw)
        if parsed is None or parsed.snapshot_at > as_of:
            continue
        if latest_at is None or parsed.snapshot_at > latest_at:
            latest_at = parsed.snapshot_at
            selected = [parsed]
        elif parsed.snapshot_at == latest_at:
            selected.append(parsed)

    if latest_at is None:
        return DepthChartSnapshot(
            state=DepthSnapshotState.MISSING,
            season=season,
            as_of=as_of,
            max_age_hours=max_age_hours,
            detail=f"no depth-chart snapshot at or before {as_of.isoformat()}",
        )

    age_hours = (as_of - latest_at).total_seconds() / 3600
    stale = age_hours >= max_age_hours
    return DepthChartSnapshot(
        state=DepthSnapshotState.STALE if stale else DepthSnapshotState.AVAILABLE,
        season=season,
        as_of=as_of,
        max_age_hours=max_age_hours,
        snapshot_at=latest_at,
        rows=tuple(selected),
        detail=(
            f"latest snapshot is {age_hours:.1f} hours old; max age is {max_age_hours} hours"
            if stale
            else None
        ),
    )


def _parse_row(row: Mapping[str, Any]) -> DepthChartRow | None:
    snapshot_at = _parse_snapshot_at(row.get("dt"))
    if snapshot_at is None:
        return None
    team = normalize_team(_optional_text(row.get("team")) or "")
    return DepthChartRow(
        snapshot_at=snapshot_at,
        team=team or "",
        player_name=_optional_text(row.get("player_name")) or "Unknown player",
        espn_id=_optional_id(row.get("espn_id")),
        gsis_id=_optional_id(row.get("gsis_id")),
        formation=_optional_text(row.get("pos_grp")),
        position=_optional_text(row.get("pos_abb")),
        position_slot=_as_int(row.get("pos_slot")),
        source_rank=_as_int(row.get("pos_rank")),
    )


def _parse_snapshot_at(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _optional_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value).strip() or None


def _optional_id(value: Any) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    if text.endswith(".0"):
        whole = text[:-2]
        if whole.isdigit():
            return whole
    return text


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
