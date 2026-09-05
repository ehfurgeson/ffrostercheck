from datetime import datetime, timezone
from pathlib import Path

from app.analysis.availability import (
    StatusSubject,
    combine_official_statuses,
    render_player_statuses,
    subjects_from_fantasy_players,
)
from app.models import (
    Confidence,
    FantasyPlatform,
    FantasyPlayer,
    GameDayState,
    GameSourceReport,
    InjuryDesignation,
    RelevantGame,
    ReportState,
    RosterEligibility,
    SourceResult,
)
from app.nfl.sources.nfl_inactives import NFLInactivesSource
from app.nfl.sources.nfl_injuries import NFLInjuryReportSource


NOW = datetime(2026, 1, 4, 17, 55, tzinfo=timezone.utc)
INACTIVES_DIR = Path(__file__).parent / "fixtures" / "nfl_inactives"
INJURIES_DIR = Path(__file__).parent / "fixtures" / "nfl_injuries"


def _subject(
    *,
    canonical_player_id: str = "00-001",
    name: str = "Amani Hooker",
    team: str | None = "TEN",
    position: str | None = "S",
    eligibility: RosterEligibility = RosterEligibility.UNKNOWN,
) -> StatusSubject:
    return StatusSubject(
        canonical_player_id=canonical_player_id,
        name=name,
        nfl_team=team,
        position=position,
        roster_eligibility=eligibility,
    )


def _result(
    *,
    source: str,
    report_state: ReportState = ReportState.COMPLETE,
    name: str | None = "Amani Hooker",
    team: str | None = "TEN",
    position: str | None = "S",
    game_day_state: GameDayState | None = None,
    injury_designation: InjuryDesignation | None = None,
    roster_eligibility: RosterEligibility | None = None,
    canonical_player_id: str | None = None,
    detail: str | None = None,
) -> SourceResult:
    return SourceResult(
        source=source,
        source_url=f"https://example.test/{source}",
        success=report_state is not ReportState.FAILED,
        report_state=report_state,
        retrieved_at=NOW,
        roster_eligibility=roster_eligibility,
        game_day_state=game_day_state,
        injury_designation=injury_designation,
        detail=detail,
        player_name=name,
        nfl_team=team,
        position=position,
        canonical_player_id=canonical_player_id,
    )


def _report(
    source: str,
    report_state: ReportState,
    *results: SourceResult,
    parsed_teams: frozenset[str] | None = None,
    expected_teams: frozenset[str] = frozenset({"TEN", "JAX"}),
    errors: tuple[str, ...] = (),
) -> GameSourceReport:
    if parsed_teams is None:
        parsed_teams = expected_teams if report_state is ReportState.COMPLETE else frozenset()
    return GameSourceReport(
        source=source,
        game_id="2025_18_TEN_JAX",
        report_state=report_state,
        expected_teams=expected_teams,
        parsed_teams=parsed_teams,
        player_results=results,
        retrieved_at=NOW,
        errors=errors,
        source_url=f"https://example.test/{source}",
    )


def test_listed_inactive_wins_and_keeps_injury_designation() -> None:
    statuses = combine_official_statuses(
        (_subject(eligibility=RosterEligibility.ELIGIBLE),),
        (
            _report(
                "nfl_inactives",
                ReportState.COMPLETE,
                _result(source="nfl_inactives", game_day_state=GameDayState.INACTIVE),
            ),
            _report(
                "nfl_injuries",
                ReportState.COMPLETE,
                _result(
                    source="nfl_injuries",
                    injury_designation=InjuryDesignation.QUESTIONABLE,
                    detail="Questionable — ankle",
                ),
            ),
        ),
        decision_at=NOW,
    )

    status = statuses[0]
    assert status.game_day_state is GameDayState.INACTIVE
    assert status.official_inactive is True
    assert status.injury_designation is InjuryDesignation.QUESTIONABLE
    assert status.injury_description == "Questionable — ankle"
    assert status.confidence is Confidence.OFFICIAL
    assert [result.source for result in status.source_results] == [
        "nfl_inactives",
        "nfl_injuries",
    ]


