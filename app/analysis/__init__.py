"""Fantasy-agnostic NFL status analysis."""

from app.analysis.availability import (
    StatusScope,
    StatusScopeIssue,
    StatusScopeIssueState,
    StatusSubject,
    build_status_scope,
    combine_official_statuses,
    render_player_statuses,
    render_status_scope,
    subjects_from_fantasy_players,
    subjects_from_source_reports,
)
from app.analysis.confidence import EvidenceOrigin, degrade_cached_confidence, score_status_confidence

__all__ = [
    "EvidenceOrigin",
    "StatusScope",
    "StatusScopeIssue",
    "StatusScopeIssueState",
    "StatusSubject",
    "build_status_scope",
    "combine_official_statuses",
    "degrade_cached_confidence",
    "render_player_statuses",
    "render_status_scope",
    "score_status_confidence",
    "subjects_from_fantasy_players",
    "subjects_from_source_reports",
]
