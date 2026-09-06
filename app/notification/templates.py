"""Pure notification templates with no transport side effects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from html import escape
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from app.analysis.fantasy_status import (
    FantasyLeagueStatuses,
    LeaguePlayerStatus,
)
from app.analysis.replacements import (
    ReplacementCandidate,
    StarterReplacementOptions,
)
from app.models import (
    FantasyAlertSeverity,
    GameDayState,
    GameSourceReport,
    InjuryDesignation,
    OpportunityLevel,
    RosterEligibility,
)
from app.notification.sources import build_source_lines
from app.notification.timestamps import build_timestamp_lines


@dataclass(frozen=True)
class TextEmail:
    """A transport-neutral email subject and plain-text body."""

    subject: str
    body: str


@dataclass(frozen=True)
class HtmlEmail:
    """A transport-neutral email subject and HTML body."""

    subject: str
    body: str


def build_text_email(
    kickoff: datetime,
    leagues: Sequence[FantasyLeagueStatuses],
    replacement_options: Sequence[StarterReplacementOptions] = (),
    *,
    decision_at: datetime,
    source_reports: Sequence[GameSourceReport] = (),
    display_timezone: tzinfo = ZoneInfo("America/New_York"),
    minutes_before_kickoff: int = 5,
) -> TextEmail:
    """Render one kickoff-window alert, grouped by urgency and then league."""

    kickoff_label, subject = _email_header(
        kickoff,
        display_timezone,
        minutes_before_kickoff,
    )
    timestamp_lines = build_timestamp_lines(
        kickoff,
        decision_at,
        leagues,
        replacement_options,
        display_timezone,
        source_reports,
    )
    source_lines = build_source_lines(leagues, source_reports)
    options_by_starter = _index_replacement_options(replacement_options)
    lines = [f"{kickoff_label} GAMES"]

    sections = (
        (FantasyAlertSeverity.CRITICAL, "ACTION NEEDED"),
        (FantasyAlertSeverity.WARNING, "RISK"),
        (FantasyAlertSeverity.INFO, "BENCH NOTES"),
        (FantasyAlertSeverity.NORMAL, "NO ACTION"),
    )
    for severity, heading in sections:
        rendered_leagues = _render_severity_section(
            severity,
            leagues,
            options_by_starter,
        )
        if rendered_leagues:
            lines.extend(("", heading, "", rendered_leagues))

    league_summary = _render_league_summary(leagues)
    if league_summary:
        lines.extend(("", "LEAGUE SUMMARY", "", league_summary))

    lines.extend(("", "SOURCES AND FAILURES", "", "\n".join(source_lines)))

    lines.extend(
        (
            "",
            "TIMESTAMPS",
            "",
            "\n".join(timestamp_lines),
        )
    )

    return TextEmail(subject=subject, body="\n".join(lines))


def build_html_email(
    kickoff: datetime,
    leagues: Sequence[FantasyLeagueStatuses],
    replacement_options: Sequence[StarterReplacementOptions] = (),
    *,
    decision_at: datetime,
    source_reports: Sequence[GameSourceReport] = (),
    display_timezone: tzinfo = ZoneInfo("America/New_York"),
    minutes_before_kickoff: int = 5,
) -> HtmlEmail:
    """Render an HTML counterpart to the plain-text kickoff-window alert."""

    kickoff_label, subject = _email_header(
        kickoff,
        display_timezone,
        minutes_before_kickoff,
    )
    timestamp_lines = build_timestamp_lines(
        kickoff,
        decision_at,
        leagues,
        replacement_options,
        display_timezone,
        source_reports,
    )
    source_lines = build_source_lines(leagues, source_reports)
    options_by_starter = _index_replacement_options(replacement_options)
    sections = (
        (FantasyAlertSeverity.CRITICAL, "ACTION NEEDED", "#b91c1c"),
        (FantasyAlertSeverity.WARNING, "RISK", "#b45309"),
        (FantasyAlertSeverity.INFO, "BENCH NOTES", "#1d4ed8"),
        (FantasyAlertSeverity.NORMAL, "NO ACTION", "#15803d"),
    )
    rendered_sections = [
        section
        for severity, heading, color in sections
        if (
            section := _render_html_severity_section(
                severity,
                heading,
                color,
                leagues,
                options_by_starter,
            )
        )
    ]
    content = "".join(rendered_sections)
    league_summary = _render_html_league_summary(leagues)
    sources = _render_html_list_section("SOURCES AND FAILURES", source_lines)
    timestamps = _render_html_timestamps(timestamp_lines)
    return HtmlEmail(
        subject=subject,
        body=(
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{escape(subject)}</title></head>"
            '<body style="margin:0;background:#f3f4f6;color:#111827;'
            'font-family:Arial,sans-serif">'
            '<main style="max-width:680px;margin:0 auto;padding:24px 16px">'
            '<div style="background:#ffffff;border:1px solid #e5e7eb;'
            'border-radius:10px;padding:24px">'
            f'<h1 style="font-size:24px;margin:0 0 24px">{escape(kickoff_label)} GAMES</h1>'
            f"{content}{league_summary}{sources}{timestamps}</div></main></body></html>"
        ),
    )


def _email_header(
    kickoff: datetime,
    display_timezone: tzinfo,
    minutes_before_kickoff: int,
) -> tuple[str, str]:
    if kickoff.tzinfo is None or kickoff.utcoffset() is None:
        raise ValueError("kickoff must be timezone-aware")
    if minutes_before_kickoff < 0:
        raise ValueError("minutes_before_kickoff must be at least 0")

    local_kickoff = kickoff.astimezone(display_timezone)
    kickoff_label = local_kickoff.strftime("%I:%M %p %Z").lstrip("0")
    subject = f"Fantasy Check — {kickoff_label} kickoff in {minutes_before_kickoff} min"
    return kickoff_label, subject


def _render_severity_section(
    severity: FantasyAlertSeverity,
    leagues: Sequence[FantasyLeagueStatuses],
    options_by_starter: Mapping[tuple[str, str], StarterReplacementOptions],
) -> str:
    league_blocks: list[str] = []
    for league_statuses in leagues:
        players = tuple(
            item
            for item in league_statuses.players
            if item.severity is severity
            and (item.player.is_starter or severity is not FantasyAlertSeverity.NORMAL)
        )
        if not players:
            continue
        label = (
            f"{league_statuses.league.nickname} — "
            f"{league_statuses.league.platform.value.title()}"
        )
        player_blocks = [
            _render_player(
                player_status,
                options_by_starter.get(_starter_key(player_status)),
            )
            for player_status in players
        ]
        league_blocks.append("\n\n".join((label, *player_blocks)))
    return "\n\n".join(league_blocks)


def _render_html_severity_section(
    severity: FantasyAlertSeverity,
    heading: str,
    color: str,
    leagues: Sequence[FantasyLeagueStatuses],
    options_by_starter: Mapping[tuple[str, str], StarterReplacementOptions],
) -> str:
    league_blocks: list[str] = []
    for league_statuses in leagues:
        players = tuple(
            item
            for item in league_statuses.players
            if item.severity is severity
            and (item.player.is_starter or severity is not FantasyAlertSeverity.NORMAL)
        )
        if not players:
            continue
        label = (
            f"{league_statuses.league.nickname} — "
            f"{league_statuses.league.platform.value.title()}"
        )
        player_blocks = "".join(
            _render_html_player(
                player_status,
                options_by_starter.get(_starter_key(player_status)),
            )
            for player_status in players
        )
        league_blocks.append(
            '<section style="margin:0 0 20px">'
            f'<h3 style="font-size:16px;margin:0 0 8px">{escape(label)}</h3>'
            f"{player_blocks}</section>"
        )
    if not league_blocks:
        return ""
    return (
        '<section style="margin:0 0 28px">'
        f'<h2 style="color:{color};font-size:18px;margin:0 0 14px">{heading}</h2>'
        f"{''.join(league_blocks)}</section>"
    )


def _render_league_summary(leagues: Sequence[FantasyLeagueStatuses]) -> str:
    return "\n\n".join(
        "\n".join((league_statuses.league.nickname, *_league_summary_lines(league_statuses)))
        for league_statuses in leagues
    )


def _render_html_league_summary(leagues: Sequence[FantasyLeagueStatuses]) -> str:
    if not leagues:
        return ""
    summaries = "".join(
        '<section style="margin:0 0 14px">'
        f'<h3 style="font-size:16px;margin:0 0 6px">'
        f"{escape(league_statuses.league.nickname)}</h3>"
        '<ul style="margin:0;padding-left:20px">'
        + "".join(
            f"<li>{escape(line)}</li>" for line in _league_summary_lines(league_statuses)
        )
        + "</ul></section>"
        for league_statuses in leagues
    )
    return (
        '<section style="margin:0">'
        '<h2 style="font-size:18px;margin:0 0 14px">LEAGUE SUMMARY</h2>'
        f"{summaries}</section>"
    )


def _league_summary_lines(league_statuses: FantasyLeagueStatuses) -> tuple[str, ...]:
    critical_count = sum(
        item.player.is_starter and item.severity is FantasyAlertSeverity.CRITICAL
        for item in league_statuses.players
    )
    warning_count = sum(
        item.player.is_starter and item.severity is FantasyAlertSeverity.WARNING
        for item in league_statuses.players
    )
    if critical_count == 0 and warning_count == 0:
        return ("No starter issues",)
    return (
        _count_label(critical_count, "lineup issue"),
        _count_label(warning_count, "warning"),
    )


def _count_label(count: int, singular: str) -> str:
    suffix = "" if count == 1 else "s"
    return f"{count} {singular}{suffix}"


def _render_html_timestamps(
    lines: Sequence[str],
) -> str:
    return _render_html_list_section("TIMESTAMPS", lines, bordered=True)


def _render_html_list_section(
    heading: str,
    lines: Sequence[str],
    *,
    bordered: bool = False,
) -> str:
    items = "".join(f"<li>{escape(line)}</li>" for line in lines)
    border_style = (
        "border-top:1px solid #e5e7eb;margin:24px 0 0;padding:20px 0 0"
        if bordered
        else "margin:24px 0 0"
    )
    return (
        f'<section style="{border_style}">'
        f'<h2 style="font-size:18px;margin:0 0 10px">{escape(heading)}</h2>'
        f'<ul style="margin:0;padding-left:20px">{items}</ul></section>'
    )


def _render_html_player(
    player_status: LeaguePlayerStatus,
    replacements: StarterReplacementOptions | None,
) -> str:
    player = player_status.player
    lineup_state = "STARTING" if player.is_starter else "BENCH"
    position = f" ({player.position})" if player.position else ""
    statuses = "".join(f"<li>{escape(line)}</li>" for line in _status_lines(player_status))
    replacements_html = ""
    if replacements is not None:
        if replacements.candidates:
            candidates = "".join(
                f"<li>{escape(_render_candidate(candidate))}</li>"
                for candidate in replacements.candidates
            )
            replacements_html = (
                '<p style="font-weight:bold;margin:12px 0 6px">Suggested replacements:</p>'
                f'<ol style="margin:0;padding-left:24px">{candidates}</ol>'
            )
        else:
            replacements_html = (
                '<p style="font-weight:bold;margin:12px 0 6px">Suggested replacements:</p>'
                '<p style="margin:0">None verified and unlocked.</p>'
            )
    return (
        '<article style="border-left:4px solid #d1d5db;margin:0 0 12px;padding:8px 12px">'
        f'<p style="font-weight:bold;margin:0 0 6px">{escape(player.name)} — '
        f"{lineup_state}{escape(position)}</p>"
        f'<ul style="margin:0;padding-left:20px">{statuses}</ul>'
        f"{replacements_html}</article>"
    )


def _render_player(
    player_status: LeaguePlayerStatus,
    replacements: StarterReplacementOptions | None,
) -> str:
    player = player_status.player
    lineup_state = "STARTING" if player.is_starter else "BENCH"
    position = f" ({player.position})" if player.position else ""
    lines = [f"{player.name} — {lineup_state}{position}"]
    lines.extend(_status_lines(player_status))

    if replacements is not None:
        lines.append("Suggested replacements:")
        if replacements.candidates:
            lines.extend(
                f"{index}. {_render_candidate(candidate)}"
                for index, candidate in enumerate(replacements.candidates, start=1)
            )
        else:
            lines.append("None verified and unlocked.")
    return "\n".join(lines)


def _status_lines(player_status: LeaguePlayerStatus) -> list[str]:
    status = player_status.status
    if status is None:
        return ["STATUS UNKNOWN — NFL status unavailable"]

    lines: list[str] = []
    if status.roster_eligibility is RosterEligibility.INELIGIBLE:
        lines.append("ROSTER INELIGIBLE")
    elif status.roster_eligibility is RosterEligibility.UNKNOWN:
        lines.append("Roster eligibility unknown")

    if status.game_day_state is GameDayState.INACTIVE or status.official_inactive is True:
        inactive_label = "OFFICIALLY INACTIVE"
        if status.injury_description and status.injury_designation is InjuryDesignation.NONE:
            inactive_label = f"{inactive_label} — {status.injury_description}"
        lines.append(inactive_label)
    elif status.game_day_state is GameDayState.ACTIVE:
        lines.append("Active")
    else:
        lines.append("Game-day status unknown")

    if status.injury_designation is not InjuryDesignation.NONE:
        designation = status.injury_designation.value.replace("_", " ").title()
        if status.injury_description:
            designation = f"{designation} — {status.injury_description}"
        lines.append(designation)
    return lines


def _render_candidate(candidate: ReplacementCandidate) -> str:
    player = candidate.player_status.player
    status = candidate.player_status.status
    assert status is not None
    identity = " — ".join(
        value for value in (player.name, player.position, player.nfl_team) if value
    )
    health = "Active"
    if status.injury_designation is InjuryDesignation.QUESTIONABLE:
        health = "Active; Questionable"
    opportunity = _render_opportunity(candidate)
    return f"{identity} — {health}{opportunity}"


def _render_opportunity(candidate: ReplacementCandidate) -> str:
    if candidate.opportunity is None:
        return ""
    label = {
        OpportunityLevel.PROMOTED: "promoted in same depth slot",
        OpportunityLevel.ROLE_BOOST: "depth-chart role boost",
        OpportunityLevel.POSITIONAL_OPPORTUNITY: "positional opportunity",
    }[candidate.opportunity.level]
    return f"; {label}"


def _index_replacement_options(
    options: Sequence[StarterReplacementOptions],
) -> Mapping[tuple[str, str], StarterReplacementOptions]:
    indexed: dict[tuple[str, str], StarterReplacementOptions] = {}
    for item in options:
        key = _starter_key(item.starter)
        if key in indexed:
            raise ValueError(
                "duplicate replacement options for fantasy player "
                f"{item.starter.player.platform_player_id!r} in league "
                f"{item.starter.player.league_id!r}"
            )
        indexed[key] = item
    return indexed


def _starter_key(player_status: LeaguePlayerStatus) -> tuple[str, str]:
    player = player_status.player
    return player.league_id, player.platform_player_id
