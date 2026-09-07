"""Structured JSON logging with secret redaction.

Game-day jobs emit one JSON object per line. Sensitive credential keys are
always redacted; authenticated request headers are never accepted as fields.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

from app.models import GameSourceReport, NFLPlayerStatus, ReportState


LOGGER_NAME = "fantasy_watchdog"
SENSITIVE_KEY_FRAGMENTS = (
    "espn_s2",
    "swid",
    "smtp_app_password",
    "password",
    "authorization",
    "cookie",
    "secret",
    "token",
)
REDACTED = "[REDACTED]"


class JsonLogFormatter(logging.Formatter):
    """Render each log record as one JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, Mapping):
            payload.update(redact_mapping(fields))
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, default=_json_default)


def configure_structured_logging(
    *,
    level: int = logging.INFO,
    stream=None,
) -> logging.Logger:
    """Configure the package logger for one-line JSON events on stderr."""

    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.setLevel(level)
    logger.propagate = False
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonLogFormatter())
    handler.setLevel(level)
    logger.addHandler(handler)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the package logger or a child logger under it."""

    if name is None or name == LOGGER_NAME:
        return logging.getLogger(LOGGER_NAME)
    if name.startswith(f"{LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def emit(
    event: str,
    *,
    level: int = logging.INFO,
    logger: logging.Logger | None = None,
    **fields: Any,
) -> None:
    """Emit one structured event with redacted fields."""

    target = logger or get_logger()
    if not target.handlers:
        # Keep unit tests and library callers quiet unless configured.
        target.addHandler(logging.NullHandler())
    target.log(
        level,
        event,
        extra={"event": event, "fields": redact_mapping(fields)},
    )


def redact_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe copy with sensitive keys replaced."""

    return {key: _redact_value(key, value) for key, value in values.items()}


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)


def source_report_fields(report: GameSourceReport) -> dict[str, Any]:
    """Flatten one source report for structured logs."""

    return {
        "source": report.source,
        "game_id": report.game_id,
        "report_state": report.report_state.value,
        "success": report.report_state is not ReportState.FAILED,
        "expected_teams": sorted(report.expected_teams),
        "parsed_teams": sorted(report.parsed_teams),
        "published_at": _iso(report.published_at),
        "source_updated_at": _iso(report.source_updated_at),
        "retrieved_at": _iso(report.retrieved_at),
        "http_cache_age_seconds": report.http_cache_age_seconds,
        "error_count": len(report.errors),
    }


def player_status_fields(status: NFLPlayerStatus) -> dict[str, Any]:
    """Flatten one unified player status for structured logs."""

    return {
        "canonical_player_id": status.canonical_player_id,
        "roster_eligibility": status.roster_eligibility.value,
        "game_day_state": status.game_day_state.value,
        "injury_designation": status.injury_designation.value,
        "confidence": int(status.confidence),
        "official_inactive": status.official_inactive,
        "decision_at": _iso(status.decision_at),
        "nfl_team": status.nfl_team,
        "position": status.position,
        "source_count": len(status.source_results),
    }


def _redact_value(key: str, value: Any) -> Any:
    if is_sensitive_key(key):
        return REDACTED
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, (list, tuple)):
        return [_redact_value(key, item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, frozenset):
        return sorted(value)
    return value


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("structured log timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return str(value)