def test_complete_inactives_can_mark_eligible_unlisted_players_active() -> None:
    statuses = combine_official_statuses(
        (
            _subject(
                name="Trevor Lawrence",
                team="JAX",
                position="QB",
                eligibility=RosterEligibility.ELIGIBLE,
            ),
        ),
        (
            _report("nfl_inactives", ReportState.COMPLETE),
            _report("nfl_injuries", ReportState.COMPLETE),
        ),
        decision_at=NOW,
    )

    status = statuses[0]
    assert status.game_day_state is GameDayState.ACTIVE
    assert status.official_inactive is False
    assert status.injury_designation is InjuryDesignation.NONE
    assert status.confidence is Confidence.OFFICIAL


def test_absence_from_complete_inactives_does_not_infer_active_without_eligibility() -> None:
    unknown = combine_official_statuses(
        (_subject(),),
        (_report("nfl_inactives", ReportState.COMPLETE),),
        decision_at=NOW,
    )[0]
    ineligible = combine_official_statuses(
        (_subject(eligibility=RosterEligibility.INELIGIBLE),),
        (_report("nfl_inactives", ReportState.COMPLETE),),
        decision_at=NOW,
    )[0]

    assert unknown.game_day_state is GameDayState.UNKNOWN
    assert unknown.official_inactive is False
    assert ineligible.game_day_state is GameDayState.UNKNOWN
    assert ineligible.official_inactive is False
    assert ineligible.roster_eligibility is RosterEligibility.INELIGIBLE


def test_official_out_stays_unknown_when_inactives_are_missing_or_incomplete() -> None:
    out_row = _result(
        source="nfl_injuries",
        name="Gunnar Helm",
        team="TEN",
        position="TE",
        injury_designation=InjuryDesignation.OUT,
        detail="Out — knee",
    )
    unpublished = combine_official_statuses(
        (_subject(name="Gunnar Helm", position="TE"),),
        (
            _report("nfl_inactives", ReportState.NOT_YET_PUBLISHED),
            _report("nfl_injuries", ReportState.COMPLETE, out_row),
        ),
        decision_at=NOW,
    )[0]
    partial = combine_official_statuses(
        (_subject(name="Gunnar Helm", position="TE"),),
        (
            _report(
                "nfl_inactives",
                ReportState.PARTIAL,
                parsed_teams=frozenset({"JAX"}),
                errors=("Inactive list missing team(s): TEN",),
            ),
            _report("nfl_injuries", ReportState.COMPLETE, out_row),
        ),
        decision_at=NOW,
    )[0]

    assert unpublished.game_day_state is GameDayState.UNKNOWN
    assert unpublished.injury_designation is InjuryDesignation.OUT
    assert unpublished.official_inactive is None
    assert unpublished.confidence is Confidence.OFFICIAL
    assert partial.game_day_state is GameDayState.UNKNOWN
    assert partial.injury_designation is InjuryDesignation.OUT
    assert any(result.report_state is ReportState.PARTIAL for result in partial.source_results)


def test_blank_injury_row_is_none_only_when_the_injury_report_is_complete() -> None:
    complete = combine_official_statuses(
        (_subject(name="Rico Dowdle", team="CAR", position="RB"),),
        (_report("nfl_injuries", ReportState.COMPLETE, expected_teams=frozenset({"CAR", "TB"})),),
        decision_at=NOW,
    )[0]
    partial = combine_official_statuses(
        (_subject(name="Rico Dowdle", team="CAR", position="RB"),),
        (
            _report(
                "nfl_injuries",
                ReportState.PARTIAL,
                expected_teams=frozenset({"CAR", "TB"}),
                parsed_teams=frozenset({"CAR"}),
            ),
        ),
        decision_at=NOW,
    )[0]

    assert complete.injury_designation is InjuryDesignation.NONE
    assert partial.injury_designation is InjuryDesignation.UNKNOWN


