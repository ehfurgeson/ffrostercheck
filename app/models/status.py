"""Status enums and source-report models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, IntEnum


class ReportState(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    NOT_YET_PUBLISHED = "not_yet_published"
    FAILED = "failed"


class GameDayState(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"


class RosterEligibility(str, Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"


class InjuryDesignation(str, Enum):
    OUT = "out"
    DOUBTFUL = "doubtful"
    QUESTIONABLE = "questionable"
    NONE = "none"
    UNKNOWN = "unknown"


class Confidence(IntEnum):
    OFFICIAL = 4
    HIGH = 3
    MEDIUM = 2
    LOW = 1


class FantasyAlertSeverity(str, Enum):
    """League-specific urgency after an NFL status is mapped to a roster."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"
    NORMAL = "normal"


@dataclass(frozen=True)
class SourceResult:
    source: str
    source_url: str | None
    success: bool
    report_state: ReportState
    retrieved_at: datetime
    roster_eligibility: RosterEligibility | None = None
    game_day_state: GameDayState | None = None
    injury_designation: InjuryDesignation | None = None
    detail: str | None = None
    player_name: str | None = None
    nfl_team: str | None = None
    position: str | None = None
    canonical_player_id: str | None = None
    published_at: datetime | None = None
    source_updated_at: datetime | None = None
    http_cache_age_seconds: int | None = None
    raw_content_hash: str | None = None


@dataclass(frozen=True)
class GameSourceReport:
    source: str
    game_id: str
    report_state: ReportState
    expected_teams: frozenset[str]
    parsed_teams: frozenset[str]
    player_results: tuple[SourceResult, ...]
    retrieved_at: datetime
    source_updated_at: datetime | None = None
    errors: tuple[str, ...] = ()
    source_url: str | None = None
    published_at: datetime | None = None
    http_cache_age_seconds: int | None = None
    raw_content_hash: str | None = None


@dataclass(frozen=True)
class NFLPlayerStatus:
    """One canonical NFL availability record shared across fantasy leagues."""

    canonical_player_id: str
    roster_eligibility: RosterEligibility
    game_day_state: GameDayState
    injury_designation: InjuryDesignation
    confidence: Confidence
    decision_at: datetime
    injury_description: str | None = None
    official_inactive: bool | None = None
    source_results: tuple[SourceResult, ...] = ()
    name: str | None = None
    nfl_team: str | None = None
    position: str | None = None
