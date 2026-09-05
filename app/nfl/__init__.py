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

__all__ = [
    "CanonicalPlayer",
    "CanonicalTeamSource",
    "DatasetLoad",
    "DatasetState",
    "IdentityResolution",
    "NFLVerseLoadError",
    "NFLVerseSchemaError",
    "NFLVerseSnapshot",
    "NFLVerseSource",
    "PlayerIdentityResolver",
    "PlayerMapping",
    "ResolutionMethod",
    "RosterMappingResult",
    "TeamAssignmentSource",
    "map_rosters_to_nfl",
    "render_roster_mapping",
]