def test_failed_sources_stay_unknown_and_are_preserved() -> None:
    statuses = combine_official_statuses(
        (_subject(),),
        (
            _report("nfl_inactives", ReportState.FAILED, errors=("landing 503",)),
            _report("nfl_injuries", ReportState.FAILED, errors=("injury 503",)),
        ),
        decision_at=NOW,
    )

    status = statuses[0]
    assert status.game_day_state is GameDayState.UNKNOWN
    assert status.injury_designation is InjuryDesignation.UNKNOWN
    assert status.official_inactive is None
    assert status.confidence is Confidence.LOW
    rendered = render_player_statuses(statuses)
    assert "Confidence: 0 official, 0 high, 0 medium, 1 low" in rendered
    assert {result.source for result in status.source_results} == {
        "nfl_inactives",
        "nfl_injuries",
    }
    assert all(result.success is False for result in status.source_results)
    assert "landing 503" in render_player_statuses(statuses)


def test_contradictory_injury_rows_are_kept_and_the_worse_designation_is_used() -> None:
    statuses = combine_official_statuses(
        (_subject(name="Josh Jacobs", team="GB", position="RB"),),
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
                _result(
                    source="nfl_injuries",
                    name="Josh Jacobs",
                    team="GB",
                    position="RB",
                    injury_designation=InjuryDesignation.OUT,
                    detail="Out — ankle",
                ),
                expected_teams=frozenset({"GB", "MIN"}),
            ),
        ),
        decision_at=NOW,
    )

    status = statuses[0]
    assert status.injury_designation is InjuryDesignation.OUT
    assert [result.injury_designation for result in status.source_results] == [
        InjuryDesignation.QUESTIONABLE,
        InjuryDesignation.OUT,
    ]


def test_duplicate_fantasy_instances_share_one_status() -> None:
    players = (
        _fantasy_player("00-jacobs", "Josh Jacobs", "GB", "RB", league_id="a"),
        _fantasy_player("00-jacobs", "Josh Jacobs", "GB", "RB", league_id="b"),
    )

    statuses = combine_official_statuses(
        subjects_from_fantasy_players(players, roster_eligibility=RosterEligibility.ELIGIBLE),
        (
            _report(
                "nfl_inactives",
                ReportState.COMPLETE,
                expected_teams=frozenset({"GB", "MIN"}),
            ),
        ),
        decision_at=NOW,
    )

    assert len(statuses) == 1
    assert statuses[0].canonical_player_id == "00-jacobs"
    assert statuses[0].game_day_state is GameDayState.ACTIVE


def test_ambiguous_same_name_teammates_are_not_guessed() -> None:
    statuses = combine_official_statuses(
        (
            _subject(
                canonical_player_id="00-wr",
                name="Charlie Jones",
                team="CIN",
                position="WR",
                eligibility=RosterEligibility.ELIGIBLE,
            ),
            _subject(
                canonical_player_id="00-cb",
                name="Charlie Jones",
                team="CIN",
                position="CB",
                eligibility=RosterEligibility.ELIGIBLE,
            ),
        ),
        (
            _report(
                "nfl_inactives",
                ReportState.COMPLETE,
                _result(
                    source="nfl_inactives",
                    name="Charlie Jones",
                    team="CIN",
                    position=None,
                    game_day_state=GameDayState.INACTIVE,
                ),
                expected_teams=frozenset({"CIN", "CLE"}),
            ),
        ),
        decision_at=NOW,
    )

    assert all(status.game_day_state is GameDayState.UNKNOWN for status in statuses)
    assert all(status.official_inactive is None for status in statuses)


def test_name_suffix_and_position_alias_still_match() -> None:
    statuses = combine_official_statuses(
        (
            _subject(
                name="Harold Fannin Jr.",
                team="CLE",
                position="TE",
                eligibility=RosterEligibility.ELIGIBLE,
            ),
        ),
        (
            _report(
                "nfl_inactives",
                ReportState.COMPLETE,
                _result(
                    source="nfl_inactives",
                    name="Harold Fannin",
                    team="CLE",
                    position="TE",
                    game_day_state=GameDayState.INACTIVE,
                ),
                expected_teams=frozenset({"CIN", "CLE"}),
            ),
        ),
        decision_at=NOW,
    )

    assert statuses[0].game_day_state is GameDayState.INACTIVE
    assert statuses[0].official_inactive is True


