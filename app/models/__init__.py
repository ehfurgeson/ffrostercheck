"""Platform-neutral Fantasy Watchdog data models."""

from app.models.freshness import LineupRefreshEvidence
from app.models.game import NFLGame, RelevantGame
from app.models.league import FantasyLeague, FantasyPlatform, FantasyRoster
from app.models.opportunity import DepthOpportunity, OpportunityLevel
from app.models.player import FantasyPlayer
from app.models.status import (
    Confidence,
    FantasyAlertSeverity,
    GameDayState,
    GameSourceReport,
    InjuryDesignation,
    NFLPlayerStatus,
    ReportState,
    RosterEligibility,
    SourceResult,
)

__all__ = [
    "Confidence",
    "DepthOpportunity",
    "FantasyLeague",
    "FantasyAlertSeverity",
    "FantasyPlatform",
    "FantasyPlayer",
    "FantasyRoster",
    "GameDayState",
    "GameSourceReport",
    "InjuryDesignation",
    "LineupRefreshEvidence",
    "NFLGame",
    "NFLPlayerStatus",
    "OpportunityLevel",
    "RelevantGame",
    "ReportState",
    "RosterEligibility",
    "SourceResult",
]
