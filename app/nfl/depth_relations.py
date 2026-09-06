"""Build ordered same-slot depth chains and relate owned players to blockers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from app.nfl.depth_chart import DepthChartRow, DepthChartSnapshot
from app.nfl.depth_join import OwnedDepthJoinResult
from app.nfl.identity import normalize_position, normalize_team


@dataclass(frozen=True, order=True)
class DepthSlotKey:
    """A depth slot scoped to one team, formation, and position."""

    team: str
    formation: str
    position: str
    position_slot: int


@dataclass(frozen=True)
class DepthSlotChain:
    """Rows in one depth slot, ordered by their possibly non-contiguous rank."""

    key: DepthSlotKey
    players: tuple[DepthChartRow, ...]


@dataclass(frozen=True)
class DepthBlocker:
    """One player ahead of an owned player in the exact same depth slot."""

    depth_row: DepthChartRow
    canonical_player_id: str | None


@dataclass(frozen=True)
class DepthRelation:
    """The source role and same-slot blockers for one owned NFL player."""

    canonical_player_id: str
    team: str
    position: str
    formation: str
    position_slot: int
    source_rank: int
    players_ahead: tuple[DepthBlocker, ...]
    snapshot_at: datetime

    @property
    def resolved_player_ids_ahead(self) -> tuple[str, ...]:
        return tuple(
            blocker.canonical_player_id
            for blocker in self.players_ahead
            if blocker.canonical_player_id is not None
        )


@dataclass(frozen=True)
class DepthChainIssue:
    """A row that cannot participate in a safely scoped depth relation."""

    depth_row: DepthChartRow
    detail: str


@dataclass(frozen=True)
class OwnedDepthRelations:
    """All snapshot chains plus relations for successfully joined owned players."""

    snapshot: DepthChartSnapshot
    chains: Mapping[DepthSlotKey, DepthSlotChain]
    relations: Mapping[str, DepthRelation]
    issues: tuple[DepthChainIssue, ...]


def build_owned_depth_relations(joined: OwnedDepthJoinResult) -> OwnedDepthRelations:
    """Group the snapshot and connect each matched owned player to lower-rank blockers."""

    chains, issues = _build_chains(joined.index.snapshot)
    relations: dict[str, DepthRelation] = {}
    for match in joined.matches:
        key, detail = _slot_key(match.depth_row)
        if key is None:
            continue
        chain = chains[key]
        source_rank = match.depth_row.source_rank
        if source_rank is None:
            # _slot_key already rejects this; the guard keeps the type narrowing explicit.
            continue
        blockers = tuple(
            DepthBlocker(row, row.gsis_id)
            for row in chain.players
            if row.source_rank is not None and row.source_rank < source_rank
        )
        relations[match.canonical_player_id] = DepthRelation(
            canonical_player_id=match.canonical_player_id,
            team=key.team,
            position=key.position,
            formation=key.formation,
            position_slot=key.position_slot,
            source_rank=source_rank,
            players_ahead=blockers,
            snapshot_at=match.depth_row.snapshot_at,
        )
    return OwnedDepthRelations(
        snapshot=joined.index.snapshot,
        chains=MappingProxyType(chains),
        relations=MappingProxyType(relations),
        issues=tuple(issues),
    )


def render_owned_depth_relations(result: OwnedDepthRelations) -> str:
    lines = [
        f"Same-slot depth chains: {len(result.chains)}",
        f"Owned depth relations: {len(result.relations)}",
        f"Excluded incomplete rows: {len(result.issues)}",
    ]
    for relation in result.relations.values():
        blockers = ", ".join(
            f"{blocker.depth_row.player_name} ({blocker.canonical_player_id or 'unresolved GSIS'})"
            for blocker in relation.players_ahead
        )
        lines.append(
            f"  {relation.canonical_player_id}: {relation.team} {relation.formation} "
            f"{relation.position} slot {relation.position_slot}, rank {relation.source_rank}; "
            f"ahead: {blockers or 'none'}"
        )
    for issue in result.issues:
        lines.append(f"  excluded: {issue.depth_row.player_name} — {issue.detail}")
    return "\n".join(lines)


def _build_chains(
    snapshot: DepthChartSnapshot,
) -> tuple[dict[DepthSlotKey, DepthSlotChain], list[DepthChainIssue]]:
    grouped: dict[DepthSlotKey, list[DepthChartRow]] = {}
    issues: list[DepthChainIssue] = []
    for row in snapshot.rows:
        key, detail = _slot_key(row)
        if key is None:
            issues.append(DepthChainIssue(row, detail or "incomplete depth slot"))
            continue
        grouped.setdefault(key, []).append(row)

    chains = {
        key: DepthSlotChain(
            key=key,
            players=tuple(sorted(rows, key=_row_order)),
        )
        for key, rows in sorted(grouped.items())
    }
    return chains, issues


def _slot_key(row: DepthChartRow) -> tuple[DepthSlotKey | None, str | None]:
    team = normalize_team(row.team)
    formation = _normalize_formation(row.formation)
    position = normalize_position(row.position)
    missing = [
        label
        for label, value in (
            ("team", team),
            ("formation", formation),
            ("position", position),
            ("position_slot", row.position_slot),
            ("source_rank", row.source_rank),
        )
        if value is None or value == ""
    ]
    if missing:
        return None, f"missing required chain fields: {', '.join(missing)}"
    return (
        DepthSlotKey(
            team=team or "",
            formation=formation or "",
            position=position or "",
            position_slot=row.position_slot or 0,
        ),
        None,
    )


def _normalize_formation(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split()).upper()
    return normalized or None


def _row_order(row: DepthChartRow) -> tuple[int, str, str, str]:
    return (
        row.source_rank if row.source_rank is not None else 0,
        row.player_name.casefold(),
        row.gsis_id or "",
        row.espn_id or "",
    )
