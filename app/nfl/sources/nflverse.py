"""nflverse weekly injury fallback. Unavailable seasons fail at the source, not the app."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.models import (
    GameSourceReport,
    InjuryDesignation,
    RelevantGame,
    ReportState,
    SourceResult,
)
from app.nfl.identity import normalize_position, normalize_team
from app.nfl.nflverse import (
    DatasetLoad,
    DatasetState,
    NFLVerseLoadError,
    NFLVerseSource,
)


SOURCE_NAME = "nflverse_status"
INJURY_RELEASE_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{season}.parquet"
)

REPORT_STATUS_MAP = {
    "questionable": InjuryDesignation.QUESTIONABLE,
    "q": InjuryDesignation.QUESTIONABLE,
    "doubtful": InjuryDesignation.DOUBTFUL,
    "d": InjuryDesignation.DOUBTFUL,
    "out": InjuryDesignation.OUT,
    "o": InjuryDesignation.OUT,
    "ir": InjuryDesignation.OUT,
    "injuredreserve": InjuryDesignation.OUT,
    "pup": InjuryDesignation.OUT,
    "nfi": InjuryDesignation.OUT,
    "sus": InjuryDesignation.OUT,
    "suspended": InjuryDesignation.OUT,
}


class NFLVerseStatusSource:
    """Structured nflverse injury fallback. Never infers game-day active."""

    name = SOURCE_NAME
    priority = 40

    def __init__(
        self,
        *,
        season: int,
        week: int,
        nflverse: NFLVerseSource | None = None,
        dataset: DatasetLoad | None = None,
        rows: Sequence[Mapping[str, Any]] | None = None,
        retrieved_at: datetime | None = None,
        source_url: str | None = None,
    ) -> None:
        self.season = season
        self.week = week
        self._nflverse = nflverse
        self._dataset = dataset
        self._rows = None if rows is None else tuple(rows)
        self._injected_retrieved_at = retrieved_at
        self._source_url = source_url or INJURY_RELEASE_URL.format(season=season)

    def __enter__(self) -> NFLVerseStatusSource:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def fetch_game(self, game: RelevantGame) -> GameSourceReport:
        expected = frozenset(
            team
            for team in (normalize_team(game.home_team), normalize_team(game.away_team))
            if team
        )
        retrieved_at = self._injected_retrieved_at or datetime.now(timezone.utc)
        try:
            rows, retrieved_at, load_detail = self._load_rows(retrieved_at)
        except NFLVerseLoadError as exc:
            return _failed_report(
                game.game_id,
                expected=expected,
                retrieved_at=retrieved_at,
                source_url=self._source_url,
                error=str(exc),
            )

        if rows is None:
            return _failed_report(
                game.game_id,
                expected=expected,
                retrieved_at=retrieved_at,
                source_url=self._source_url,
                error=load_detail or f"nflverse injuries are unavailable for season {self.season}",
            )

        content_hash = _content_hash(rows)
        week_rows = tuple(
            row
            for row in rows
            if _row_season(row) == self.season
            and _row_week(row) == self.week
            and _is_regular_season(row)
        )
        if not week_rows:
            return GameSourceReport(
                source=self.name,
                game_id=game.game_id,
                report_state=ReportState.NOT_YET_PUBLISHED,
                expected_teams=expected,
                parsed_teams=frozenset(),
                player_results=(),
                retrieved_at=retrieved_at,
                errors=(f"No nflverse injury rows for season {self.season} week {self.week}",),
                source_url=self._source_url,
                raw_content_hash=content_hash,
            )

        parsed_teams, player_results, source_updated_at = _results_for_game(
            week_rows,
            expected_teams=expected,
            retrieved_at=retrieved_at,
            source_url=self._source_url,
            raw_content_hash=content_hash,
        )
        return GameSourceReport(
            source=self.name,
            game_id=game.game_id,
            report_state=ReportState.COMPLETE,
            expected_teams=expected,
            parsed_teams=parsed_teams,
            player_results=player_results,
            retrieved_at=retrieved_at,
            source_updated_at=source_updated_at,
            source_url=self._source_url,
            raw_content_hash=content_hash,
        )

    def _load_rows(
        self,
        retrieved_at: datetime,
    ) -> tuple[tuple[Mapping[str, Any], ...] | None, datetime, str | None]:
        if self._rows is not None:
            return self._rows, retrieved_at, None
        dataset = self._dataset
        if dataset is None:
            loader = self._nflverse or NFLVerseSource()
            dataset = loader.load_injuries(self.season)
        if dataset.state is not DatasetState.AVAILABLE or dataset.frame is None:
            return None, retrieved_at, dataset.detail
        return tuple(dataset.frame.iter_rows(named=True)), retrieved_at, dataset.detail


def normalize_nflverse_report_status(value: str | None) -> InjuryDesignation:
    """Map known nflverse report_status values; unmapped strings become UNKNOWN."""

    if value is None or not str(value).strip():
        return InjuryDesignation.NONE
    mapped = REPORT_STATUS_MAP.get(_token(value))
    return mapped if mapped is not None else InjuryDesignation.UNKNOWN


def render_nflverse_status_report(report: GameSourceReport) -> str:
    lines = [
        f"nflverse status: {report.report_state.value}",
        f"Game: {report.game_id}",
        f"Expected teams: {', '.join(sorted(report.expected_teams)) or 'none'}",
        f"Parsed teams: {', '.join(sorted(report.parsed_teams)) or 'none'}",
        "nflverse injuries never set game-day active",
    ]
    if report.source_url:
        lines.append(f"Source: {report.source_url}")
    if report.source_updated_at is not None:
        lines.append(f"Source updated: {report.source_updated_at.isoformat()}")
    for result in report.player_results:
        identity = result.player_name or result.canonical_player_id or "Unknown player"
        position = f"{result.position} " if result.position else ""
        team = f" ({result.nfl_team})" if result.nfl_team else ""
        designation = (
            result.injury_designation.value if result.injury_designation else "unknown"
        )
        game_day = result.game_day_state.value if result.game_day_state else "unset"
        lines.append(
            f"  {designation.upper()}: {position}{identity}{team} game_day={game_day}"
        )
        if result.detail:
            lines.append(f"    {result.detail}")
    if not report.player_results:
        lines.append("  No nflverse injury rows were treated as game-day active")
    for error in report.errors:
        lines.append(f"  error: {error}")
    return "\n".join(lines)


def _results_for_game(
    week_rows: Sequence[Mapping[str, Any]],
    *,
    expected_teams: frozenset[str],
    retrieved_at: datetime,
    source_url: str,
    raw_content_hash: str | None,
) -> tuple[frozenset[str], tuple[SourceResult, ...], datetime | None]:
    parsed_teams: set[str] = set()
    results: list[SourceResult] = []
    updated_at: datetime | None = None
    for row in week_rows:
        team = normalize_team(_optional_text(row.get("team")))
        if team is None or team not in expected_teams:
            continue
        parsed_teams.add(team)
        result = _player_result(
            row,
            team=team,
            retrieved_at=retrieved_at,
            source_url=source_url,
            raw_content_hash=raw_content_hash,
        )
        results.append(result)
        if result.source_updated_at is not None and (
            updated_at is None or result.source_updated_at > updated_at
        ):
            updated_at = result.source_updated_at
    results.sort(key=lambda result: (result.nfl_team or "", result.player_name or ""))
    return frozenset(parsed_teams), tuple(results), updated_at


def _player_result(
    row: Mapping[str, Any],
    *,
    team: str,
    retrieved_at: datetime,
    source_url: str,
    raw_content_hash: str | None,
) -> SourceResult:
    report_status = _optional_text(row.get("report_status"))
    gsis_id = _optional_text(row.get("gsis_id"))
    return SourceResult(
        source=SOURCE_NAME,
        source_url=source_url,
        success=True,
        report_state=ReportState.COMPLETE,
        retrieved_at=retrieved_at,
        game_day_state=None,
        injury_designation=normalize_nflverse_report_status(report_status),
        detail=_detail(row, gsis_id=gsis_id, report_status=report_status),
        player_name=_player_name(row),
        nfl_team=team,
        position=normalize_position(_optional_text(row.get("position"))),
        canonical_player_id=gsis_id,
        source_updated_at=_parse_timestamp(row.get("date_modified")),
        raw_content_hash=raw_content_hash,
    )


def _detail(
    row: Mapping[str, Any],
    *,
    gsis_id: str | None,
    report_status: str | None,
) -> str:
    parts = []
    if gsis_id:
        parts.append(f"gsis_id={gsis_id}")
    if report_status:
        parts.append(f"report_status={report_status}")
    injury = _optional_text(row.get("report_primary_injury"))
    secondary = _optional_text(row.get("report_secondary_injury"))
    if injury and secondary:
        parts.append(f"{injury}; {secondary}")
    elif injury:
        parts.append(injury)
    practice = _optional_text(row.get("practice_status"))
    if practice:
        parts.append(f"practice_status={practice}")
    parts.append("nflverse_does_not_set_game_day_active")
    return "; ".join(parts)


def _player_name(row: Mapping[str, Any]) -> str:
    name = _optional_text(row.get("full_name"))
    if name:
        return name
    combined = " ".join(
        part
        for part in (
            _optional_text(row.get("first_name")),
            _optional_text(row.get("last_name")),
        )
        if part
    )
    return combined or _optional_text(row.get("gsis_id")) or "Unknown player"


def _failed_report(
    game_id: str,
    *,
    expected: frozenset[str],
    retrieved_at: datetime,
    source_url: str,
    error: str,
) -> GameSourceReport:
    return GameSourceReport(
        source=SOURCE_NAME,
        game_id=game_id,
        report_state=ReportState.FAILED,
        expected_teams=expected,
        parsed_teams=frozenset(),
        player_results=(),
        retrieved_at=retrieved_at,
        errors=(error,),
        source_url=source_url,
    )


def _row_season(row: Mapping[str, Any]) -> int | None:
    return _as_int(row.get("season"))


def _row_week(row: Mapping[str, Any]) -> int | None:
    return _as_int(row.get("week"))


def _is_regular_season(row: Mapping[str, Any]) -> bool:
    season_type = _optional_text(row.get("season_type"))
    return season_type is None or season_type.casefold() in {"reg", "regular"}


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _token(value: str | None) -> str:
    if value is None:
        return ""
    return "".join(character for character in value.casefold() if character.isalnum())


def _optional_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _content_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps(list(rows), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
