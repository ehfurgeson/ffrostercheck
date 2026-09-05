"""Fantasy-agnostic NFL status analysis."""

from app.analysis.availability import (
    StatusSubject,
    combine_official_statuses,
    render_player_statuses,
    subjects_from_fantasy_players,
    subjects_from_source_reports,
)
from app.analysis.confidence import EvidenceOrigin, degrade_cached_confidence, score_status_confidence

__all__ = [
    "EvidenceOrigin",
    "StatusSubject",
    "combine_official_statuses",
    "degrade_cached_confidence",
    "render_player_statuses",
    "score_status_confidence",
    "subjects_from_fantasy_players",
    "subjects_from_source_reports",
]
