"""ESPN fantasy-football API ingestion and roster interpretation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Mapping, Sequence

import httpx


ESPN_API_URL = "https://lm-api-reads.fantasy.espn.com"
ESPN_VIEWS = ("mTeam", "mRoster", "mSettings")
BENCH_SLOT_ID = 20
INJURED_RESERVE_SLOT_ID = 21

LINEUP_SLOT_NAMES = {
    0: "QB",
    2: "RB",
    3: "RB/WR",
    4: "WR",
    5: "WR/TE",
    6: "TE",
    7: "OP",
    16: "D/ST",
    17: "K",
    20: "BE",
    21: "IR",
    23: "FLEX",
}

DEFAULT_POSITION_NAMES = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    16: "D/ST",
}

PRO_TEAM_ABBREVIATIONS = {
    1: "ATL",
    2: "BUF",
    3: "CHI",
    4: "CIN",
    5: "CLE",
    6: "DAL",
    7: "DEN",
    8: "DET",
    9: "GB",
    10: "TEN",
    11: "IND",
    12: "KC",
    13: "LV",
    14: "LAR",
    15: "MIA",
    16: "MIN",
    17: "NE",
    18: "NO",
    19: "NYG",
    20: "NYJ",
    21: "PHI",
    22: "ARI",
    23: "PIT",
    24: "LAC",
    25: "SF",
    26: "SEA",
    27: "TB",
    28: "WAS",
    29: "CAR",
    30: "JAX",
    33: "BAL",
    34: "HOU",
}


class ESPNAPIError(RuntimeError):
    """Raised when ESPN cannot provide a valid fantasy response."""


@dataclass(frozen=True)
class ESPNResponseMetadata:
    retrieved_at: datetime
    response_date: datetime | None
    cache_age_seconds: int | None
    etag: str | None


@dataclass(frozen=True)
class ESPNPlayer:
    player_id: str
    name: str
    nfl_team: str | None
    position: str | None
    lineup_slot: str
    lineup_slot_id: int
    eligible_slot_ids: tuple[int, ...]
    injury_status: str | None
    is_starter: bool
    is_reserve: bool = False


@dataclass(frozen=True)
class ESPNLeagueRoster:
    league_id: str
    league_name: str
    nickname: str
    team_id: str
    team_name: str
    roster_rules: Mapping[str, Any]
    scoring_settings: Mapping[str, Any]
    starters: tuple[ESPNPlayer, ...]
    bench: tuple[ESPNPlayer, ...]

    @property
    def players(self) -> tuple[ESPNPlayer, ...]:
        return self.starters + self.bench


class ESPNClient:
    """Small synchronous client for ESPN's current fantasy read API."""

    def __init__(
        self,
        *,
        swid: str | None = None,
        espn_s2: str | None = None,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        if bool(swid) != bool(espn_s2):
            raise ESPNAPIError("ESPN_SWID and ESPN_S2 must be provided together")
        self._swid = swid
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            base_url=ESPN_API_URL,
            timeout=timeout_seconds,
            follow_redirects=True,
            cookies={"SWID": swid or "", "espn_s2": espn_s2 or ""},
            headers={"User-Agent": "fantasy-watchdog/0.1"},
        )
        self.response_metadata: ESPNResponseMetadata | None = None

    def __enter__(self) -> ESPNClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch_league(self, *, season: int, league_id: str) -> Mapping[str, Any]:
        path = f"/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}"
        try:
            response = self._client.get(path, params=[("view", view) for view in ESPN_VIEWS])
        except httpx.HTTPError as exc:
            raise ESPNAPIError(f"ESPN request failed: {exc}") from exc

        if response.status_code in {401, 403}:
            raise ESPNAPIError(
                "ESPN authentication failed; verify ESPN_SWID and ESPN_S2 for this league"
            )
        if response.status_code == 404:
            raise ESPNAPIError(
                f"ESPN league {league_id} was not found for season {season}; verify both values"
            )
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ESPNAPIError(f"ESPN request failed: {exc}") from exc

        content_type = response.headers.get("content-type", "").lower()
        if "text/html" in content_type:
            raise ESPNAPIError(
                "ESPN returned an HTML page instead of league data; authentication may be invalid"
            )
        try:
            raw = response.json()
        except ValueError as exc:
            raise ESPNAPIError("ESPN returned a non-JSON response") from exc
        if not isinstance(raw, dict):
            raise ESPNAPIError("ESPN returned an invalid league object")
        self._validate_league_shape(raw)
        self.response_metadata = ESPNResponseMetadata(
            retrieved_at=datetime.now(timezone.utc),
            response_date=_http_datetime(response.headers.get("date")),
            cache_age_seconds=_optional_int(response.headers.get("age")),
            etag=response.headers.get("etag"),
        )
        return raw

    def load_roster(
        self,
        *,
        season: int,
        league_id: str,
        team_id: str | None = None,
        nickname: str = "ESPN",
    ) -> ESPNLeagueRoster:
        """Fetch a league and interpret one explicitly or authentically owned team."""

        league = self.fetch_league(season=season, league_id=league_id)
        team = self._select_team(league["teams"], team_id=team_id, owner_swid=self._swid)
        return self._build_roster(league, team, nickname=nickname)

    @staticmethod
    def _validate_league_shape(league: Mapping[str, Any]) -> None:
        teams = league.get("teams")
        settings = league.get("settings")
        if not isinstance(teams, list) or not teams:
            raise ESPNAPIError("ESPN response is missing teams; requested views may be invalid")
        if not isinstance(settings, dict):
            raise ESPNAPIError("ESPN response is missing settings; requested views may be invalid")
        for team in teams:
            entries = team.get("roster", {}).get("entries") if isinstance(team, dict) else None
            if not isinstance(entries, list):
                raise ESPNAPIError(
                    "ESPN response is missing teams[].roster.entries; mRoster was not returned"
                )

    @staticmethod
    def _select_team(
        teams: Sequence[Mapping[str, Any]],
        *,
        team_id: str | None,
        owner_swid: str | None,
    ) -> Mapping[str, Any]:
        if team_id:
            matches = [team for team in teams if str(team.get("id")) == team_id]
            if len(matches) != 1:
                raise ESPNAPIError(f"Expected ESPN team {team_id}; found {len(matches)} matches")
            return matches[0]

        if owner_swid:
            owner_id = _normalize_owner_id(owner_swid)
            matches = [
                team
                for team in teams
                if owner_id
                in {
                    _normalize_owner_id(str(owner))
                    for owner in [team.get("primaryOwner"), *(team.get("owners") or [])]
                    if owner
                }
            ]
            if len(matches) == 1:
                return matches[0]

        if len(teams) == 1:
            return teams[0]
        raise ESPNAPIError(
            "Unable to identify your ESPN team; configure team_id or provide private-league SWID"
        )

    @staticmethod
    def _build_roster(
        league: Mapping[str, Any],
        team: Mapping[str, Any],
        *,
        nickname: str,
    ) -> ESPNLeagueRoster:
        settings = league["settings"]
        entries = team["roster"]["entries"]
        players = tuple(_parse_player(entry) for entry in entries)
        starters = tuple(player for player in players if player.is_starter)
        bench = tuple(player for player in players if not player.is_starter)
        team_name = " ".join(
            part for part in (team.get("location"), team.get("nickname")) if part
        ) or str(team.get("name") or team["id"])
        return ESPNLeagueRoster(
            league_id=str(league.get("id") or league.get("leagueId")),
            league_name=str(settings.get("name") or league.get("name") or league.get("id")),
            nickname=nickname,
            team_id=str(team["id"]),
            team_name=team_name,
            roster_rules=settings.get("rosterSettings") or {},
            scoring_settings=settings.get("scoringSettings") or {},
            starters=starters,
            bench=bench,
        )


