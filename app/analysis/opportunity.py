"""Resolve depth-chart opportunity from official NFL availability evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.models import (
    Confidence,
    DepthOpportunity,
    GameDayState,
    InjuryDesignation,
    NFLPlayerStatus,
    OpportunityLevel,
)
from app.nfl.depth_relations import DepthSlotChain, OwnedDepthRelations


OFFICIAL_INACTIVES_SOURCE = "nfl_inactives"
OFFICIAL_INJURIES_SOURCE = "nfl_injuries"


def detect_depth_opportunities(
    relations: OwnedDepthRelations,
    statuses: Sequence[NFLPlayerStatus],
) -> tuple[DepthOpportunity, ...]:
    """Return the strongest supported opportunity signal for each owned player.

    Only explicit official inactive or Out evidence removes a player from a depth
    chain. Unknown, questionable, doubtful, and fallback-only states remain ahead.
    """

    statuses_by_id = {status.canonical_player_id: status for status in statuses}
    opportunities: list[DepthOpportunity] = []
    for beneficiary_id, relation in relations.relations.items():
        unavailable_ahead = tuple(
            blocker.canonical_player_id
            for blocker in relation.players_ahead
            if blocker.canonical_player_id is not None
            and _is_officially_unavailable(statuses_by_id.get(blocker.canonical_player_id))
        )
        if unavailable_ahead:
            previous_order = len(relation.players_ahead) + 1
            effective_order = previous_order - len(unavailable_ahead)
            promoted = effective_order == 1
            informing_statuses = tuple(statuses_by_id[player_id] for player_id in unavailable_ahead)
            opportunities.append(
                DepthOpportunity(
                    beneficiary_player_id=beneficiary_id,
                    unavailable_player_ids=unavailable_ahead,
                    level=(
                        OpportunityLevel.PROMOTED if promoted else OpportunityLevel.ROLE_BOOST
                    ),
                    previous_order_in_slot=previous_order,
                    effective_order_in_slot=effective_order,
                    promoted_to_first_available=promoted,
                    confidence=_bounded_confidence(informing_statuses, Confidence.HIGH),
                    depth_chart_as_of=relation.snapshot_at,
                    status_decision_at=max(status.decision_at for status in informing_statuses),
                )
            )
            continue

        positional_statuses = _unavailable_other_slot_starters(
            relation.team,
            relation.position,
            relation.formation,
            relation.position_slot,
            relations.chains,
            statuses_by_id,
        )
        if not positional_statuses:
            continue
        previous_order = len(relation.players_ahead) + 1
        opportunities.append(
            DepthOpportunity(
                beneficiary_player_id=beneficiary_id,
                unavailable_player_ids=tuple(
                    status.canonical_player_id for status in positional_statuses
                ),
                level=OpportunityLevel.POSITIONAL_OPPORTUNITY,
                previous_order_in_slot=previous_order,
                effective_order_in_slot=previous_order,
                promoted_to_first_available=False,
                confidence=_bounded_confidence(positional_statuses, Confidence.MEDIUM),
                depth_chart_as_of=relation.snapshot_at,
                status_decision_at=max(status.decision_at for status in positional_statuses),
            )
        )
    return tuple(opportunities)


def render_depth_opportunities(opportunities: Sequence[DepthOpportunity]) -> str:
    """Render deterministic opportunity diagnostics without workload claims."""

    lines = [f"Depth opportunities: {len(opportunities)}"]
    for opportunity in opportunities:
        unavailable = ", ".join(opportunity.unavailable_player_ids)
        lines.append(
            f"  {opportunity.beneficiary_player_id}: {opportunity.level.value}; "
            f"order {opportunity.previous_order_in_slot} -> "
            f"{opportunity.effective_order_in_slot}; unavailable: {unavailable}; "
            f"confidence={opportunity.confidence.name.lower()}"
        )
    return "\n".join(lines)


def _unavailable_other_slot_starters(
    team: str,
    position: str,
    formation: str,
    position_slot: int,
    chains: Mapping[object, DepthSlotChain],
    statuses_by_id: Mapping[str, NFLPlayerStatus],
) -> tuple[NFLPlayerStatus, ...]:
    unavailable: dict[str, NFLPlayerStatus] = {}
    for key, chain in chains.items():
        if (
            key.team != team
            or key.position != position
            or key.formation != formation
            or key.position_slot == position_slot
            or not chain.players
        ):
            continue
        first_rank = chain.players[0].source_rank
        for row in chain.players:
            if row.source_rank != first_rank:
                break
            if row.gsis_id is None:
                continue
            status = statuses_by_id.get(row.gsis_id)
            if _is_officially_unavailable(status):
                unavailable.setdefault(row.gsis_id, status)
    return tuple(unavailable.values())


def _is_officially_unavailable(status: NFLPlayerStatus | None) -> bool:
    if status is None:
        return False
    if status.official_inactive is True:
        return True
    return any(
        result.success
        and (
            (
                result.source == OFFICIAL_INACTIVES_SOURCE
                and result.game_day_state is GameDayState.INACTIVE
            )
            or (
                result.source == OFFICIAL_INJURIES_SOURCE
                and result.injury_designation is InjuryDesignation.OUT
            )
        )
        for result in status.source_results
    )


def _bounded_confidence(
    statuses: Sequence[NFLPlayerStatus],
    maximum: Confidence,
) -> Confidence:
    return min(maximum, *(status.confidence for status in statuses))
