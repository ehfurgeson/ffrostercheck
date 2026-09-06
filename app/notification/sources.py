"""Source coverage and failure summaries for kickoff notifications."""

from __future__ import annotations

from collections import Counter
from typing import Sequence

from app.analysis.fantasy_status import FantasyLeagueStatuses
from app.models import GameSourceReport, ReportState, SourceResult
from app.notification.timestamps import source_label


def build_source_lines(
    leagues: Sequence[FantasyLeagueStatuses],
    source_reports: Sequence[GameSourceReport] = (),
) -> tuple[str, ...]:
    """Summarize checked sources and disclose every degraded source detail."""

    if source_reports:
        return _report_lines(source_reports)
    return _evidence_lines(leagues)


def _report_lines(reports: Sequence[GameSourceReport]) -> tuple[str, ...]:
    grouped: dict[str, list[GameSourceReport]] = {}
    for report in reports:
        if report not in grouped.setdefault(report.source, []):
            grouped[report.source].append(report)

    lines: list[str] = []
    for source, source_reports in grouped.items():
        lines.append(_report_summary(source, source_reports))
        for report in source_reports:
            if report.report_state is ReportState.COMPLETE:
                continue
            label = "Failure" if report.report_state is ReportState.FAILED else "Limitation"
            details = report.errors or (_default_report_detail(report.report_state),)
            lines.extend(f"  {label} [{report.game_id}]: {detail}" for detail in details)
    return tuple(lines)


def _report_summary(source: str, reports: Sequence[GameSourceReport]) -> str:
    states = Counter(report.report_state for report in reports)
    state_label = _aggregate_state_label(states)
    game_label = "game" if len(reports) == 1 else "games"
    parsed_count = sum(len(report.parsed_teams) for report in reports)
    expected_count = sum(len(report.expected_teams) for report in reports)
    parsed = frozenset(team for report in reports for team in report.parsed_teams)
    coverage = (
        f"team coverage {parsed_count}/{expected_count}"
        f" ({', '.join(sorted(parsed)) or 'none'})"
    )
    return (
        f"{source_label(source)}: {state_label} — "
        f"{len(reports)} {game_label}; {coverage}"
    )


def _aggregate_state_label(states: Counter[ReportState]) -> str:
    if len(states) == 1:
        return _state_label(next(iter(states)))
    details = ", ".join(
        f"{count} {_state_label(state).lower()}"
        for state in ReportState
        if (count := states[state])
    )
    return f"MIXED ({details})"


def _evidence_lines(
    leagues: Sequence[FantasyLeagueStatuses],
) -> tuple[str, ...]:
    grouped: dict[str, set[SourceResult]] = {}
    for league in leagues:
        for player_status in league.players:
            if player_status.status is None:
                continue
            for result in player_status.status.source_results:
                grouped.setdefault(result.source, set()).add(result)
    if not grouped:
        return ("No source reports or player evidence available.",)

    lines: list[str] = []
    for source, results in grouped.items():
        states = Counter(result.report_state for result in results)
        lines.append(
            f"{source_label(source)}: {_aggregate_state_label(states)} — "
            "player-level evidence only"
        )
        degraded_details = {
            result.detail
            for result in results
            if result.report_state is not ReportState.COMPLETE and result.detail
        }
        for detail in sorted(degraded_details):
            lines.append(f"  Limitation: {detail}")
    return tuple(lines)


def _state_label(state: ReportState) -> str:
    return state.value.replace("_", " ").upper()


def _default_report_detail(state: ReportState) -> str:
    return {
        ReportState.PARTIAL: "Report coverage was incomplete.",
        ReportState.NOT_YET_PUBLISHED: "Report was not yet published.",
        ReportState.FAILED: "Source retrieval or parsing failed.",
        ReportState.COMPLETE: "Report was complete.",
    }[state]
