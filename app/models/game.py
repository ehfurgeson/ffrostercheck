"""Timezone-aware NFL game model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class NFLGame:
    """One scheduled NFL game with a UTC kickoff derived from Eastern fields."""

    game_id: str
    season: int
    week: int
    game_type: str
    home_team: str
    away_team: str
    kickoff: datetime

    def involves(self, team: str) -> bool:
        return team in {self.home_team, self.away_team}

    def opponent(self, team: str) -> str | None:
        if team == self.home_team:
            return self.away_team
        if team == self.away_team:
            return self.home_team
        return None
