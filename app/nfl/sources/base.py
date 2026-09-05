"""Shared contract for per-game player status sources."""

from __future__ import annotations

from typing import Protocol

from app.models import GameSourceReport, RelevantGame


class PlayerStatusSource(Protocol):
    name: str
    priority: int

    def fetch_game(self, game: RelevantGame) -> GameSourceReport:
        """Return a game-level report. Absence is meaningful only when complete."""
        ...
