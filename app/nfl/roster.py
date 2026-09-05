"""Apply canonical NFL identities and current teams to fantasy rosters."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Sequence

from app.models import FantasyPlayer, FantasyRoster
from app.nfl.identity import (
    CanonicalTeamSource,
    IdentityResolution,
    PlayerIdentityResolver,
    ResolutionMethod,
    normalize_team,
)


class TeamAssignmentSource(str, Enum):
    CURRENT_ROSTER = "current_roster"
    FANTASY_PLATFORM = "fantasy_platform"
    NFLVERSE_METADATA = "nflverse_metadata"
    TEAM_DEFENSE = "team_defense"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class PlayerMapping:
    league_id: str
    league_name: str
    player_name: str
    platform_player_id: str
    canonical_player_id: str | None
    platform_team: str | None
    nfl_team: str | None
    identity_method: ResolutionMethod
    team_source: TeamAssignmentSource
    team_conflict: bool
    detail: str | None = None

    @property
    def ready_for_schedule(self) -> bool:
        return self.canonical_player_id is not None and self.nfl_team is not None


@dataclass(frozen=True)
class RosterMappingResult:
    rosters: tuple[FantasyRoster, ...]
    players: tuple[PlayerMapping, ...]

    @property
    def unresolved(self) -> tuple[PlayerMapping, ...]:
        return tuple(player for player in self.players if not player.ready_for_schedule)

    @property
    def team_conflicts(self) -> tuple[PlayerMapping, ...]:
        return tuple(player for player in self.players if player.team_conflict)


def map_rosters_to_nfl(
    rosters: Sequence[FantasyRoster],
    resolver: PlayerIdentityResolver,
) -> RosterMappingResult:
    """Return immutable rosters with canonical IDs and best current NFL teams."""

    mapped_rosters: list[FantasyRoster] = []
    mappings: list[PlayerMapping] = []
    for roster in rosters:
        mapped_players: list[FantasyPlayer] = []
        for player in roster.players:
            resolution = resolver.resolve_fantasy_player(player)
            mapped_player, mapping = _map_player(player, resolution)
            mapped_players.append(mapped_player)
            mappings.append(mapping)
        mapped_rosters.append(replace(roster, players=tuple(mapped_players)))
    return RosterMappingResult(tuple(mapped_rosters), tuple(mappings))


def render_roster_mapping(result: RosterMappingResult) -> str:
    """Render a compact identity/team diagnostic without exposing secrets."""

    ready_count = sum(player.ready_for_schedule for player in result.players)
    source_counts = {
        source: sum(player.team_source is source for player in result.players)
        for source in TeamAssignmentSource
    }
    lines = [
        f"Fantasy leagues: {len(result.rosters)}",
        f"Players ready for schedule matching: {ready_count}/{len(result.players)}",
        "Team assignments: "
        + ", ".join(
            f"{source.value}={count}" for source, count in source_counts.items() if count
        ),
        f"Team conflicts: {len(result.team_conflicts)}",
        f"Unresolved: {len(result.unresolved)}",
    ]
    for mapping in result.team_conflicts:
        lines.append(
            f"  conflict: {mapping.league_name} — {mapping.player_name} — "
            f"platform={mapping.platform_team}, selected={mapping.nfl_team}"
        )
    for mapping in result.unresolved:
        lines.append(
            f"  unresolved: {mapping.league_name} — {mapping.player_name} — {mapping.detail}"
        )
    return "\n".join(lines)


def _map_player(
    player: FantasyPlayer,
    resolution: IdentityResolution,
) -> tuple[FantasyPlayer, PlayerMapping]:
    platform_team = normalize_team(player.nfl_team)
    identity = resolution.identity
    canonical_team = normalize_team(identity.team) if identity else None
    team_conflict = bool(platform_team and canonical_team and platform_team != canonical_team)

    if identity and identity.is_team_defense:
        nfl_team = canonical_team or platform_team
        team_source = TeamAssignmentSource.TEAM_DEFENSE
    elif identity and identity.team_source is CanonicalTeamSource.CURRENT_ROSTER and canonical_team:
        nfl_team = canonical_team
        team_source = TeamAssignmentSource.CURRENT_ROSTER
    elif platform_team:
        nfl_team = platform_team
        team_source = TeamAssignmentSource.FANTASY_PLATFORM
    elif canonical_team:
        nfl_team = canonical_team
        team_source = TeamAssignmentSource.NFLVERSE_METADATA
    else:
        nfl_team = None
        team_source = TeamAssignmentSource.UNRESOLVED

    canonical_id = identity.canonical_player_id if identity else None
    detail = resolution.detail
    if canonical_id and nfl_team is None:
        detail = "Canonical identity has no current NFL team"
    mapped = replace(
        player,
        canonical_player_id=canonical_id,
        nfl_team=nfl_team,
    )
    mapping = PlayerMapping(
        league_id=player.league_id,
        league_name=player.league_name,
        player_name=player.name,
        platform_player_id=player.platform_player_id,
        canonical_player_id=canonical_id,
        platform_team=platform_team,
        nfl_team=nfl_team,
        identity_method=resolution.method,
        team_source=team_source,
        team_conflict=team_conflict,
        detail=detail,
    )
    return mapped, mapping
