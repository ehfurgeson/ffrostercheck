"""Sleeper player-catalog status fallback. Never treats catalog ``active`` as game-day active."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

import httpx

from app.fantasy.sleeper import SLEEPER_API_URL, SleeperAPIError, SleeperClient
from app.models import (
    GameSourceReport,
    InjuryDesignation,
    RelevantGame,
    ReportState,
    RosterEligibility,
    SourceResult,
)
from app.nfl.identity import TEAM_DEFENSE_POSITIONS, normalize_position, normalize_team


SOURCE_NAME = "sleeper_status"
SOURCE_URL = f"{SLEEPER_API_URL}/players/nfl"
CATALOG_PATH = "/players/nfl"

INJURY_STATUS_MAP = {
    "questionable": InjuryDesignation.QUESTIONABLE,
    "q": InjuryDesignation.QUESTIONABLE,
    "doubtful": InjuryDesignation.DOUBTFUL,
    "d": InjuryDesignation.DOUBTFUL,
    "out": InjuryDesignation.OUT,
    "o": InjuryDesignation.OUT,
    "ir": InjuryDesignation.OUT,
    "pup": InjuryDesignation.OUT,
    "nfi": InjuryDesignation.OUT,
    "sus": InjuryDesignation.OUT,
    "susp": InjuryDesignation.OUT,
    "suspended": InjuryDesignation.OUT,
    "suspension": InjuryDesignation.OUT,
}

INELIGIBLE_TOKENS = frozenset(
    {
        "ir",
        "injuredreserve",
        "reserveinjured",
        "pup",
        "physicallyunabletoperform",
        "reservepup",
        "nfi",
        "nonfootballinjury",
        "reservenfi",
        "sus",
        "susp",
        "suspended",
        "suspension",
        "reservesus",
        "reservesuspended",
        "exempt",
        "commissionerexempt",
        "reserveexempt",
    }
)


class SleeperStatusSource:
    """Structured Sleeper fallback. Catalog ``active`` is not a game-day declaration."""

    name = SOURCE_NAME
    priority = 50

    def __init__(
        self,
        *,
        client: SleeperClient | None = None,
        http_client: httpx.Client | None = None,
        catalog: Mapping[str, Mapping[str, Any]] | None = None,
        retrieved_at: datetime | None = None,
        http_cache_age_seconds: int | None = None,
        source_url: str = SOURCE_URL,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._catalog = catalog
        self._injected_retrieved_at = retrieved_at
        self._injected_cache_age = http_cache_age_seconds
        self._source_url = source_url
        self._owns_client = client is None and catalog is None
        self._client = client
        if self._owns_client:
            self._client = SleeperClient(
                http_client=http_client,
                timeout_seconds=timeout_seconds,
            )

    def __enter__(self) -> SleeperStatusSource:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()

    def fetch_game(self, game: RelevantGame) -> GameSourceReport:
        expected = frozenset(
            team
            for team in (normalize_team(game.home_team), normalize_team(game.away_team))
            if team
        )
        try:
            catalog, retrieved_at, cache_age = self._load_catalog()
        except SleeperAPIError as exc:
            retrieved_at = datetime.now(timezone.utc)
            return GameSourceReport(
                source=self.name,
                game_id=game.game_id,
                report_state=ReportState.FAILED,
                expected_teams=expected,
                parsed_teams=frozenset(),
                player_results=(),
                retrieved_at=retrieved_at,
                errors=(str(exc),),
                source_url=self._source_url,
            )

        if not catalog:
            return GameSourceReport(
                source=self.name,
                game_id=game.game_id,
                report_state=ReportState.FAILED,
                expected_teams=expected,
                parsed_teams=frozenset(),
                player_results=(),
                retrieved_at=retrieved_at,
                errors=("Sleeper player catalog was empty",),
                source_url=self._source_url,
                http_cache_age_seconds=cache_age,
                raw_content_hash=_content_hash(catalog),
            )

        content_hash = _content_hash(catalog)

        parsed_teams, player_results = _results_for_game(
            catalog,
            expected_teams=expected,
            retrieved_at=retrieved_at,
            source_url=self._source_url,
            http_cache_age_seconds=cache_age,
            raw_content_hash=content_hash,
        )
        if parsed_teams == expected:
            report_state = ReportState.COMPLETE
            errors: tuple[str, ...] = ()
        elif parsed_teams:
            report_state = ReportState.PARTIAL
            missing = ", ".join(sorted(expected - parsed_teams))
            errors = (f"Sleeper catalog missing team(s): {missing}",)
        else:
            report_state = ReportState.FAILED
            expected_label = ", ".join(sorted(expected)) or "none"
            errors = (f"Sleeper catalog had no players for expected teams: {expected_label}",)
        return GameSourceReport(
            source=self.name,
            game_id=game.game_id,
            report_state=report_state,
            expected_teams=expected,
            parsed_teams=parsed_teams,
            player_results=player_results,
            retrieved_at=retrieved_at,
            errors=errors,
            source_url=self._source_url,
            http_cache_age_seconds=cache_age,
            raw_content_hash=content_hash,
        )

    def _load_catalog(
        self,
    ) -> tuple[Mapping[str, Mapping[str, Any]], datetime, int | None]:
        if self._catalog is not None:
            retrieved_at = self._injected_retrieved_at or datetime.now(timezone.utc)
            return self._catalog, retrieved_at, self._injected_cache_age
        if self._client is None:
            raise SleeperAPIError("Sleeper status source has no client or catalog")
        catalog = self._client.get_player_catalog()
        metadata = self._client.response_metadata.get(CATALOG_PATH)
        retrieved_at = metadata.retrieved_at if metadata else datetime.now(timezone.utc)
        cache_age = metadata.cache_age_seconds if metadata else None
        return catalog, retrieved_at, cache_age


def normalize_sleeper_injury_status(value: str | None) -> InjuryDesignation:
    """Map known Sleeper injury_status values; unmapped strings become UNKNOWN."""

    if value is None or not str(value).strip():
        return InjuryDesignation.NONE
    mapped = INJURY_STATUS_MAP.get(_token(value))
    return mapped if mapped is not None else InjuryDesignation.UNKNOWN


def normalize_sleeper_eligibility(
    *,
    status: str | None,
    injury_status: str | None,
) -> RosterEligibility:
    """IR/PUP/NFI/suspended/exempt are ineligible. Catalog ``active`` is ignored."""

    if _is_ineligible(status) or _is_ineligible(injury_status):
        return RosterEligibility.INELIGIBLE
    if status is not None and _token(status) == "active":
        return RosterEligibility.ELIGIBLE
    return RosterEligibility.UNKNOWN


def render_sleeper_status_report(report: GameSourceReport) -> str:
    lines = [
        f"Sleeper status: {report.report_state.value}",
        f"Game: {report.game_id}",
        f"Expected teams: {', '.join(sorted(report.expected_teams)) or 'none'}",
        f"Parsed teams: {', '.join(sorted(report.parsed_teams)) or 'none'}",
        "Catalog active is not game-day active",
    ]
    if report.source_url:
        lines.append(f"Source: {report.source_url}")
    if report.http_cache_age_seconds is not None:
        lines.append(f"HTTP cache age: {report.http_cache_age_seconds} seconds")
    for result in report.player_results:
        identity = result.player_name or "Unknown player"
        position = f"{result.position} " if result.position else ""
        team = f" ({result.nfl_team})" if result.nfl_team else ""
        designation = (
            result.injury_designation.value if result.injury_designation else "unknown"
        )
        eligibility = (
            result.roster_eligibility.value if result.roster_eligibility else "unknown"
        )
        game_day = result.game_day_state.value if result.game_day_state else "unset"
        lines.append(
            f"  {designation.upper()}: {position}{identity}{team} "
            f"eligibility={eligibility} game_day={game_day}"
        )
        if result.detail:
            lines.append(f"    {result.detail}")
    if not report.player_results:
        lines.append("  No Sleeper catalog players were treated as game-day active")
    for error in report.errors:
        lines.append(f"  error: {error}")
    return "\n".join(lines)


def _results_for_game(
    catalog: Mapping[str, Mapping[str, Any]],
    *,
    expected_teams: frozenset[str],
    retrieved_at: datetime,
    source_url: str,
    http_cache_age_seconds: int | None,
    raw_content_hash: str | None,
) -> tuple[frozenset[str], tuple[SourceResult, ...]]:
    parsed_teams: set[str] = set()
    results: list[SourceResult] = []
    for player_id, record in catalog.items():
        if not isinstance(record, Mapping):
            continue
        team = normalize_team(_optional_text(record.get("team")))
        if team is None or team not in expected_teams:
            continue
        parsed_teams.add(team)
        if _is_team_defense(record):
            continue
        results.append(
            _player_result(
                player_id,
                record,
                team=team,
                retrieved_at=retrieved_at,
                source_url=source_url,
                http_cache_age_seconds=http_cache_age_seconds,
                raw_content_hash=raw_content_hash,
            )
        )
    results.sort(key=lambda result: (result.nfl_team or "", result.player_name or ""))
    return frozenset(parsed_teams), tuple(results)


def _player_result(
    player_id: str,
    record: Mapping[str, Any],
    *,
    team: str,
    retrieved_at: datetime,
    source_url: str,
    http_cache_age_seconds: int | None,
    raw_content_hash: str | None,
) -> SourceResult:
    status = _optional_text(record.get("status"))
    injury_status = _optional_text(record.get("injury_status"))
    catalog_active = record.get("active")
    designation = normalize_sleeper_injury_status(injury_status)
    eligibility = normalize_sleeper_eligibility(
        status=status, injury_status=injury_status
    )
    return SourceResult(
        source=SOURCE_NAME,
        source_url=source_url,
        success=True,
        report_state=ReportState.COMPLETE,
        retrieved_at=retrieved_at,
        roster_eligibility=eligibility,
        game_day_state=None,
        injury_designation=designation,
        detail=_detail(
            player_id=player_id,
            status=status,
            injury_status=injury_status,
            catalog_active=catalog_active,
            injury_notes=_optional_text(record.get("injury_notes"))
            or _optional_text(record.get("injury_body")),
            practice_participation=_optional_text(record.get("practice_participation")),
        ),
        player_name=_player_name(player_id, record),
        nfl_team=team,
        position=normalize_position(_optional_text(record.get("position"))),
        http_cache_age_seconds=http_cache_age_seconds,
        raw_content_hash=raw_content_hash,
    )


def _detail(
    *,
    player_id: str,
    status: str | None,
    injury_status: str | None,
    catalog_active: object,
    injury_notes: str | None,
    practice_participation: str | None,
) -> str:
    parts = [f"sleeper_id={player_id}"]
    if status:
        parts.append(f"status={status}")
    if injury_status:
        parts.append(f"injury_status={injury_status}")
    if isinstance(catalog_active, bool):
        parts.append(f"catalog_active={str(catalog_active).lower()}")
        parts.append("catalog_active_is_not_game_day_active")
    if injury_notes:
        parts.append(injury_notes)
    if practice_participation:
        parts.append(f"practice_participation={practice_participation}")
    return "; ".join(parts)


def _player_name(player_id: str, record: Mapping[str, Any]) -> str:
    name = record.get("full_name") or " ".join(
        part for part in (record.get("first_name"), record.get("last_name")) if part
    )
    return str(name or player_id)


def _is_team_defense(record: Mapping[str, Any]) -> bool:
    position = normalize_position(_optional_text(record.get("position")))
    return position in {"DST"} or (
        _optional_text(record.get("position")) or ""
    ).strip().upper() in TEAM_DEFENSE_POSITIONS


def _is_ineligible(value: str | None) -> bool:
    token = _token(value)
    return bool(token) and token in INELIGIBLE_TOKENS


def _token(value: str | None) -> str:
    if value is None:
        return ""
    return "".join(character for character in value.casefold() if character.isalnum())


def _optional_text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _content_hash(catalog: Mapping[str, Mapping[str, Any]]) -> str:
    payload = json.dumps(catalog, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
