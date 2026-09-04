"""Normalized fantasy league and roster models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from app.models.player import FantasyPlayer


class FantasyPlatform(str, Enum):
    ESPN = "espn"
    SLEEPER = "sleeper"


@dataclass(frozen=True)
class FantasyLeague:
    id: str
    name: str
    nickname: str
    platform: FantasyPlatform
    roster_id: str
    roster_rules: Mapping[str, Any]
    scoring_settings: Mapping[str, Any]


@dataclass(frozen=True)
class FantasyRoster:
    league: FantasyLeague
    team_name: str
    players: tuple[FantasyPlayer, ...]

    @property
    def starters(self) -> tuple[FantasyPlayer, ...]:
        return tuple(player for player in self.players if player.is_starter)

    @property
    def bench(self) -> tuple[FantasyPlayer, ...]:
        return tuple(player for player in self.players if not player.is_starter)
