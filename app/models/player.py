"""Normalized fantasy player model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.league import FantasyPlatform


@dataclass(frozen=True)
class FantasyPlayer:
    platform_player_id: str
    name: str
    nfl_team: str | None
    position: str | None
    league_id: str
    league_name: str
    platform: FantasyPlatform
    lineup_slot: str
    eligible_slots: tuple[str, ...]
    is_starter: bool
    is_reserve: bool = False
    is_taxi: bool = False
    platform_injury_status: str | None = None
    canonical_player_id: str | None = None
