"""Depth-chart opportunity models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from app.models.status import Confidence


class OpportunityLevel(str, Enum):
    """Strength of a depth-chart opportunity signal."""

    PROMOTED = "promoted"
    ROLE_BOOST = "role_boost"
    POSITIONAL_OPPORTUNITY = "positional_opportunity"


@dataclass(frozen=True)
class DepthOpportunity:
    """An owned player's effective role after official unavailability decisions."""

    beneficiary_player_id: str
    unavailable_player_ids: tuple[str, ...]
    level: OpportunityLevel
    previous_order_in_slot: int
    effective_order_in_slot: int
    promoted_to_first_available: bool
    confidence: Confidence
    depth_chart_as_of: datetime
    status_decision_at: datetime