def test_week18_fixtures_combine_without_inferring_active_from_a_partial_injury_page() -> None:
    game = RelevantGame(
        game_id="2025_18_CAR_TB",
        home_team="TB",
        away_team="CAR",
        kickoff=datetime(2026, 1, 3, 21, 30, tzinfo=timezone.utc),
        fantasy_players=(),
    )
    inactives = NFLInactivesSource(
        article_html=(INACTIVES_DIR / "week18_excerpt.html").read_text(encoding="utf-8"),
        article_url="https://www.nfl.com/news/week-18-inactives",
    ).fetch_game(game)
    injuries = NFLInjuryReportSource(
        season=2025,
        week=18,
        html=(INJURIES_DIR / "week18_excerpt.html").read_text(encoding="utf-8"),
        source_url="https://www.nfl.com/injuries/league/2025/reg18",
    ).fetch_game(game, validate_date=False)

    statuses = combine_official_statuses(
        (
            _subject(
                canonical_player_id="00-cherelus",
                name="Claudin Cherelus",
                team="CAR",
                position="LB",
                eligibility=RosterEligibility.ELIGIBLE,
            ),
        ),
        (inactives, injuries),
        decision_at=NOW,
    )

    status = statuses[0]
    assert inactives.report_state is ReportState.PARTIAL
    assert injuries.report_state is ReportState.COMPLETE
    assert status.game_day_state is GameDayState.UNKNOWN
    assert status.injury_designation is InjuryDesignation.OUT
    assert status.official_inactive is None
    rendered = render_player_statuses(statuses, (inactives, injuries))
    assert "nfl_inactives partial" in rendered
    assert "designation=out" in rendered


def test_sleeper_catalog_active_does_not_set_game_day_active() -> None:
    statuses = combine_official_statuses(
        (_subject(name="Trevor Lawrence", team="JAX", position="QB"),),
        (
            _report(
                "sleeper_status",
                ReportState.COMPLETE,
                _result(
                    source="sleeper_status",
                    name="Trevor Lawrence",
                    team="JAX",
                    position="QB",
                    roster_eligibility=RosterEligibility.ELIGIBLE,
                    injury_designation=InjuryDesignation.NONE,
                    detail="catalog_active=true; catalog_active_is_not_game_day_active",
                ),
                expected_teams=frozenset({"TEN", "JAX"}),
            ),
        ),
        decision_at=NOW,
    )

    status = statuses[0]
    assert status.game_day_state is GameDayState.UNKNOWN
    assert status.injury_designation is InjuryDesignation.NONE
    assert status.confidence is Confidence.MEDIUM
    assert status.official_inactive is None


def test_official_inactive_wins_over_sleeper_questionable() -> None:
    statuses = combine_official_statuses(
        (_subject(eligibility=RosterEligibility.ELIGIBLE),),
        (
            _report(
                "nfl_inactives",
                ReportState.COMPLETE,
                _result(source="nfl_inactives", game_day_state=GameDayState.INACTIVE),
            ),
            _report(
                "sleeper_status",
                ReportState.COMPLETE,
                _result(
                    source="sleeper_status",
                    injury_designation=InjuryDesignation.QUESTIONABLE,
                    detail="injury_status=Questionable",
                ),
            ),
        ),
        decision_at=NOW,
    )

    status = statuses[0]
    assert status.game_day_state is GameDayState.INACTIVE
    assert status.official_inactive is True
    assert status.injury_designation is InjuryDesignation.QUESTIONABLE
    assert status.confidence is Confidence.OFFICIAL
    assert [result.source for result in status.source_results] == [
        "nfl_inactives",
        "sleeper_status",
    ]


def test_complete_official_injury_report_keeps_control_over_sleeper() -> None:
    statuses = combine_official_statuses(
        (_subject(name="Trevor Lawrence", team="JAX", position="QB"),),
        (
            _report(
                "nfl_injuries",
                ReportState.COMPLETE,
                expected_teams=frozenset({"TEN", "JAX"}),
            ),
            _report(
                "sleeper_status",
                ReportState.COMPLETE,
                _result(
                    source="sleeper_status",
                    name="Trevor Lawrence",
                    team="JAX",
                    position="QB",
                    injury_designation=InjuryDesignation.QUESTIONABLE,
                    detail="injury_status=Questionable",
                ),
                expected_teams=frozenset({"TEN", "JAX"}),
            ),
        ),
        decision_at=NOW,
    )

    status = statuses[0]
    assert status.injury_designation is InjuryDesignation.NONE
    assert status.confidence is Confidence.OFFICIAL
    assert any(
        result.source == "sleeper_status"
        and result.injury_designation is InjuryDesignation.QUESTIONABLE
        for result in status.source_results
    )


