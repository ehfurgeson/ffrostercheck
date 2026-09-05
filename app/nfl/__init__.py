"""NFL and nflverse data integrations."""

from app.nfl.identity import (
    CanonicalPlayer,
    CanonicalTeamSource,
    IdentityResolution,
    PlayerIdentityResolver,
    ResolutionMethod,
)
from app.nfl.nflverse import (
    DatasetLoad,
    DatasetState,
    NFLVerseLoadError,
    NFLVerseSchemaError,
    NFLVerseSnapshot,
    NFLVerseSource,
)
from app.nfl.roster import (
    PlayerMapping,
    RosterMappingResult,
    TeamAssignmentSource,
    map_rosters_to_nfl,
    render_roster_mapping,
)
from app.nfl.schedule import (
    InvalidScheduleRow,
    NextGameAssignmentResult,
    NextGameState,
    NFLSchedule,
    PlayerNextGame,
    assign_next_games,
    parse_nfl_schedule,
    render_next_games,
)

__all__ = [
    "CanonicalPlayer",
    "CanonicalTeamSource",
    "DatasetLoad",
    "DatasetState",
    "IdentityResolution",
    "InvalidScheduleRow",
    "NFLSchedule",
    "NFLVerseLoadError",
    "NFLVerseSchemaError",
    "NFLVerseSnapshot",
    "NFLVerseSource",
    "NextGameAssignmentResult",
    "NextGameState",
    "PlayerIdentityResolver",
    "PlayerMapping",
    "PlayerNextGame",
    "ResolutionMethod",
    "RosterMappingResult",
    "TeamAssignmentSource",
    "assign_next_games",
    "map_rosters_to_nfl",
    "parse_nfl_schedule",
    "render_next_games",
    "render_roster_mapping",
]
