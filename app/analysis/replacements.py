"""Find verified, league-specific bench replacements for affected starters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from app.analysis.eligibility import LeagueRosterEligibility
from app.analysis.fantasy_status import FantasyLeagueStatuses, LeaguePlayerStatus
from app.models import (
    DepthOpportunity,
    FantasyAlertSeverity,
    GameDayState,
    InjuryDesignation,
    OpportunityLevel,
    RosterEligibility,
)


@dataclass(frozen=True)
class ReplacementCandidate:
    """One verified bench player who can fill an affected starter's slot."""

    player_status: LeaguePlayerStatus
    opportunity: DepthOpportunity | None = None


@dataclass(frozen=True)
class StarterReplacementOptions:
    """Ranked replacements for one starter who needs attention."""

    starter: LeaguePlayerStatus
    candidates: tuple[ReplacementCandidate, ...]


def find_valid_bench_substitutes(
    league_statuses: FantasyLeagueStatuses,
    eligibility: LeagueRosterEligibility,
    opportunities: Sequence[DepthOpportunity] = (),
) -> tuple[StarterReplacementOptions, ...]:
    """Return ranked, verified substitutes for each critical or warning starter.

    This milestone deliberately does not evaluate kickoff locks. Callers must apply
    game-start exclusion before presenting these candidates as actionable.
    """

    if (
        eligibility.league_id != league_statuses.league.id
        or eligibility.platform is not league_statuses.league.platform
    ):
        raise ValueError("roster eligibility does not belong to the supplied league")

    opportunities_by_id = _index_opportunities(opportunities)
    options: list[StarterReplacementOptions] = []
    for starter in league_statuses.starters:
        if starter.severity not in {
            FantasyAlertSeverity.CRITICAL,
            FantasyAlertSeverity.WARNING,
        }:
            continue
        candidates = [
            ReplacementCandidate(
                player_status=bench_player,
                opportunity=(
                    opportunities_by_id.get(bench_player.player.canonical_player_id)
                    if bench_player.player.canonical_player_id
                    else None
                ),
            )
            for bench_player in league_statuses.bench
            if not bench_player.player.is_reserve
            and not bench_player.player.is_taxi
            and eligibility.is_player_eligible(
                bench_player.player,
                starter.player.lineup_slot,
            )
            and _is_verified_available(bench_player)
        ]
        candidates.sort(key=_candidate_rank)
        options.append(StarterReplacementOptions(starter, tuple(candidates)))
    return tuple(options)


def _is_verified_available(candidate: LeaguePlayerStatus) -> bool:
    status = candidate.status
    return bool(
        status is not None
        and status.roster_eligibility is RosterEligibility.ELIGIBLE
        and status.game_day_state is GameDayState.ACTIVE
        and status.injury_designation
        in {InjuryDesignation.NONE, InjuryDesignation.QUESTIONABLE}
    )


def _index_opportunities(
    opportunities: Sequence[DepthOpportunity],
) -> Mapping[str, DepthOpportunity]:
    strongest: dict[str, DepthOpportunity] = {}
    for opportunity in opportunities:
        current = strongest.get(opportunity.beneficiary_player_id)
        if current is None or _opportunity_rank(opportunity) < _opportunity_rank(current):
            strongest[opportunity.beneficiary_player_id] = opportunity
    return strongest


def _candidate_rank(candidate: ReplacementCandidate) -> tuple[int, int, int, str, str]:
    status = candidate.player_status.status
    assert status is not None
    return (
        0 if status.injury_designation is InjuryDesignation.NONE else 1,
        -int(status.confidence),
        _opportunity_rank(candidate.opportunity),
        candidate.player_status.player.name.casefold(),
        candidate.player_status.player.platform_player_id,
    )


def _opportunity_rank(opportunity: DepthOpportunity | None) -> int:
    if opportunity is None:
        return 3
    return {
        OpportunityLevel.PROMOTED: 0,
        OpportunityLevel.ROLE_BOOST: 1,
        OpportunityLevel.POSITIONAL_OPPORTUNITY: 2,
    }[opportunity.level]
