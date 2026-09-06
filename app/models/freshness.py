"""Freshness evidence for final fantasy-lineup refreshes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class LineupRefreshEvidence:
    """One fantasy provider response used by a T−5 decision."""

    source: str
    retrieved_at: datetime
    http_cache_age_seconds: int | None = None
    detail: str | None = None
