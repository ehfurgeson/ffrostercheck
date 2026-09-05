"""Canonical NFL player identities and conservative provider resolution."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable, Mapping

from app.models import FantasyPlayer, FantasyPlatform
from app.nfl.nflverse import DataFrameLike


TEAM_ALIASES = {
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "GBP": "GB",
    "HST": "HOU",
    "JAC": "JAX",
    "KCC": "KC",
    "LA": "LAR",
    "LVR": "LV",
    "NEP": "NE",
    "NOS": "NO",
    "OAK": "LV",
    "SD": "LAC",
    "SFO": "SF",
    "STL": "LAR",
    "TBB": "TB",
    "WSH": "WAS",
}
TEAM_DEFENSE_POSITIONS = frozenset({"DEF", "DST", "D/ST"})
NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})


class CanonicalTeamSource(str, Enum):
    CURRENT_ROSTER = "current_roster"
    PLAYER_METADATA = "player_metadata"
    FANTASY_ID_CROSSWALK = "fantasy_id_crosswalk"
    PLATFORM = "platform"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CanonicalPlayer:
    """One real NFL player keyed by a stable GSIS identifier."""

    canonical_player_id: str
    name: str
    team: str | None
    position: str | None
    espn_id: str | None = None
    sleeper_id: str | None = None
    team_source: CanonicalTeamSource = CanonicalTeamSource.UNKNOWN

    @property
    def gsis_id(self) -> str:
        return self.canonical_player_id

    @property
    def is_team_defense(self) -> bool:
        return self.canonical_player_id.startswith("DST:")


class ResolutionMethod(str, Enum):
    PLATFORM_ID = "platform_id"
    NAME_TEAM_POSITION = "name_team_position"
    NAME_POSITION_TEAM_TRANSITION = "name_position_team_transition"
    MANUAL_OVERRIDE = "manual_override"
    TEAM_DEFENSE = "team_defense"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class IdentityResolution:
    identity: CanonicalPlayer | None
    method: ResolutionMethod
    detail: str | None = None

    @property
    def resolved(self) -> bool:
        return self.identity is not None


@dataclass
class _IdentityBuilder:
    gsis_id: str
    name: str | None = None
    team: str | None = None
    position: str | None = None
    espn_id: str | None = None
    sleeper_id: str | None = None
    team_source: CanonicalTeamSource = CanonicalTeamSource.UNKNOWN

    def freeze(self) -> CanonicalPlayer | None:
        if not self.name:
            return None
        return CanonicalPlayer(
            canonical_player_id=self.gsis_id,
            name=self.name,
            team=self.team,
            position=self.position,
            espn_id=self.espn_id,
            sleeper_id=self.sleeper_id,
            team_source=self.team_source,
        )


class PlayerIdentityResolver:
    """Resolve platform players without silently choosing ambiguous matches."""

    def __init__(
        self,
        identities: Iterable[CanonicalPlayer],
        *,
        manual_overrides: Mapping[tuple[str, str], str] | None = None,
    ) -> None:
        identity_list = tuple(identities)
        self._by_gsis = {identity.gsis_id: identity for identity in identity_list}
        self._by_espn = _unique_id_index(identity_list, "espn_id")
        self._by_sleeper = _unique_id_index(identity_list, "sleeper_id")
        self._by_name_team_position = _group_identities(
            identity_list,
            lambda identity: (
                normalize_name(identity.name),
                normalize_team(identity.team),
                normalize_position(identity.position),
            ),
            require_team=True,
        )
        self._by_name_position = _group_identities(
            identity_list,
            lambda identity: (
                normalize_name(identity.name),
                normalize_position(identity.position),
            ),
        )
        self._manual_overrides = {
            (platform.casefold(), _normalize_id(player_id)): gsis_id
            for (platform, player_id), gsis_id in (manual_overrides or {}).items()
        }

    @classmethod
    def from_nflverse(
        cls,
        *,
        fantasy_player_ids: DataFrameLike,
        players: DataFrameLike,
        rosters: DataFrameLike,
        manual_overrides: Mapping[tuple[str, str], str] | None = None,
    ) -> PlayerIdentityResolver:
        """Build a crosswalk, preferring the current roster for team and role."""

        builders: dict[str, _IdentityBuilder] = {}
        for row in _rows(fantasy_player_ids):
            _merge_identity(
                builders,
                row,
                name_field="name",
                team_field="team",
                overwrite_context=False,
                include_sleeper=True,
                team_source=CanonicalTeamSource.FANTASY_ID_CROSSWALK,
            )
        for row in _rows(players):
            _merge_identity(
                builders,
                row,
                name_field="display_name",
                team_field="latest_team",
                overwrite_context=True,
                include_sleeper=False,
                team_source=CanonicalTeamSource.PLAYER_METADATA,
            )
        roster_rows = sorted(
            _rows(rosters),
            key=lambda row: (_integer(row.get("season")), _integer(row.get("week"))),
        )
        for row in roster_rows:
            _merge_identity(
                builders,
                row,
                name_field="full_name",
                team_field="team",
                overwrite_context=True,
                include_sleeper=True,
                team_source=CanonicalTeamSource.CURRENT_ROSTER,
            )

        identities = tuple(
            identity
            for builder in builders.values()
            if (identity := builder.freeze()) is not None
        )
        return cls(identities, manual_overrides=manual_overrides)

    @property
    def identities(self) -> tuple[CanonicalPlayer, ...]:
        return tuple(self._by_gsis.values())

    def resolve_fantasy_player(
        self,
        player: FantasyPlayer,
        *,
        allow_team_transition: bool = False,
    ) -> IdentityResolution:
        return self.resolve(
            platform=player.platform,
            platform_player_id=player.platform_player_id,
            name=player.name,
            team=player.nfl_team,
            position=player.position,
            allow_team_transition=allow_team_transition,
        )

    def resolve_espn_player(
        self,
        *,
        player_id: str,
        name: str,
        team: str | None,
        position: str | None,
        allow_team_transition: bool = False,
    ) -> IdentityResolution:
        return self.resolve(
            platform=FantasyPlatform.ESPN,
            platform_player_id=player_id,
            name=name,
            team=team,
            position=position,
            allow_team_transition=allow_team_transition,
        )

    def resolve_sleeper_player(
        self,
        *,
        player_id: str,
        name: str,
        team: str | None,
        position: str | None,
        allow_team_transition: bool = False,
    ) -> IdentityResolution:
        return self.resolve(
            platform=FantasyPlatform.SLEEPER,
            platform_player_id=player_id,
            name=name,
            team=team,
            position=position,
            allow_team_transition=allow_team_transition,
        )

    def resolve(
        self,
        *,
        platform: FantasyPlatform | str,
        platform_player_id: str,
        name: str,
        team: str | None,
        position: str | None,
        allow_team_transition: bool = False,
    ) -> IdentityResolution:
        platform_value = platform.value if isinstance(platform, FantasyPlatform) else platform
        platform_key = platform_value.casefold()
        normalized_id = _normalize_id(platform_player_id)
        normalized_team = normalize_team(team)
        normalized_position = normalize_position(position)

        if normalized_position == "DST":
            if normalized_team:
                return IdentityResolution(
                    identity=CanonicalPlayer(
                        canonical_player_id=f"DST:{normalized_team}",
                        name=f"{normalized_team} D/ST",
                        team=normalized_team,
                        position="DST",
                        team_source=CanonicalTeamSource.PLATFORM,
                    ),
                    method=ResolutionMethod.TEAM_DEFENSE,
                )
            return _unresolved("Team defense has no NFL team")

        provider_index = {
            FantasyPlatform.ESPN.value: self._by_espn,
            FantasyPlatform.SLEEPER.value: self._by_sleeper,
        }.get(platform_key)
        if provider_index is not None:
            identity = provider_index.get(normalized_id)
            if identity is not None:
                return IdentityResolution(identity, ResolutionMethod.PLATFORM_ID)

        name_key = normalize_name(name)
        constrained = self._by_name_team_position.get(
            (name_key, normalized_team, normalized_position), ()
        )
        if len(constrained) == 1:
            return IdentityResolution(constrained[0], ResolutionMethod.NAME_TEAM_POSITION)

        if allow_team_transition:
            transition = self._by_name_position.get((name_key, normalized_position), ())
            if len(transition) == 1:
                return IdentityResolution(
                    transition[0],
                    ResolutionMethod.NAME_POSITION_TEAM_TRANSITION,
                )

        override_id = self._manual_overrides.get((platform_key, normalized_id))
        if override_id is not None and override_id in self._by_gsis:
            return IdentityResolution(
                self._by_gsis[override_id],
                ResolutionMethod.MANUAL_OVERRIDE,
            )

        return _unresolved("No unique canonical player match")

    def resolve_name_team_position(
        self,
        *,
        name: str,
        team: str | None,
        position: str | None,
        allow_team_transition: bool = False,
    ) -> IdentityResolution:
        return self.resolve(
            platform="official",
            platform_player_id="",
            name=name,
            team=team,
            position=position,
            allow_team_transition=allow_team_transition,
        )


def normalize_name(name: str) -> str:
    """Normalize punctuation, accents, whitespace, and common suffixes."""

    ascii_name = "".join(
        character
        for character in unicodedata.normalize("NFKD", name)
        if not unicodedata.combining(character)
    )
    tokens = re.findall(r"[a-z0-9]+", ascii_name.casefold())
    if tokens and tokens[-1] in NAME_SUFFIXES:
        tokens.pop()
    return "".join(tokens)


def normalize_team(team: str | None) -> str | None:
    if not team:
        return None
    normalized = team.strip().upper()
    return TEAM_ALIASES.get(normalized, normalized)


def normalize_position(position: str | None) -> str | None:
    if not position:
        return None
    normalized = position.strip().upper()
    if normalized in TEAM_DEFENSE_POSITIONS:
        return "DST"
    if normalized in {"FB", "HB"}:
        return "RB"
    return normalized


def _rows(frame: DataFrameLike) -> list[Mapping[str, Any]]:
    iter_rows = getattr(frame, "iter_rows", None)
    if not callable(iter_rows):
        raise TypeError("Identity crosswalk requires a Polars-like frame with iter_rows")
    return list(iter_rows(named=True))


def _merge_identity(
    builders: dict[str, _IdentityBuilder],
    row: Mapping[str, Any],
    *,
    name_field: str,
    team_field: str,
    overwrite_context: bool,
    include_sleeper: bool,
    team_source: CanonicalTeamSource,
) -> None:
    gsis_id = _optional_id(row.get("gsis_id"))
    if not gsis_id:
        return
    builder = builders.setdefault(gsis_id, _IdentityBuilder(gsis_id=gsis_id))
    name = _optional_text(row.get(name_field))
    team = normalize_team(_optional_text(row.get(team_field)))
    position = _optional_text(row.get("position"))
    espn_id = _optional_id(row.get("espn_id"))
    sleeper_id = _optional_id(row.get("sleeper_id")) if include_sleeper else None

    if overwrite_context:
        builder.name = name or builder.name
        if team:
            builder.team = team
            builder.team_source = team_source
        builder.position = position or builder.position
        builder.espn_id = espn_id or builder.espn_id
        builder.sleeper_id = sleeper_id or builder.sleeper_id
        return

    builder.name = builder.name or name
    if not builder.team and team:
        builder.team = team
        builder.team_source = team_source
    builder.position = builder.position or position
    builder.espn_id = builder.espn_id or espn_id
    builder.sleeper_id = builder.sleeper_id or sleeper_id


def _unique_id_index(
    identities: Iterable[CanonicalPlayer],
    attribute: str,
) -> dict[str, CanonicalPlayer]:
    index: dict[str, CanonicalPlayer] = {}
    ambiguous: set[str] = set()
    for identity in identities:
        provider_id = getattr(identity, attribute)
        if not provider_id or provider_id in ambiguous:
            continue
        existing = index.get(provider_id)
        if existing is not None and existing.gsis_id != identity.gsis_id:
            ambiguous.add(provider_id)
            index.pop(provider_id)
        else:
            index[provider_id] = identity
    return index


def _group_identities(
    identities: Iterable[CanonicalPlayer],
    key: Callable[[CanonicalPlayer], tuple[str | None, ...]],
    *,
    require_team: bool = False,
) -> dict[tuple[str | None, ...], tuple[CanonicalPlayer, ...]]:
    groups: dict[tuple[str | None, ...], list[CanonicalPlayer]] = {}
    for identity in identities:
        identity_key = key(identity)
        if not identity_key[0] or not identity_key[-1]:
            continue
        if require_team and not identity_key[1]:
            continue
        groups.setdefault(identity_key, []).append(identity)
    return {identity_key: tuple(values) for identity_key, values in groups.items()}


def _normalize_id(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _optional_id(value: Any) -> str | None:
    if value is None:
        return None
    normalized = _normalize_id(value)
    return normalized or None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _unresolved(detail: str) -> IdentityResolution:
    return IdentityResolution(None, ResolutionMethod.UNRESOLVED, detail)