def test_sleeper_fills_designation_when_official_injury_report_is_unpublished() -> None:
    statuses = combine_official_statuses(
        (_subject(),),
        (
            _report("nfl_injuries", ReportState.NOT_YET_PUBLISHED),
            _report(
                "sleeper_status",
                ReportState.COMPLETE,
                _result(
                    source="sleeper_status",
                    injury_designation=InjuryDesignation.QUESTIONABLE,
                    detail="injury_status=Questionable",
                ),
            ),
        ),
        decision_at=NOW,
    )

    status = statuses[0]
    assert status.game_day_state is GameDayState.UNKNOWN
    assert status.injury_designation is InjuryDesignation.QUESTIONABLE
    assert status.confidence is Confidence.MEDIUM


def test_sleeper_ir_blocks_active_inference_from_complete_inactives() -> None:
    statuses = combine_official_statuses(
        (_subject(name="Injured Reserve", team="TEN", position="WR"),),
        (
            _report("nfl_inactives", ReportState.COMPLETE),
            _report(
                "sleeper_status",
                ReportState.COMPLETE,
                _result(
                    source="sleeper_status",
                    name="Injured Reserve",
                    team="TEN",
                    position="WR",
                    roster_eligibility=RosterEligibility.INELIGIBLE,
                    injury_designation=InjuryDesignation.OUT,
                    detail="injury_status=IR",
                ),
                expected_teams=frozenset({"TEN", "JAX"}),
            ),
        ),
        decision_at=NOW,
    )

    status = statuses[0]
    assert status.roster_eligibility is RosterEligibility.INELIGIBLE
    assert status.game_day_state is GameDayState.UNKNOWN
    assert status.official_inactive is False
    assert status.injury_designation is InjuryDesignation.OUT
    assert status.confidence is Confidence.MEDIUM


def test_nflverse_does_not_set_game_day_active() -> None:
    statuses = combine_official_statuses(
        (_subject(canonical_player_id="00-0035678"),),
        (
            _report(
                "nflverse_status",
                ReportState.COMPLETE,
                _result(
                    source="nflverse_status",
                    canonical_player_id="00-0035678",
                    injury_designation=InjuryDesignation.QUESTIONABLE,
                    detail="report_status=Questionable",
                ),
            ),
        ),
        decision_at=NOW,
    )

    status = statuses[0]
    assert status.game_day_state is GameDayState.UNKNOWN
    assert status.injury_designation is InjuryDesignation.QUESTIONABLE
    assert status.confidence is Confidence.LOW
    assert status.official_inactive is None


def test_nflverse_can_match_by_gsis_when_names_differ() -> None:
    statuses = combine_official_statuses(
        (_subject(canonical_player_id="00-0035678", name="A. Hooker"),),
        (
            _report(
                "nflverse_status",
                ReportState.COMPLETE,
                _result(
                    source="nflverse_status",
                    name="Amani Hooker",
                    canonical_player_id="00-0035678",
                    injury_designation=InjuryDesignation.QUESTIONABLE,
                    detail="report_status=Questionable",
                ),
            ),
        ),
        decision_at=NOW,
    )

    assert statuses[0].injury_designation is InjuryDesignation.QUESTIONABLE
    assert statuses[0].confidence is Confidence.LOW


