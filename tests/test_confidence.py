from datetime import datetime, timezone

from app.analysis.availability import StatusSubject, combine_official_statuses
from app.analysis.confidence import EvidenceOrigin, degrade_cached_confidence, score_status_confidence
from app.models import (
    Confidence,
    GameDayState,
    GameSourceReport,
    InjuryDesignation,
    ReportState,
    RosterEligibility,
    SourceResult,
)


NOW = datetime(2026, 1, 4, 17, 55, tzinfo=timezone.utc)


def test_official_evidence_is_stronger_than_fallbacks() -> None:
    assert (
        score_status_confidence(EvidenceOrigin.OFFICIAL, EvidenceOrigin.SLEEPER)
        is Confidence.OFFICIAL
    )
    assert (
        score_status_confidence(EvidenceOrigin.OFFICIAL, EvidenceOrigin.NFLVERSE)
        is Confidence.OFFICIAL
    )


def test_sleeper_is_medium_and_nflverse_is_low() -> None:
    assert score_status_confidence(EvidenceOrigin.SLEEPER) is Confidence.MEDIUM
    assert score_status_confidence(EvidenceOrigin.NFLVERSE) is Confidence.LOW
    assert score_status_confidence(EvidenceOrigin.NONE) is Confidence.LOW


def test_contradictory_origins_are_not_averaged() -> None:
    assert (
        score_status_confidence(EvidenceOrigin.SLEEPER, EvidenceOrigin.NFLVERSE)
        is Confidence.MEDIUM
    )
    assert score_status_confidence("official", "sleeper", "nflverse") is Confidence.OFFICIAL


def test_cached_official_evidence_is_reduced_to_high() -> None:
    assert (
        score_status_confidence(EvidenceOrigin.OFFICIAL, origin_fresh=False)
        is Confidence.HIGH
    )
    assert degrade_cached_confidence(Confidence.OFFICIAL) is Confidence.HIGH
    assert degrade_cached_confidence(Confidence.MEDIUM) is Confidence.LOW
    assert degrade_cached_confidence(Confidence.LOW) is Confidence.LOW


def test_official_injury_keeps_control_when_sleeper_disagrees() -> None:
    statuses = combine_official_statuses(
        (
            StatusSubject(
                canonical_player_id="00-001",
                name="Josh Jacobs",
                nfl_team="GB",
                position="RB",
            ),
        ),
        (
            _report(
                "nfl_injuries",
                ReportState.COMPLETE,
                _result(
                    source="nfl_injuries",
                    name="Josh Jacobs",
                    team="GB",
                    position="RB",
                    injury_designation=InjuryDesignation.QUESTIONABLE,
                    detail="Questionable — ankle",
                ),
                expected_teams=frozenset({"GB", "MIN"}),
            ),
            _report(
                "sleeper_status",
                ReportState.COMPLETE,
                _result(
                    source="sleeper_status",
                    name="Josh Jacobs",
                    team="GB",
                    position="RB",
                    injury_designation=InjuryDesignation.OUT,
                    detail="injury_status=Out",
                ),
                expected_teams=frozenset({"GB", "MIN"}),
            ),
        ),
        decision_at=NOW,
    )

    status = statuses[0]
    assert status.injury_designation is InjuryDesignation.QUESTIONABLE
    assert status.confidence is Confidence.OFFICIAL
    assert [result.injury_designation for result in status.source_results] == [
        InjuryDesignation.QUESTIONABLE,
        InjuryDesignation.OUT,
    ]


def test_cached_official_status_is_high_not_official() -> None:
    statuses = combine_official_statuses(
        (
            StatusSubject(
                canonical_player_id="00-001",
                name="Amani Hooker",
                nfl_team="TEN",
                position="S",
                roster_eligibility=RosterEligibility.ELIGIBLE,
            ),
        ),
        (
            _report(
                "nfl_inactives",
                ReportState.COMPLETE,
                _result(
                    source="nfl_inactives",
                    name="Amani Hooker",
                    team="TEN",
                    position="S",
                    game_day_state=GameDayState.INACTIVE,
                ),
            ),
        ),
        decision_at=NOW,
        origin_fresh=False,
    )

    assert statuses[0].game_day_state is GameDayState.INACTIVE
    assert statuses[0].confidence is Confidence.HIGH


def _result(
    *,
    source: str,
    name: str,
    team: str,
    position: str,
    injury_designation: InjuryDesignation | None = None,
    game_day_state: GameDayState | None = None,
    detail: str | None = None,
) -> SourceResult:
    return SourceResult(
        source=source,
        source_url=f"https://example.test/{source}",
        success=True,
        report_state=ReportState.COMPLETE,
        retrieved_at=NOW,
        game_day_state=game_day_state,
        injury_designation=injury_designation,
        detail=detail,
        player_name=name,
        nfl_team=team,
        position=position,
    )


def _report(
    source: str,
    report_state: ReportState,
    *results: SourceResult,
    expected_teams: frozenset[str] = frozenset({"TEN", "JAX"}),
) -> GameSourceReport:
    return GameSourceReport(
        source=source,
        game_id="manual",
        report_state=report_state,
        expected_teams=expected_teams,
        parsed_teams=expected_teams if report_state is ReportState.COMPLETE else frozenset(),
        player_results=results,
        retrieved_at=NOW,
        source_url=f"https://example.test/{source}",
    )
