"""Sleeper API ingestion and roster interpretation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Mapping, Sequence

import httpx

from app.config import LeagueConfig


SLEEPER_API_URL = "https://api.sleeper.app/v1"
NON_STARTER_SLOTS = {"BN", "IR", "TAXI"}


class SleeperAPIError(RuntimeError):
    """Raised when Sleeper cannot provide a valid response."""


@dataclass(frozen=True)
class SleeperResponseMetadata:
    retrieved_at: datetime
    response_date: datetime | None
    cache_age_seconds: int | None
    etag: str | None
    cache_status: str | None


@dataclass(frozen=True)
class SleeperPlayer:
    player_id: str
    name: str
    nfl_team: str | None
    position: str | None
    lineup_slot: str
    is_starter: bool
    is_reserve: bool = False
    is_taxi: bool = False


@dataclass(frozen=True)
class SleeperLeagueRoster:
    league_id: str
    league_name: str
    nickname: str
    roster_id: str
    roster_positions: tuple[str, ...]
    scoring_settings: Mapping[str, Any]
    starters: tuple[SleeperPlayer, ...]
    bench: tuple[SleeperPlayer, ...]

    @property
    def players(self) -> tuple[SleeperPlayer, ...]:
        return self.starters + self.bench


class SleeperClient:
    """Small synchronous client for Sleeper's public fantasy API."""

    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            base_url=SLEEPER_API_URL,
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "fantasy-watchdog/0.1"},
        )
        self.response_metadata: dict[str, SleeperResponseMetadata] = {}

    def __enter__(self) -> SleeperClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def resolve_user(self, username: str) -> Mapping[str, Any]:
        user = self._get_mapping(f"/user/{username}")
        if not user.get("user_id"):
            raise SleeperAPIError(f"Sleeper user not found: {username}")
        return user

    def get_leagues(self, user_id: str, season: int) -> list[Mapping[str, Any]]:
        return self._get_mapping_list(f"/user/{user_id}/leagues/nfl/{season}")

    def get_league(self, league_id: str) -> Mapping[str, Any]:
        return self._get_mapping(f"/league/{league_id}")

    def get_rosters(self, league_id: str) -> list[Mapping[str, Any]]:
        return self._get_mapping_list(f"/league/{league_id}/rosters")

    def get_player_catalog(self) -> Mapping[str, Mapping[str, Any]]:
        raw = self._get_mapping("/players/nfl")
        if not all(isinstance(value, dict) for value in raw.values()):
            raise SleeperAPIError("Sleeper player catalog contained invalid player records")
        return raw  # type: ignore[return-value]

    def load_rosters(
        self,
        *,
        username: str,
        season: int,
        configured_leagues: Sequence[LeagueConfig] = (),
    ) -> tuple[SleeperLeagueRoster, ...]:
        """Discover leagues and return the user's interpreted roster in each one."""

        user = self.resolve_user(username)
        user_id = str(user["user_id"])
        discovered = {
            str(league["league_id"]): league for league in self.get_leagues(user_id, season)
        }
        selected = self._select_leagues(discovered, configured_leagues)
        catalog = self.get_player_catalog()

        results = []
        for league_summary, configured in selected:
            league_id = str(league_summary["league_id"])
            league = self.get_league(league_id)
            roster = self._find_user_roster(
                self.get_rosters(league_id),
                user_id=user_id,
                configured_roster_id=configured.roster_id if configured else None,
            )
            results.append(
                self._build_roster(
                    league=league,
                    roster=roster,
                    catalog=catalog,
                    nickname=(
                        configured.nickname
                        if configured
                        else str(league.get("name", league_id))
                    ),
                )
            )
        return tuple(results)

    def _select_leagues(
        self,
        discovered: Mapping[str, Mapping[str, Any]],
        configured: Sequence[LeagueConfig],
    ) -> list[tuple[Mapping[str, Any], LeagueConfig | None]]:
        if not configured:
            return [(league, None) for league in discovered.values()]

        missing = [league.id for league in configured if league.id not in discovered]
        if missing:
            raise SleeperAPIError(
                "Configured Sleeper league(s) were not discovered for this user and season: "
                + ", ".join(missing)
            )
        return [(discovered[league.id], league) for league in configured]

    @staticmethod
    def _find_user_roster(
        rosters: Sequence[Mapping[str, Any]],
        *,
        user_id: str,
        configured_roster_id: str | None,
    ) -> Mapping[str, Any]:
        owned = [
            roster
            for roster in rosters
            if str(roster.get("owner_id")) == user_id
            or user_id in {str(owner) for owner in roster.get("co_owners") or []}
        ]
        if configured_roster_id is not None:
            owned = [
                roster for roster in owned if str(roster.get("roster_id")) == configured_roster_id
            ]
        if len(owned) != 1:
            qualifier = f" matching roster {configured_roster_id}" if configured_roster_id else ""
            raise SleeperAPIError(
                f"Expected one owned Sleeper roster{qualifier}; found {len(owned)}"
            )
        return owned[0]

    def _build_roster(
        self,
        *,
        league: Mapping[str, Any],
        roster: Mapping[str, Any],
        catalog: Mapping[str, Mapping[str, Any]],
        nickname: str,
    ) -> SleeperLeagueRoster:
        roster_positions = tuple(str(slot) for slot in league.get("roster_positions") or [])
        starter_slots = tuple(slot for slot in roster_positions if slot not in NON_STARTER_SLOTS)
        player_ids = tuple(str(player_id) for player_id in roster.get("players") or [])
        raw_starters = tuple(str(player_id) for player_id in roster.get("starters") or [])
        starter_ids = {player_id for player_id in raw_starters if player_id != "0"}
        reserve_ids = {str(player_id) for player_id in roster.get("reserve") or []}
        taxi_ids = {str(player_id) for player_id in roster.get("taxi") or []}

        starters = tuple(
            self._build_player(
                player_id,
                catalog,
                lineup_slot=starter_slots[index] if index < len(starter_slots) else "STARTER",
                is_starter=True,
                reserve_ids=reserve_ids,
                taxi_ids=taxi_ids,
            )
            for index, player_id in enumerate(raw_starters)
            if player_id != "0"
        )
        bench = tuple(
            self._build_player(
                player_id,
                catalog,
                lineup_slot="BN",
                is_starter=False,
                reserve_ids=reserve_ids,
                taxi_ids=taxi_ids,
            )
            for player_id in player_ids
            if player_id not in starter_ids
        )

        return SleeperLeagueRoster(
            league_id=str(league["league_id"]),
            league_name=str(league.get("name") or league["league_id"]),
            nickname=nickname,
            roster_id=str(roster["roster_id"]),
            roster_positions=roster_positions,
            scoring_settings=league.get("scoring_settings") or {},
            starters=starters,
            bench=bench,
        )

    @staticmethod
    def _build_player(
        player_id: str,
        catalog: Mapping[str, Mapping[str, Any]],
        *,
        lineup_slot: str,
        is_starter: bool,
        reserve_ids: set[str],
        taxi_ids: set[str],
    ) -> SleeperPlayer:
        record = catalog.get(player_id, {})
        name = record.get("full_name") or " ".join(
            part for part in (record.get("first_name"), record.get("last_name")) if part
        )
        return SleeperPlayer(
            player_id=player_id,
            name=str(name or player_id),
            nfl_team=_optional_text(record.get("team")),
            position=_optional_text(record.get("position")),
            lineup_slot=lineup_slot,
            is_starter=is_starter,
            is_reserve=player_id in reserve_ids,
            is_taxi=player_id in taxi_ids,
        )

    def _get_mapping(self, path: str) -> Mapping[str, Any]:
        value = self._get_json(path)
        if not isinstance(value, dict):
            raise SleeperAPIError(f"Sleeper returned an invalid object for {path}")
        return value

    def _get_mapping_list(self, path: str) -> list[Mapping[str, Any]]:
        value = self._get_json(path)
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise SleeperAPIError(f"Sleeper returned an invalid list for {path}")
        return value

    def _get_json(self, path: str) -> Any:
        try:
            response = self._client.get(path)
            response.raise_for_status()
            value = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SleeperAPIError(f"Sleeper request failed for {path}: {exc}") from exc

        self.response_metadata[path] = SleeperResponseMetadata(
            retrieved_at=datetime.now(timezone.utc),
            response_date=_http_datetime(response.headers.get("date")),
            cache_age_seconds=_optional_int(response.headers.get("age")),
            etag=response.headers.get("etag"),
            cache_status=response.headers.get("cf-cache-status")
            or response.headers.get("x-cache"),
        )
        return value


def render_sleeper_rosters(rosters: Sequence[SleeperLeagueRoster]) -> str:
    """Render a compact diagnostic report for the ingestion milestone."""

    lines = [f"Sleeper leagues: {len(rosters)}"]
    for roster in rosters:
        lines.extend(("", f"{roster.nickname} ({roster.league_name})", "  Starters:"))
        lines.extend(f"    {_player_line(player)}" for player in roster.starters)
        lines.append("  Bench:")
        lines.extend(f"    {_player_line(player)}" for player in roster.bench)
    return "\n".join(lines)


def _player_line(player: SleeperPlayer) -> str:
    flags = []
    if player.is_reserve:
        flags.append("reserve")
    if player.is_taxi:
        flags.append("taxi")
    suffix = f" [{', '.join(flags)}]" if flags else ""
    team_position = "/".join(value for value in (player.nfl_team, player.position) if value)
    detail = f" ({team_position})" if team_position else ""
    return f"{player.lineup_slot}: {player.name}{detail}{suffix}"


def _optional_text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _optional_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _http_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        result = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
