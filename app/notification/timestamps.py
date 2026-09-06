"""Timestamp summaries shared by plain-text and HTML notifications."""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import Iterable, Sequence

from app.analysis.fantasy_status import FantasyLeagueStatuses
from app.analysis.replacements import StarterReplacementOptions
from app.models import SourceResult


def build_timestamp_lines(
    kickoff: datetime,
    decision_at: datetime,
    leagues: Sequence[FantasyLeagueStatuses],
    replacement_options: Sequence[StarterReplacementOptions],
    display_timezone: tzinfo,
) -> tuple[str, ...]:
    """Build concise, display-timezone metadata without hiding source age."""

    decision_at = _require_aware(decision_at, "decision_at")
    kickoff = _require_aware(kickoff, "kickoff")
    _validate_status_decision_times(leagues, decision_at)
    lines = [
        f"Decision time: {_format_timestamp(decision_at, display_timezone)}",
        f"Kickoff: {_format_timestamp(kickoff, display_timezone)}",
    ]
    for source, results in _source_results_by_source(leagues):
        parts: list[str] = []
        published_at = _latest_timestamp(
            (result.published_at for result in results),
            f"{source}.published_at",
        )
        source_updated_at = _latest_timestamp(
            (result.source_updated_at for result in results),
            f"{source}.source_updated_at",
        )
        retrieved_at = _latest_timestamp(
            (result.retrieved_at for result in results),
            f"{source}.retrieved_at",
        )
        cache_ages = [
            result.http_cache_age_seconds
            for result in results
            if result.http_cache_age_seconds is not None
        ]
        if published_at is not None:
            parts.append(f"published {_format_timestamp(published_at, display_timezone)}")
        if source_updated_at is not None:
            parts.append(f"updated {_format_timestamp(source_updated_at, display_timezone)}")
        assert retrieved_at is not None
        parts.append(f"retrieved {_format_timestamp(retrieved_at, display_timezone)}")
        if cache_ages:
            parts.append(f"HTTP cache age {_duration_label(max(cache_ages))}")
        lines.append(f"{_source_label(source)}: {'; '.join(parts)}")

    for depth_chart_as_of in _depth_chart_timestamps(replacement_options):
        lines.append(
            f"Depth chart snapshot: {_format_timestamp(depth_chart_as_of, display_timezone)}"
        )
    return tuple(lines)


def _source_results_by_source(
    leagues: Sequence[FantasyLeagueStatuses],
) -> tuple[tuple[str, tuple[SourceResult, ...]], ...]:
    grouped: dict[str, set[SourceResult]] = {}
    for league in leagues:
        for player_status in league.players:
            if player_status.status is None:
                continue
            for result in player_status.status.source_results:
                grouped.setdefault(result.source, set()).add(result)
    return tuple(
        (source, tuple(grouped[source]))
        for source in sorted(grouped, key=lambda value: (_source_label(value), value))
    )


def _depth_chart_timestamps(
    replacement_options: Sequence[StarterReplacementOptions],
) -> tuple[datetime, ...]:
    timestamps = {
        _require_aware(candidate.opportunity.depth_chart_as_of, "depth_chart_as_of")
        for options in replacement_options
        for candidate in options.candidates
        if candidate.opportunity is not None
    }
    return tuple(sorted(timestamps))


def _latest_timestamp(
    values: Iterable[datetime | None],
    field: str,
) -> datetime | None:
    timestamps = [
        _require_aware(value, field)
        for value in values
        if value is not None
    ]
    return max(timestamps) if timestamps else None


def _format_timestamp(value: datetime, display_timezone: tzinfo) -> str:
    local = value.astimezone(display_timezone)
    return (
        f"{local.strftime('%b')} {local.day}, {local.year} "
        f"{local.strftime('%I:%M:%S %p %Z').lstrip('0')}"
    )


def _duration_label(seconds: int) -> str:
    suffix = "" if seconds == 1 else "s"
    return f"{seconds} second{suffix}"


def _source_label(source: str) -> str:
    return {
        "nfl_inactives": "NFL official inactives",
        "nfl_injuries": "NFL injury report",
        "nflverse_status": "nflverse injury fallback",
        "official_team": "Official team context",
        "sleeper_status": "Sleeper status fallback",
    }.get(source, source.replace("_", " ").strip().title())


def _validate_status_decision_times(
    leagues: Sequence[FantasyLeagueStatuses],
    decision_at: datetime,
) -> None:
    for league in leagues:
        for player_status in league.players:
            status = player_status.status
            if status is not None and status.decision_at != decision_at:
                raise ValueError(
                    "status decision_at must match the email decision_at "
                    f"for canonical player {status.canonical_player_id!r}"
                )


def _require_aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value
