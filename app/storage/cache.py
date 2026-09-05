"""Persist T−90 official status snapshots without treating them as origin-fresh."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.analysis.availability import (
    StatusSubject,
    combine_official_statuses,
    subjects_from_source_reports,
)
from app.models import (
    Confidence,
    GameDayState,
    GameSourceReport,
    InjuryDesignation,
    NFLPlayerStatus,
    ReportState,
    RosterEligibility,
    SourceResult,
)


SNAPSHOT_NAME_RE = re.compile(r"^(?P<stamp>\d{8}T\d{6}Z)\.json$")
SAFE_GAME_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")


class StatusCacheError(ValueError):
    """Raised when a status snapshot is missing, corrupt, or used unsafely."""


class StatusFreshness(str, Enum):
    ORIGIN_FRESH = "origin_fresh"
    CACHED = "cached"
    CACHED_AFTER_FAILED_REFRESH = "cached_after_failed_refresh"


@dataclass(frozen=True)
class CachedStatusSnapshot:
    """One on-disk T−90 snapshot. Loading it never makes the data origin-fresh."""

    game_id: str
    cached_at: datetime
    reports: tuple[GameSourceReport, ...]
    statuses: tuple[NFLPlayerStatus, ...]

    @property
    def origin_fresh(self) -> bool:
        return False


@dataclass(frozen=True)
class StatusResolution:
    """T−5 lookup result, with cache use and origin freshness recorded separately."""

    game_id: str
    reports: tuple[GameSourceReport, ...]
    statuses: tuple[NFLPlayerStatus, ...]
    decision_at: datetime
    freshness: StatusFreshness
    refresh_attempted: bool
    used_cache: bool
    cache_age_seconds: int | None = None
    cached_at: datetime | None = None
    errors: tuple[str, ...] = ()

    @property
    def origin_fresh(self) -> bool:
        return self.freshness is StatusFreshness.ORIGIN_FRESH


class StatusCache:
    """JSON snapshots under ``cache/status/{game_id}/{timestamp}.json``."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.status_dir = self.root / "status"

    def save_prefetch(
        self,
        *,
        game_id: str,
        reports: Sequence[GameSourceReport],
        statuses: Sequence[NFLPlayerStatus],
        cached_at: datetime,
    ) -> CachedStatusSnapshot:
        """Store T−90 source results and unified statuses for one game."""

        snapshot = CachedStatusSnapshot(
            game_id=game_id,
            cached_at=_require_aware(cached_at, "cached_at"),
            reports=tuple(reports),
            statuses=tuple(statuses),
        )
        path = self._snapshot_path(game_id, snapshot.cached_at)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_snapshot_to_json(snapshot), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return snapshot

    def load_latest(self, game_id: str) -> CachedStatusSnapshot | None:
        """Return the newest snapshot for a game. Cached data is never origin-fresh."""

        directory = self._game_dir(game_id)
        if not directory.is_dir():
            return None
        snapshots = [
            self._read_snapshot(path)
            for path in directory.glob("*.json")
            if SNAPSHOT_NAME_RE.match(path.name)
        ]
        if not snapshots:
            return None
        return max(snapshots, key=lambda snapshot: snapshot.cached_at)

    def resolve_final(
        self,
        *,
        game_id: str,
        fetch_reports: Callable[[], Sequence[GameSourceReport]],
        subjects: Sequence[StatusSubject] = (),
        decision_at: datetime,
        require_refresh: bool = True,
    ) -> StatusResolution:
        """Attempt a T−5 refresh. Complete cached reports are not origin-fresh without it."""

        decision_at = _require_aware(decision_at, "decision_at")
        if not require_refresh:
            raise StatusCacheError(
                "Final status lookup requires a refresh attempt; "
                "cached complete reports are not origin-fresh"
            )

        live_reports, fetch_error = _attempt_refresh(fetch_reports)
        if live_reports is not None and not _refresh_failed(live_reports):
            chosen_subjects = _resolution_subjects(subjects, live_reports)
            return StatusResolution(
                game_id=game_id,
                reports=live_reports,
                statuses=combine_official_statuses(
                    chosen_subjects, live_reports, decision_at=decision_at
                ),
                decision_at=decision_at,
                freshness=StatusFreshness.ORIGIN_FRESH,
                refresh_attempted=True,
                used_cache=False,
                errors=() if fetch_error is None else (fetch_error,),
            )

        cached = self.load_latest(game_id)
        if cached is None:
            if live_reports is None:
                raise StatusCacheError(
                    f"Official refresh failed for {game_id} and no cached snapshot exists"
                    + (f": {fetch_error}" if fetch_error else "")
                )
            chosen_subjects = _resolution_subjects(subjects, live_reports)
            return StatusResolution(
                game_id=game_id,
                reports=live_reports,
                statuses=combine_official_statuses(
                    chosen_subjects, live_reports, decision_at=decision_at
                ),
                decision_at=decision_at,
                freshness=StatusFreshness.ORIGIN_FRESH,
                refresh_attempted=True,
                used_cache=False,
                errors=_resolution_errors(live_reports, fetch_error),
            )

        errors = list(_resolution_errors(live_reports, fetch_error))
        errors.append(
            "Using cached official snapshot; complete cached reports are not origin-fresh"
        )
        chosen_subjects = _resolution_subjects(subjects, cached.reports)
        return StatusResolution(
            game_id=game_id,
            reports=cached.reports,
            statuses=combine_official_statuses(
                chosen_subjects,
                cached.reports,
                decision_at=decision_at,
                origin_fresh=False,
            ),
            decision_at=decision_at,
            freshness=StatusFreshness.CACHED_AFTER_FAILED_REFRESH,
            refresh_attempted=True,
            used_cache=True,
            cache_age_seconds=_cache_age_seconds(decision_at, cached.cached_at),
            cached_at=cached.cached_at,
            errors=tuple(errors),
        )

    def _game_dir(self, game_id: str) -> Path:
        return self.status_dir / _safe_game_id(game_id)

    def _snapshot_path(self, game_id: str, cached_at: datetime) -> Path:
        stamp = _require_aware(cached_at, "cached_at").astimezone(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        return self._game_dir(game_id) / f"{stamp}.json"

    def _read_snapshot(self, path: Path) -> CachedStatusSnapshot:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StatusCacheError(f"Corrupt status snapshot {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise StatusCacheError(f"Corrupt status snapshot {path}: expected an object")
        try:
            return _snapshot_from_json(raw)
        except (KeyError, TypeError, ValueError) as exc:
            raise StatusCacheError(f"Corrupt status snapshot {path}: {exc}") from exc


def render_cached_snapshot(snapshot: CachedStatusSnapshot, *, path: Path | None = None) -> str:
    lines = [
        f"Cached status: {snapshot.game_id}",
        f"Cached at: {_format_datetime(snapshot.cached_at)}",
        "Origin fresh: no",
        f"Reports: {len(snapshot.reports)}",
        f"Statuses: {len(snapshot.statuses)}",
    ]
    if path is not None:
        lines.append(f"Path: {path}")
    for report in snapshot.reports:
        lines.append(
            f"  {report.source}: {report.report_state.value} "
            f"retrieved={_format_datetime(report.retrieved_at)}"
            + (
                f" published={_format_datetime(report.published_at)}"
                if report.published_at
                else ""
            )
            + (
                f" cache_age={report.http_cache_age_seconds}s"
                if report.http_cache_age_seconds is not None
                else ""
            )
        )
    return "\n".join(lines)


def render_status_resolution(resolution: StatusResolution) -> str:
    lines = [
        f"Status lookup: {resolution.game_id}",
        f"Freshness: {resolution.freshness.value}",
        f"Origin fresh: {'yes' if resolution.origin_fresh else 'no'}",
        f"Refresh attempted: {'yes' if resolution.refresh_attempted else 'no'}",
        f"Used cache: {'yes' if resolution.used_cache else 'no'}",
        f"Decision: {_format_datetime(resolution.decision_at)}",
    ]
    if resolution.cached_at is not None:
        lines.append(f"Cached at: {_format_datetime(resolution.cached_at)}")
    if resolution.cache_age_seconds is not None:
        lines.append(f"Local cache age: {resolution.cache_age_seconds} seconds")
    for report in resolution.reports:
        lines.append(
            f"  {report.source}: {report.report_state.value} "
            f"retrieved={_format_datetime(report.retrieved_at)}"
            + (
                f" published={_format_datetime(report.published_at)}"
                if report.published_at
                else ""
            )
            + (
                f" updated={_format_datetime(report.source_updated_at)}"
                if report.source_updated_at
                else ""
            )
            + (
                f" http_cache_age={report.http_cache_age_seconds}s"
                if report.http_cache_age_seconds is not None
                else ""
            )
        )
    for error in resolution.errors:
        lines.append(f"  error: {error}")
    return "\n".join(lines)


def _resolution_subjects(
    subjects: Sequence[StatusSubject],
    reports: Sequence[GameSourceReport],
) -> tuple[StatusSubject, ...]:
    if subjects:
        return tuple(subjects)
    return subjects_from_source_reports(reports)


def _attempt_refresh(
    fetch_reports: Callable[[], Sequence[GameSourceReport]],
) -> tuple[tuple[GameSourceReport, ...] | None, str | None]:
    try:
        return tuple(fetch_reports()), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _refresh_failed(reports: Sequence[GameSourceReport]) -> bool:
    return not reports or all(report.report_state is ReportState.FAILED for report in reports)


def _resolution_errors(
    reports: Sequence[GameSourceReport] | None,
    fetch_error: str | None,
) -> tuple[str, ...]:
    errors: list[str] = []
    if fetch_error:
        errors.append(fetch_error)
    if reports:
        for report in reports:
            errors.extend(f"{report.source}: {error}" for error in report.errors)
            if report.report_state is ReportState.FAILED and not report.errors:
                errors.append(f"{report.source}: official refresh failed")
    return tuple(errors)


def _cache_age_seconds(decision_at: datetime, cached_at: datetime) -> int:
    return max(0, int((decision_at - cached_at).total_seconds()))


def _safe_game_id(game_id: str) -> str:
    cleaned = SAFE_GAME_ID_RE.sub("_", game_id.strip())
    if not cleaned:
        raise StatusCacheError("game_id must not be empty")
    return cleaned


def _snapshot_to_json(snapshot: CachedStatusSnapshot) -> dict[str, Any]:
    return {
        "game_id": snapshot.game_id,
        "cached_at": _format_datetime(snapshot.cached_at),
        "reports": [_report_to_json(report) for report in snapshot.reports],
        "statuses": [_status_to_json(status) for status in snapshot.statuses],
    }


def _snapshot_from_json(data: Mapping[str, Any]) -> CachedStatusSnapshot:
    return CachedStatusSnapshot(
        game_id=_string(data, "game_id"),
        cached_at=_require_datetime(data, "cached_at"),
        reports=tuple(_report_from_json(item) for item in _list(data, "reports")),
        statuses=tuple(_status_from_json(item) for item in _list(data, "statuses")),
    )


def _report_to_json(report: GameSourceReport) -> dict[str, Any]:
    return {
        "source": report.source,
        "game_id": report.game_id,
        "report_state": report.report_state.value,
        "expected_teams": sorted(report.expected_teams),
        "parsed_teams": sorted(report.parsed_teams),
        "player_results": [_result_to_json(result) for result in report.player_results],
        "retrieved_at": _format_datetime(report.retrieved_at),
        "source_updated_at": _optional_datetime(report.source_updated_at),
        "errors": list(report.errors),
        "source_url": report.source_url,
        "published_at": _optional_datetime(report.published_at),
        "http_cache_age_seconds": report.http_cache_age_seconds,
        "raw_content_hash": report.raw_content_hash,
    }


def _report_from_json(data: Any) -> GameSourceReport:
    payload = _mapping(data, "report")
    return GameSourceReport(
        source=_string(payload, "source"),
        game_id=_string(payload, "game_id"),
        report_state=ReportState(_string(payload, "report_state")),
        expected_teams=frozenset(_string_list(payload, "expected_teams")),
        parsed_teams=frozenset(_string_list(payload, "parsed_teams")),
        player_results=tuple(_result_from_json(item) for item in _list(payload, "player_results")),
        retrieved_at=_require_datetime(payload, "retrieved_at"),
        source_updated_at=_optional_datetime_field(payload, "source_updated_at"),
        errors=tuple(_string_list(payload, "errors")),
        source_url=_optional_string(payload, "source_url"),
        published_at=_optional_datetime_field(payload, "published_at"),
        http_cache_age_seconds=_optional_int(payload, "http_cache_age_seconds"),
        raw_content_hash=_optional_string(payload, "raw_content_hash"),
    )


def _result_to_json(result: SourceResult) -> dict[str, Any]:
    return {
        "source": result.source,
        "source_url": result.source_url,
        "success": result.success,
        "report_state": result.report_state.value,
        "retrieved_at": _format_datetime(result.retrieved_at),
        "roster_eligibility": _enum_value(result.roster_eligibility),
        "game_day_state": _enum_value(result.game_day_state),
        "injury_designation": _enum_value(result.injury_designation),
        "detail": result.detail,
        "player_name": result.player_name,
        "nfl_team": result.nfl_team,
        "position": result.position,
        "published_at": _optional_datetime(result.published_at),
        "source_updated_at": _optional_datetime(result.source_updated_at),
        "http_cache_age_seconds": result.http_cache_age_seconds,
        "raw_content_hash": result.raw_content_hash,
    }


def _result_from_json(data: Any) -> SourceResult:
    payload = _mapping(data, "source result")
    return SourceResult(
        source=_string(payload, "source"),
        source_url=_optional_string(payload, "source_url"),
        success=_boolean(payload, "success"),
        report_state=ReportState(_string(payload, "report_state")),
        retrieved_at=_require_datetime(payload, "retrieved_at"),
        roster_eligibility=_optional_enum(payload, "roster_eligibility", RosterEligibility),
        game_day_state=_optional_enum(payload, "game_day_state", GameDayState),
        injury_designation=_optional_enum(payload, "injury_designation", InjuryDesignation),
        detail=_optional_string(payload, "detail"),
        player_name=_optional_string(payload, "player_name"),
        nfl_team=_optional_string(payload, "nfl_team"),
        position=_optional_string(payload, "position"),
        published_at=_optional_datetime_field(payload, "published_at"),
        source_updated_at=_optional_datetime_field(payload, "source_updated_at"),
        http_cache_age_seconds=_optional_int(payload, "http_cache_age_seconds"),
        raw_content_hash=_optional_string(payload, "raw_content_hash"),
    )


def _status_to_json(status: NFLPlayerStatus) -> dict[str, Any]:
    return {
        "canonical_player_id": status.canonical_player_id,
        "roster_eligibility": status.roster_eligibility.value,
        "game_day_state": status.game_day_state.value,
        "injury_designation": status.injury_designation.value,
        "confidence": status.confidence.name.lower(),
        "decision_at": _format_datetime(status.decision_at),
        "injury_description": status.injury_description,
        "official_inactive": status.official_inactive,
        "source_results": [_result_to_json(result) for result in status.source_results],
        "name": status.name,
        "nfl_team": status.nfl_team,
        "position": status.position,
    }


def _status_from_json(data: Any) -> NFLPlayerStatus:
    payload = _mapping(data, "status")
    return NFLPlayerStatus(
        canonical_player_id=_string(payload, "canonical_player_id"),
        roster_eligibility=RosterEligibility(_string(payload, "roster_eligibility")),
        game_day_state=GameDayState(_string(payload, "game_day_state")),
        injury_designation=InjuryDesignation(_string(payload, "injury_designation")),
        confidence=_confidence(payload.get("confidence")),
        decision_at=_require_datetime(payload, "decision_at"),
        injury_description=_optional_string(payload, "injury_description"),
        official_inactive=_optional_bool(payload, "official_inactive"),
        source_results=tuple(_result_from_json(item) for item in _list(payload, "source_results")),
        name=_optional_string(payload, "name"),
        nfl_team=_optional_string(payload, "nfl_team"),
        position=_optional_string(payload, "position"),
    )


def _confidence(value: Any) -> Confidence:
    if isinstance(value, str):
        try:
            return Confidence[value.upper()]
        except KeyError as exc:
            raise ValueError(f"Unknown confidence: {value}") from exc
    if isinstance(value, int) and not isinstance(value, bool):
        return Confidence(value)
    raise ValueError("confidence must be an official/high/medium/low name or integer")


def _format_datetime(value: datetime) -> str:
    return _require_aware(value, "datetime").astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _optional_datetime(value: datetime | None) -> str | None:
    return None if value is None else _format_datetime(value)


def _require_datetime(data: Mapping[str, Any], key: str) -> datetime:
    parsed = _parse_datetime(data.get(key))
    if parsed is None:
        raise ValueError(f"{key} must be a timezone-aware timestamp")
    return parsed


def _optional_datetime_field(data: Mapping[str, Any], key: str) -> datetime | None:
    value = data.get(key)
    if value is None:
        return None
    parsed = _parse_datetime(value)
    if parsed is None:
        raise ValueError(f"{key} must be a timezone-aware timestamp")
    return parsed


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        raise ValueError("naive datetime is not allowed")
    return parsed.astimezone(timezone.utc)


def _require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None:
        raise StatusCacheError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _enum_value(value: Enum | None) -> str | None:
    return None if value is None else value.value


def _optional_enum(data: Mapping[str, Any], key: str, enum: type[Enum]):
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return enum(value)


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be an object")
    return value


def _list(data: Mapping[str, Any], key: str) -> list[Any]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return value


def _string_list(data: Mapping[str, Any], key: str) -> list[str]:
    values = _list(data, key)
    if any(not isinstance(item, str) for item in values):
        raise ValueError(f"{key} must be a list of strings")
    return values


def _string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_string(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _boolean(data: Mapping[str, Any], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be true or false")
    return value


def _optional_bool(data: Mapping[str, Any], key: str) -> bool | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be true, false, or null")
    return value


def _optional_int(data: Mapping[str, Any], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value