def render_espn_roster(roster: ESPNLeagueRoster) -> str:
    """Render a compact diagnostic report for the ESPN ingestion milestone."""

    lines = [
        f"{roster.nickname} ({roster.league_name})",
        f"  Team: {roster.team_name} (ID {roster.team_id})",
        "  Starters:",
    ]
    lines.extend(f"    {_player_line(player)}" for player in roster.starters)
    lines.append("  Bench:")
    lines.extend(f"    {_player_line(player)}" for player in roster.bench)
    return "\n".join(lines)


def _parse_player(entry: Mapping[str, Any]) -> ESPNPlayer:
    try:
        lineup_slot_id = int(entry["lineupSlotId"])
        player = entry["playerPoolEntry"]["player"]
        player_id = str(player.get("id") or entry["playerId"])
        name = str(player["fullName"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ESPNAPIError("ESPN roster contains an invalid player entry") from exc

    pro_team_id = _optional_int_value(player.get("proTeamId"))
    position_id = _optional_int_value(player.get("defaultPositionId"))
    eligible_slots = player.get("eligibleSlots") or []
    if not isinstance(eligible_slots, list):
        raise ESPNAPIError(f"ESPN player {player_id} has invalid eligibleSlots")
    try:
        eligible_slot_ids = tuple(int(slot) for slot in eligible_slots)
    except (TypeError, ValueError) as exc:
        raise ESPNAPIError(f"ESPN player {player_id} has invalid eligibleSlots") from exc

    return ESPNPlayer(
        player_id=player_id,
        name=name,
        nfl_team=PRO_TEAM_ABBREVIATIONS.get(pro_team_id),
        position=DEFAULT_POSITION_NAMES.get(position_id),
        lineup_slot=LINEUP_SLOT_NAMES.get(lineup_slot_id, f"SLOT_{lineup_slot_id}"),
        lineup_slot_id=lineup_slot_id,
        eligible_slot_ids=eligible_slot_ids,
        injury_status=_optional_text(player.get("injuryStatus")),
        is_starter=lineup_slot_id not in {BENCH_SLOT_ID, INJURED_RESERVE_SLOT_ID},
        is_reserve=lineup_slot_id == INJURED_RESERVE_SLOT_ID,
    )


def _player_line(player: ESPNPlayer) -> str:
    team_position = "/".join(value for value in (player.nfl_team, player.position) if value)
    detail = f" ({team_position})" if team_position else ""
    injury = f" [{player.injury_status}]" if player.injury_status else ""
    return f"{player.lineup_slot}: {player.name}{detail}{injury}"


def _normalize_owner_id(value: str) -> str:
    return value.strip().strip("{}").lower()


def _optional_text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _optional_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _optional_int_value(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _http_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        result = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
