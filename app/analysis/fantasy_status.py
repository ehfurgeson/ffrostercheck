"""Map canonical NFL statuses back to league-specific fantasy instances."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from app.models import (
    FantasyAlertSeverity,
    FantasyLeague,
    FantasyPlayer,
    FantasyRoster,
    GameDayState,
    InjuryDesignation,
    NFLPlayerStatus,
    RosterEligibility,
)


class FantasyStatusMappingError(ValueError):
    """Raised when canonical NFL status input violates its uniqueness contract."""


class FantasyStatusIssueState(str, Enum):
    """A reason a fantasy-player instance has no canonical NFL status."""

    UNRESOLVED_PLAYER_IDENTITY = "unresolved_player_identity"
    MISSING_NFL_STATUS = "missing_nfl_status"


@dataclass(frozen=True)
class FantasyStatusIssue:
    """One league-specific player instance that could not be mapped safely."""

    state: FantasyStatusIssueState
    player: FantasyPlayer
    detail: str


@dataclass(frozen=True)
class LeaguePlayerStatus:
    """A fantasy-player instance and its shared canonical NFL status, if available."""

    player: FantasyPlayer
    status: NFLPlayerStatus | None
    severity: FantasyAlertSeverity

    @property
    def is_mapped(self) -> bool:
        return self.status is not None


@dataclass(frozen=True)
class FantasyLeagueStatuses:
    """Status view for one owned fantasy roster."""

    league: FantasyLeague
    team_name: str
    players: tuple[LeaguePlayerStatus, ...]

    @property
    def starters(self) -> tuple[LeaguePlayerStatus, ...]:
        return tuple(item for item in self.players if item.player.is_starter)

    @property
    def bench(self) -> tuple[LeaguePlayerStatus, ...]:
        return tuple(item for item in self.players if not item.player.is_starter)


@dataclass(frozen=True)
class FantasyStatusMapping:
    """All league-specific mappings plus explicit incomplete-mapping diagnostics."""

    leagues: tuple[FantasyLeagueStatuses, ...]
    issues: tuple[FantasyStatusIssue, ...]

    @property
    def is_complete(self) -> bool:
        return not self.issues


def determine_fantasy_severity(
    player: FantasyPlayer,
    status: NFLPlayerStatus | None,
) -> FantasyAlertSeverity:
    """Apply the deterministic V1 urgency policy in one league's lineup context."""

    if status is None:
        return (
            FantasyAlertSeverity.WARNING
            if player.is_starter
            else FantasyAlertSeverity.INFO
        )

    unavailable = (
        status.roster_eligibility is RosterEligibility.INELIGIBLE
        or status.game_day_state is GameDayState.INACTIVE
        or status.official_inactive is True
        or status.injury_designation is InjuryDesignation.OUT
    )
    risky_or_unknown = (
        status.roster_eligibility is RosterEligibility.UNKNOWN
        or status.game_day_state is GameDayState.UNKNOWN
        or status.injury_designation
        in {
            InjuryDesignation.DOUBTFUL,
            InjuryDesignation.QUESTIONABLE,
            InjuryDesignation.UNKNOWN,
        }
    )
    if player.is_starter:
        if unavailable:
            return FantasyAlertSeverity.CRITICAL
        if risky_or_unknown:
            return FantasyAlertSeverity.WARNING
        return FantasyAlertSeverity.NORMAL

    if unavailable or risky_or_unknown:
        return FantasyAlertSeverity.INFO
    return FantasyAlertSeverity.NORMAL


def map_nfl_statuses_to_fantasy_leagues(
    rosters: Sequence[FantasyRoster],
    statuses: Sequence[NFLPlayerStatus],
) -> FantasyStatusMapping:
    """Fan one canonical NFL status out to every matching fantasy instance."""

    statuses_by_id: dict[str, NFLPlayerStatus] = {}
    for status in statuses:
        canonical_id = status.canonical_player_id
        if canonical_id in statuses_by_id:
            raise FantasyStatusMappingError(
                f"duplicate NFL status for canonical player {canonical_id!r}"
            )
        statuses_by_id[canonical_id] = status

    leagues: list[FantasyLeagueStatuses] = []
    issues: list[FantasyStatusIssue] = []
    for roster in rosters:
        mapped_players: list[LeaguePlayerStatus] = []
        for player in roster.players:
            status = (
                statuses_by_id.get(player.canonical_player_id)
                if player.canonical_player_id
                else None
            )
            mapped_players.append(
                LeaguePlayerStatus(
                    player=player,
                    status=status,
                    severity=determine_fantasy_severity(player, status),
                )
            )
            if not player.canonical_player_id:
                issues.append(
                    FantasyStatusIssue(
                        state=FantasyStatusIssueState.UNRESOLVED_PLAYER_IDENTITY,
                        player=player,
                        detail="fantasy player has no canonical NFL identity",
                    )
                )
            elif status is None:
                issues.append(
                    FantasyStatusIssue(
                        state=FantasyStatusIssueState.MISSING_NFL_STATUS,
                        player=player,
                        detail=(
                            "no NFL status was produced for canonical player "
                            f"{player.canonical_player_id}"
                        ),
                    )
                )
        leagues.append(
            FantasyLeagueStatuses(
                league=roster.league,
                team_name=roster.team_name,
                players=tuple(mapped_players),
            )
        )
    return FantasyStatusMapping(leagues=tuple(leagues), issues=tuple(issues))
