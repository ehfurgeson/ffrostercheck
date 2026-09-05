"""Combine official status sources with explicit precedence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from app.models import (
    Confidence,
    FantasyPlayer,
    GameDayState,
    GameSourceReport,
    InjuryDesignation,
    NFLPlayerStatus,
    ReportState,
    RosterEligibility,
    SourceResult,
)
from app.nfl.identity import normalize_name, normalize_position, normalize_team


INACTIVES_SOURCE = "nfl_inactives"
INJURIES_SOURCE = "nfl_injuries"
DESIGNATION_SEVERITY = {
    InjuryDesignation.OUT: 4,
    InjuryDesignation.DOUBTFUL: 3,
    InjuryDesignation.QUESTIONABLE: 2,
    InjuryDesignation.UNKNOWN: 1,
    InjuryDesignation.NONE: 0,
}


@dataclass(frozen=True)
class StatusSubject:
    """One real NFL player who needs a unified status decision."""

    canonical_player_id: str
    name: str
    nfl_team: str | None
    position: str | None
    roster_eligibility: RosterEligibility = RosterEligibility.UNKNOWN


def subjects_from_fantasy_players(
    players: Sequence[FantasyPlayer],
    *,
    roster_eligibility: RosterEligibility = RosterEligibility.UNKNOWN,
) -> tuple[StatusSubject, ...]:
    """Deduplicate fantasy instances by canonical ID without guessing identities."""

    subjects: dict[str, StatusSubject] = {}
    for player in players:
        canonical_id = player.canonical_player_id
        if not canonical_id or canonical_id in subjects:
            continue
        subjects[canonical_id] = StatusSubject(
            canonical_player_id=canonical_id,
            name=player.name,
            nfl_team=player.nfl_team,
            position=player.position,
            roster_eligibility=roster_eligibility,
        )
    return tuple(subjects.values())


def subjects_from_source_reports(
    reports: Sequence[GameSourceReport],
) -> tuple[StatusSubject, ...]:
    """Build subjects from listed official rows when no roster is supplied."""

    subjects: dict[str, StatusSubject] = {}
    for report in reports:
        for result in report.player_results:
            if not result.player_name or not result.nfl_team:
                continue
            team = normalize_team(result.nfl_team)
            name_key = normalize_name(result.player_name)
            if not team or not name_key:
                continue
            canonical_id = f"listed:{team}:{name_key}"
            subjects.setdefault(
                canonical_id,
                StatusSubject(
                    canonical_player_id=canonical_id,
                    name=result.player_name,
                    nfl_team=team,
                    position=result.position,
                ),
            )
    return tuple(subjects.values())


def combine_official_statuses(
    subjects: Sequence[StatusSubject],
    reports: Sequence[GameSourceReport],
    *,
    decision_at: datetime,
) -> tuple[NFLPlayerStatus, ...]:
    """Produce one NFLPlayerStatus per subject, keeping every matched source row."""

    assignments, blocked_active = _assign_results(subjects, reports)
    return tuple(
        _combine_subject(
            subject,
            reports,
            assignments.get(subject.canonical_player_id, ()),
            decision_at,
            blocked_active=subject.canonical_player_id in blocked_active,
        )
        for subject in _unique_subjects(subjects)
    )


def render_player_statuses(
    statuses: Sequence[NFLPlayerStatus],
    reports: Sequence[GameSourceReport] = (),
) -> str:
    """Render unified statuses without treating unknown as healthy."""

    lines = [
        f"Official player statuses: {len(statuses)}",
        (
            "Game-day: "
            f"{_count(statuses, 'game_day_state', GameDayState.INACTIVE)} inactive, "
            f"{_count(statuses, 'game_day_state', GameDayState.ACTIVE)} active, "
            f"{_count(statuses, 'game_day_state', GameDayState.UNKNOWN)} unknown"
        ),
        (
            "Designations: "
            f"{_count(statuses, 'injury_designation', InjuryDesignation.OUT)} out, "
            f"{_count(statuses, 'injury_designation', InjuryDesignation.DOUBTFUL)} doubtful, "
            f"{_count(statuses, 'injury_designation', InjuryDesignation.QUESTIONABLE)} questionable, "
            f"{_count(statuses, 'injury_designation', InjuryDesignation.NONE)} none, "
            f"{_count(statuses, 'injury_designation', InjuryDesignation.UNKNOWN)} unknown"
        ),
    ]
    for report in reports:
        coverage = ", ".join(sorted(report.parsed_teams)) or "none"
        lines.append(
            f"  {report.source}: {report.report_state.value} (teams: {coverage})"
        )
        for error in report.errors:
            lines.append(f"    error: {error}")
    for status in statuses:
        identity = status.name or status.canonical_player_id
        position = f"{status.position} " if status.position else ""
        team = f" ({status.nfl_team})" if status.nfl_team else ""
        inactive = (
            "n/a"
            if status.official_inactive is None
            else str(status.official_inactive).lower()
        )
        lines.append(f"  {position}{identity}{team}")
        lines.append(
            f"    game_day={status.game_day_state.value} "
            f"eligibility={status.roster_eligibility.value} "
            f"designation={status.injury_designation.value}"
        )
        lines.append(
            f"    confidence={status.confidence.name.lower()} official_inactive={inactive}"
        )
        if status.injury_description:
            lines.append(f"    injury: {status.injury_description}")
        for result in status.source_results:
            designation = (
                result.injury_designation.value if result.injury_designation else "-"
            )
            game_day = result.game_day_state.value if result.game_day_state else "-"
            detail = f" — {result.detail}" if result.detail else ""
            lines.append(
                f"    {result.source} {result.report_state.value} "
                f"game_day={game_day} designation={designation}{detail}"
            )
    return "\n".join(lines)


def _unique_subjects(subjects: Sequence[StatusSubject]) -> tuple[StatusSubject, ...]:
    unique: dict[str, StatusSubject] = {}
    for subject in subjects:
        unique.setdefault(subject.canonical_player_id, subject)
    return tuple(unique.values())


def _assign_results(
    subjects: Sequence[StatusSubject],
    reports: Sequence[GameSourceReport],
) -> tuple[dict[str, tuple[SourceResult, ...]], frozenset[str]]:
    unique_subjects = _unique_subjects(subjects)
    assigned: dict[str, list[SourceResult]] = {
        subject.canonical_player_id: [] for subject in unique_subjects
    }
    blocked_active: set[str] = set()
    for report in reports:
        unmatched: list[SourceResult] = []
        for result in report.player_results:
            match = _unique_match(result, unique_subjects)
            if match is not None:
                assigned[match.canonical_player_id].append(result)
            else:
                unmatched.append(result)
        for subject in unique_subjects:
            if any(_name_team_match(subject, result) for result in unmatched):
                blocked_active.add(subject.canonical_player_id)
            if not any(
                result.source == report.source for result in assigned[subject.canonical_player_id]
            ):
                assigned[subject.canonical_player_id].append(_coverage_result(report, subject))
    return {key: tuple(values) for key, values in assigned.items()}, frozenset(blocked_active)


def _unique_match(
    result: SourceResult,
    subjects: Sequence[StatusSubject],
) -> StatusSubject | None:
    name_team_matches = [subject for subject in subjects if _name_team_match(subject, result)]
    if not name_team_matches:
        return None
    if len(name_team_matches) == 1:
        match = name_team_matches[0]
        return match if _compatible_position(match.position, result.position) else None
    positioned = [
        subject
        for subject in name_team_matches
        if _strict_position_match(subject.position, result.position)
    ]
    if len(positioned) == 1:
        return positioned[0]
    return None


def _name_team_match(subject: StatusSubject, result: SourceResult) -> bool:
    if not result.player_name or not subject.name:
        return False
    if normalize_name(subject.name) != normalize_name(result.player_name):
        return False
    subject_team = normalize_team(subject.nfl_team)
    result_team = normalize_team(result.nfl_team)
    return bool(subject_team and result_team and subject_team == result_team)


def _compatible_position(left: str | None, right: str | None) -> bool:
    normalized_left = normalize_position(left)
    normalized_right = normalize_position(right)
    if not normalized_left or not normalized_right:
        return True
    return normalized_left == normalized_right


def _strict_position_match(left: str | None, right: str | None) -> bool:
    normalized_left = normalize_position(left)
    normalized_right = normalize_position(right)
    return bool(normalized_left and normalized_right and normalized_left == normalized_right)


def _coverage_result(report: GameSourceReport, subject: StatusSubject) -> SourceResult:
    team = normalize_team(subject.nfl_team)
    if report.report_state is ReportState.FAILED:
        detail = "; ".join(report.errors) or "Official report failed"
    elif report.report_state is ReportState.NOT_YET_PUBLISHED:
        detail = "Official report is not yet published"
    elif report.report_state is ReportState.PARTIAL:
        if team and team in report.parsed_teams:
            detail = "Player was not listed on the parsed team table"
        else:
            missing = ", ".join(sorted(report.expected_teams - report.parsed_teams)) or "unknown"
            detail = f"Player's team was not in this partial report (missing {missing})"
    else:
        detail = "Player was not listed"
    return SourceResult(
        source=report.source,
        source_url=report.source_url,
        success=report.report_state is not ReportState.FAILED,
        report_state=report.report_state,
        retrieved_at=report.retrieved_at,
        detail=detail,
        player_name=subject.name,
        nfl_team=subject.nfl_team,
        position=subject.position,
        published_at=report.published_at,
        source_updated_at=report.source_updated_at,
        http_cache_age_seconds=report.http_cache_age_seconds,
        raw_content_hash=report.raw_content_hash,
    )


def _combine_subject(
    subject: StatusSubject,
    reports: Sequence[GameSourceReport],
    source_results: Sequence[SourceResult],
    decision_at: datetime,
    *,
    blocked_active: bool,
) -> NFLPlayerStatus:
    inactives_reports = _reports_named(reports, INACTIVES_SOURCE)
    injuries_reports = _reports_named(reports, INJURIES_SOURCE)
    inactive_rows = tuple(
        result
        for result in source_results
        if result.source == INACTIVES_SOURCE and result.game_day_state is GameDayState.INACTIVE
    )
    injury_rows = tuple(
        result
        for result in source_results
        if result.source == INJURIES_SOURCE and result.injury_designation is not None
    )
    game_day_state, official_inactive = _game_day_state(
        subject,
        inactives_reports,
        inactive_rows,
        blocked_active=blocked_active,
    )
    injury_designation, injury_description = _injury_decision(injuries_reports, injury_rows)
    return NFLPlayerStatus(
        canonical_player_id=subject.canonical_player_id,
        roster_eligibility=subject.roster_eligibility,
        game_day_state=game_day_state,
        injury_designation=injury_designation,
        confidence=_confidence(game_day_state, injury_designation),
        decision_at=decision_at,
        injury_description=injury_description,
        official_inactive=official_inactive,
        source_results=tuple(source_results),
        name=subject.name,
        nfl_team=subject.nfl_team,
        position=subject.position,
    )


def _game_day_state(
    subject: StatusSubject,
    inactives_reports: Sequence[GameSourceReport],
    inactive_rows: Sequence[SourceResult],
    *,
    blocked_active: bool,
) -> tuple[GameDayState, bool | None]:
    if inactive_rows:
        return GameDayState.INACTIVE, True
    if not inactives_reports:
        return GameDayState.UNKNOWN, None
    if blocked_active or any(
        report.report_state is not ReportState.COMPLETE for report in inactives_reports
    ):
        return GameDayState.UNKNOWN, None
    if subject.roster_eligibility is RosterEligibility.ELIGIBLE:
        return GameDayState.ACTIVE, False
    return GameDayState.UNKNOWN, False


def _injury_decision(
    injuries_reports: Sequence[GameSourceReport],
    injury_rows: Sequence[SourceResult],
) -> tuple[InjuryDesignation, str | None]:
    if injury_rows:
        chosen = max(injury_rows, key=lambda row: DESIGNATION_SEVERITY[row.injury_designation or InjuryDesignation.UNKNOWN])
        return chosen.injury_designation or InjuryDesignation.UNKNOWN, chosen.detail
    if not injuries_reports:
        return InjuryDesignation.UNKNOWN, None
    if any(report.report_state is not ReportState.COMPLETE for report in injuries_reports):
        return InjuryDesignation.UNKNOWN, None
    return InjuryDesignation.NONE, None


def _confidence(
    game_day_state: GameDayState,
    injury_designation: InjuryDesignation,
) -> Confidence:
    if game_day_state is not GameDayState.UNKNOWN or injury_designation is not InjuryDesignation.UNKNOWN:
        return Confidence.OFFICIAL
    return Confidence.LOW


def _reports_named(reports: Sequence[GameSourceReport], name: str) -> tuple[GameSourceReport, ...]:
    return tuple(report for report in reports if report.source == name)


def _count(statuses: Sequence[NFLPlayerStatus], field: str, value: object) -> int:
    return sum(1 for status in statuses if getattr(status, field) is value)