def test_complete_official_injury_report_keeps_control_over_nflverse() -> None:
    statuses = combine_official_statuses(
        (_subject(canonical_player_id="00-0035678", name="Trevor Lawrence", team="JAX", position="QB"),),
        (
            _report(
                "nfl_injuries",
                ReportState.COMPLETE,
                expected_teams=frozenset({"TEN", "JAX"}),
            ),
            _report(
                "nflverse_status",
                ReportState.COMPLETE,
                _result(
                    source="nflverse_status",
                    name="Trevor Lawrence",
                    team="JAX",
                    position="QB",
                    canonical_player_id="00-0035678",
                    injury_designation=InjuryDesignation.QUESTIONABLE,
                    detail="report_status=Questionable",
                ),
                expected_teams=frozenset({"TEN", "JAX"}),
            ),
        ),
        decision_at=NOW,
    )

    status = statuses[0]
    assert status.injury_designation is InjuryDesignation.NONE
    assert status.confidence is Confidence.OFFICIAL
    assert any(
        result.source == "nflverse_status"
        and result.injury_designation is InjuryDesignation.QUESTIONABLE
        for result in status.source_results
    )


def test_sleeper_is_preferred_over_nflverse_when_official_injuries_are_unpublished() -> None:
    statuses = combine_official_statuses(
        (_subject(canonical_player_id="00-0035678"),),
        (
            _report("nfl_injuries", ReportState.NOT_YET_PUBLISHED),
            _report(
                "sleeper_status",
                ReportState.COMPLETE,
                _result(
                    source="sleeper_status",
                    injury_designation=InjuryDesignation.QUESTIONABLE,
                    detail="injury_status=Questionable",
                ),
            ),
            _report(
                "nflverse_status",
                ReportState.COMPLETE,
                _result(
                    source="nflverse_status",
                    canonical_player_id="00-0035678",
                    injury_designation=InjuryDesignation.OUT,
                    detail="report_status=Out",
                ),
            ),
        ),
        decision_at=NOW,
    )

    status = statuses[0]
    assert status.injury_designation is InjuryDesignation.QUESTIONABLE
    assert status.confidence is Confidence.MEDIUM
    assert any(result.source == "nflverse_status" for result in status.source_results)


def test_nflverse_fills_designation_when_official_and_sleeper_are_unavailable() -> None:
    statuses = combine_official_statuses(
        (_subject(canonical_player_id="00-0035678"),),
        (
            _report("nfl_injuries", ReportState.NOT_YET_PUBLISHED),
            _report(
                "nflverse_status",
                ReportState.COMPLETE,
                _result(
                    source="nflverse_status",
                    canonical_player_id="00-0035678",
                    injury_designation=InjuryDesignation.QUESTIONABLE,
                    detail="report_status=Questionable",
                ),
            ),
        ),
        decision_at=NOW,
    )

    status = statuses[0]
    assert status.game_day_state is GameDayState.UNKNOWN
    assert status.injury_designation is InjuryDesignation.QUESTIONABLE
    assert status.confidence is Confidence.LOW


def test_nflverse_absence_does_not_infer_healthy() -> None:
    statuses = combine_official_statuses(
        (_subject(name="Trevor Lawrence", team="JAX", position="QB"),),
        (
            _report(
                "nflverse_status",
                ReportState.COMPLETE,
                expected_teams=frozenset({"TEN", "JAX"}),
            ),
        ),
        decision_at=NOW,
    )

    status = statuses[0]
    assert status.injury_designation is InjuryDesignation.UNKNOWN
    assert status.game_day_state is GameDayState.UNKNOWN
    assert status.confidence is Confidence.LOW


def test_unsupported_nflverse_season_stays_unknown() -> None:
    statuses = combine_official_statuses(
        (_subject(),),
        (
            _report(
                "nflverse_status",
                ReportState.FAILED,
                errors=("Season must be between 2009 and 2025",),
            ),
        ),
        decision_at=NOW,
    )

    status = statuses[0]
    assert status.game_day_state is GameDayState.UNKNOWN
    assert status.injury_designation is InjuryDesignation.UNKNOWN
    assert status.confidence is Confidence.LOW
    assert any(result.success is False for result in status.source_results)


def _fantasy_player(
    canonical_player_id: str,
    name: str,
    team: str,
    position: str,
    *,
    league_id: str,
) -> FantasyPlayer:
    return FantasyPlayer(
        platform_player_id=f"{league_id}-{name}",
        name=name,
        nfl_team=team,
        position=position,
        league_id=league_id,
        league_name=league_id,
        platform=FantasyPlatform.SLEEPER,
        lineup_slot=position,
        eligible_slots=(position,),
        is_starter=True,
        canonical_player_id=canonical_player_id,
    )
