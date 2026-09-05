"""Join owned fantasy skill players to one indexed depth-chart snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Sequence

from app.models import FantasyPlayer
from app.nfl.depth_chart import DepthChartRow, DepthChartSnapshot
from app.nfl.identity import CanonicalPlayer, normalize_position


SKILL_POSITIONS = frozenset({"QB", "RB", "WR", "TE"})


class DepthJoinMethod(str, Enum):
    GSIS_ID = "gsis_id"
    ESPN_ID = "espn_id"


class DepthJoinIssueState(str, Enum):
    UNRESOLVED_IDENTITY = "unresolved_identity"
    TEAM_DEFENSE = "team_defense"
    NON_SKILL_POSITION = "non_skill_position"
    NOT_IN_SNAPSHOT = "not_in_snapshot"
    AMBIGUOUS_DEPTH_ID = "ambiguous_depth_id"


@dataclass(frozen=True)
class DepthChartIndex:
    """Exact-ID indexes for the already-selected snapshot."""

    snapshot: DepthChartSnapshot
    by_gsis_id: Mapping[str, tuple[DepthChartRow, ...]]
    by_espn_id: Mapping[str, tuple[DepthChartRow, ...]]


@dataclass(frozen=True)
class OwnedDepthMatch:
    """One canonical NFL player and every fantasy instance that owns them."""

    canonical_player_id: str
    depth_row: DepthChartRow
    method: DepthJoinMethod
    fantasy_players: tuple[FantasyPlayer, ...]


@dataclass(frozen=True)
class DepthJoinIssue:
    state: DepthJoinIssueState
    fantasy_players: tuple[FantasyPlayer, ...]
    canonical_player_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class OwnedDepthJoinResult:
    index: DepthChartIndex
    matches: tuple[OwnedDepthMatch, ...]
    issues: tuple[DepthJoinIssue, ...]

    @property
    def matched_fantasy_instances(self) -> int:
        return sum(len(match.fantasy_players) for match in self.matches)


def build_depth_chart_index(snapshot: DepthChartSnapshot) -> DepthChartIndex:
    """Index the selected rows once without discarding duplicate-ID ambiguity."""

    by_gsis: dict[str, list[DepthChartRow]] = {}
    by_espn: dict[str, list[DepthChartRow]] = {}
    for row in snapshot.rows:
        if row.gsis_id:
            by_gsis.setdefault(row.gsis_id, []).append(row)
        if row.espn_id:
            by_espn.setdefault(row.espn_id, []).append(row)
    return DepthChartIndex(
        snapshot=snapshot,
        by_gsis_id=MappingProxyType(
            {player_id: tuple(rows) for player_id, rows in by_gsis.items()}
        ),
        by_espn_id=MappingProxyType(
            {player_id: tuple(rows) for player_id, rows in by_espn.items()}
        ),
    )


def join_owned_skill_players(
    players: Sequence[FantasyPlayer],
    snapshot: DepthChartSnapshot,
    *,
    identities: Sequence[CanonicalPlayer] = (),
) -> OwnedDepthJoinResult:
    """Join owned players by GSIS, then known ESPN ID, never by name."""

    index = build_depth_chart_index(snapshot)
    identities_by_gsis = {identity.gsis_id: identity for identity in identities}
    grouped: dict[str, list[FantasyPlayer]] = {}
    issues: list[DepthJoinIssue] = []

    for player in players:
        canonical_id = player.canonical_player_id
        position = normalize_position(player.position)
        if canonical_id is None:
            issues.append(
                DepthJoinIssue(
                    state=DepthJoinIssueState.UNRESOLVED_IDENTITY,
                    fantasy_players=(player,),
                    detail="player has no canonical GSIS identity",
                )
            )
        elif canonical_id.startswith("DST:") or position == "DST":
            issues.append(
                DepthJoinIssue(
                    state=DepthJoinIssueState.TEAM_DEFENSE,
                    fantasy_players=(player,),
                    canonical_player_id=canonical_id,
                    detail="team defenses are outside individual-player depth analysis",
                )
            )
        elif position not in SKILL_POSITIONS:
            issues.append(
                DepthJoinIssue(
                    state=DepthJoinIssueState.NON_SKILL_POSITION,
                    fantasy_players=(player,),
                    canonical_player_id=canonical_id,
                    detail=f"position {position or 'unknown'} is outside QB/RB/WR/TE",
                )
            )
        else:
            grouped.setdefault(canonical_id, []).append(player)

    matches: list[OwnedDepthMatch] = []
    for canonical_id, fantasy_instances in grouped.items():
        owned = tuple(fantasy_instances)
        gsis_rows = index.by_gsis_id.get(canonical_id, ())
        if len(gsis_rows) == 1:
            matches.append(
                OwnedDepthMatch(canonical_id, gsis_rows[0], DepthJoinMethod.GSIS_ID, owned)
            )
            continue
        if len(gsis_rows) > 1:
            issues.append(_ambiguous_issue(canonical_id, owned, "GSIS", canonical_id))
            continue

        espn_id = _known_espn_id(canonical_id, owned, identities_by_gsis)
        espn_rows = index.by_espn_id.get(espn_id, ()) if espn_id else ()
        if len(espn_rows) == 1:
            matches.append(
                OwnedDepthMatch(canonical_id, espn_rows[0], DepthJoinMethod.ESPN_ID, owned)
            )
        elif len(espn_rows) > 1:
            issues.append(_ambiguous_issue(canonical_id, owned, "ESPN", espn_id or ""))
        else:
            detail = f"GSIS {canonical_id} is absent from the selected depth snapshot"
            if espn_id:
                detail += f"; ESPN {espn_id} is also absent"
            issues.append(
                DepthJoinIssue(
                    state=DepthJoinIssueState.NOT_IN_SNAPSHOT,
                    fantasy_players=owned,
                    canonical_player_id=canonical_id,
                    detail=detail,
                )
            )

    return OwnedDepthJoinResult(index, tuple(matches), tuple(issues))


def render_owned_depth_join(result: OwnedDepthJoinResult) -> str:
    issue_counts = {
        state: sum(issue.state is state for issue in result.issues)
        for state in DepthJoinIssueState
    }
    total_instances = result.matched_fantasy_instances + sum(
        len(issue.fantasy_players) for issue in result.issues
    )
    lines = [
        f"Owned fantasy instances considered: {total_instances}",
        f"Canonical skill players matched: {len(result.matches)}",
        f"Matched fantasy instances: {result.matched_fantasy_instances}",
        "Join methods: "
        + ", ".join(
            f"{method.value}={sum(match.method is method for match in result.matches)}"
            for method in DepthJoinMethod
        ),
        "Join issues: "
        + (
            ", ".join(
                f"{state.value}={count}" for state, count in issue_counts.items() if count
            )
            or "none"
        ),
    ]
    for issue in result.issues:
        names = ", ".join(player.name for player in issue.fantasy_players)
        lines.append(f"  {issue.state.value}: {names} — {issue.detail}")
    return "\n".join(lines)


def _known_espn_id(
    canonical_id: str,
    fantasy_players: tuple[FantasyPlayer, ...],
    identities_by_gsis: Mapping[str, CanonicalPlayer],
) -> str | None:
    identity = identities_by_gsis.get(canonical_id)
    if identity and identity.espn_id:
        return identity.espn_id
    espn_ids = {
        player.platform_player_id
        for player in fantasy_players
        if player.platform.value == "espn" and player.platform_player_id
    }
    return next(iter(espn_ids)) if len(espn_ids) == 1 else None


def _ambiguous_issue(
    canonical_id: str,
    fantasy_players: tuple[FantasyPlayer, ...],
    provider: str,
    provider_id: str,
) -> DepthJoinIssue:
    return DepthJoinIssue(
        state=DepthJoinIssueState.AMBIGUOUS_DEPTH_ID,
        fantasy_players=fantasy_players,
        canonical_player_id=canonical_id,
        detail=f"multiple depth rows share {provider} ID {provider_id}",
    )
