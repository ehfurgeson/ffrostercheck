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
from app.analysis.confidence import (
    EvidenceOrigin,
    degrade_cached_confidence,
    score_status_confidence,
)
from app.analysis.fantasy_status import (
    FantasyLeagueStatuses,
    FantasyStatusIssue,
    FantasyStatusIssueState,
    FantasyStatusMapping,
    FantasyStatusMappingError,
    LeaguePlayerStatus,
    determine_fantasy_severity,
    map_nfl_statuses_to_fantasy_leagues,
)
from app.analysis.opportunity import (
    DepthOpportunityAnalysis,
    OpportunityLimitation,
    OpportunityLimitationState,
    analyze_depth_opportunities,
    detect_depth_opportunities,
    render_depth_opportunities,
    render_depth_opportunity_analysis,
)

__all__ = [
    "DepthOpportunityAnalysis",
    "EvidenceOrigin",
    "FantasyLeagueStatuses",
    "FantasyStatusIssue",
    "FantasyStatusIssueState",
    "FantasyStatusMapping",
    "FantasyStatusMappingError",
    "LeaguePlayerStatus",
    "OpportunityLimitation",
    "OpportunityLimitationState",
    "StatusScope",
    "StatusScopeIssue",
    "StatusScopeIssueState",
    "StatusSubject",
    "analyze_depth_opportunities",
    "build_status_scope",
    "combine_official_statuses",
    "degrade_cached_confidence",
    "detect_depth_opportunities",
    "determine_fantasy_severity",
    "map_nfl_statuses_to_fantasy_leagues",
    "render_player_statuses",
    "render_depth_opportunities",
    "render_depth_opportunity_analysis",
    "render_status_scope",
    "score_status_confidence",
    "subjects_from_fantasy_players",
    "subjects_from_source_reports",
]
