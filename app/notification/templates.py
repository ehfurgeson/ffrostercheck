"""Pure notification templates with no transport side effects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
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
    InjuryDesignation,
    OpportunityLevel,
    RosterEligibility,
)


@dataclass(frozen=True)
class TextEmail:
    """A transport-neutral email subject and plain-text body."""

    subject: str
    body: str


def build_text_email(
    kickoff: datetime,
    leagues: Sequence[FantasyLeagueStatuses],
    replacement_options: Sequence[StarterReplacementOptions] = (),
    *,
    display_timezone: tzinfo = ZoneInfo("America/New_York"),
    minutes_before_kickoff: int = 5,
) -> TextEmail:
    """Render one kickoff-window alert, grouped by urgency and then league."""

    if kickoff.tzinfo is None or kickoff.utcoffset() is None:
        raise ValueError("kickoff must be timezone-aware")
    if minutes_before_kickoff < 0:
        raise ValueError("minutes_before_kickoff must be at least 0")

    local_kickoff = kickoff.astimezone(display_timezone)
    kickoff_label = local_kickoff.strftime("%I:%M %p %Z").lstrip("0")
    subject = f"Fantasy Check — {kickoff_label} kickoff in {minutes_before_kickoff} min"
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

    return TextEmail(subject=subject, body="\n".join(lines))


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
