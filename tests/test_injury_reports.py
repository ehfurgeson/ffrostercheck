from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.models import GameDayState, InjuryDesignation, RelevantGame, ReportState
from app.nfl.sources.nfl_injuries import (
    NFLInjuryReportSource,
    parse_game_status,
    parse_injury_report,
    render_injury_report,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "nfl_injuries"
WEEK18_URL = "https://www.nfl.com/injuries/league/2025/reg18"
WEEK1_URL = "https://www.nfl.com/injuries/league/2026/reg1"


def _html(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def _source(
    name: str,
    *,
    season: int = 2025,
    week: int = 18,
    source_url: str | None = None,
) -> NFLInjuryReportSource:
    return NFLInjuryReportSource(
        season=season,
        week=week,
        html=_html(name),
        source_url=source_url or injury_url(season, week),
    )


def injury_url(season: int, week: int) -> str:
    return f"https://www.nfl.com/injuries/league/{season}/reg{week}"


def _game(
    *,
    game_id: str = "2025_18_CAR_TB",
    home: str = "TB",
    away: str = "CAR",
    kickoff: datetime | None = None,
) -> RelevantGame:
    return RelevantGame(
        game_id=game_id,
        home_team=home,
        away_team=away,
        kickoff=kickoff or datetime(2026, 1, 3, 21, 30, tzinfo=timezone.utc),
        fantasy_players=(),
    )


def test_complete_game_emits_designations_without_active_inactive() -> None:
    report = _source("week18_excerpt.html").fetch_game(_game())

    by_name = {result.player_name: result for result in report.player_results}
    assert report.report_state is ReportState.COMPLETE
    assert report.parsed_teams == frozenset({"CAR", "TB"})
    assert by_name["Claudin Cherelus"].injury_designation is InjuryDesignation.OUT
    assert by_name["Krys Barnes"].injury_designation is InjuryDesignation.QUESTIONABLE
    assert by_name["Rico Dowdle"].injury_designation is InjuryDesignation.NONE
    assert all(result.game_day_state is None for result in report.player_results)
    assert all(result.game_day_state is not GameDayState.ACTIVE for result in report.player_results)


def test_doubtful_and_blank_rows_stay_on_the_injury_axis() -> None:
    report = _source("week18_excerpt.html").fetch_game(
        _game(
            game_id="2025_18_GB_MIN",
            home="MIN",
            away="GB",
            kickoff=datetime(2026, 1, 4, 18, 0, tzinfo=timezone.utc),
        )
    )
    by_name = {result.player_name: result for result in report.player_results}

    assert report.report_state is ReportState.COMPLETE
    assert by_name["Dontayvion Wicks"].injury_designation is InjuryDesignation.DOUBTFUL
    assert by_name["Josh Jacobs"].injury_designation is InjuryDesignation.NONE
    assert by_name["Aaron Jones"].injury_designation is InjuryDesignation.OUT
    assert all(result.game_day_state is None for result in report.player_results)


def test_page_title_week_is_ignored_when_url_and_heading_match() -> None:
    html = _html("week18_excerpt.html")
    document = parse_injury_report(
        html,
        season=2025,
        week=18,
        source_url=WEEK18_URL,
        retrieved_at=datetime(2026, 1, 3, 20, 0, tzinfo=timezone.utc),
    )

    assert "Week 1 of the 2025 Season" in html
    assert "<h1>Official NFL Injury Report for Players - Week 1" in html
    assert document.visible_week == 18
    assert document.report_state is not ReportState.FAILED
    assert {"CAR", "TB", "GB", "MIN"} <= document.parsed_teams


def test_zero_tables_is_not_yet_published_even_with_no_injuries_copy() -> None:
    report = _source(
        "empty_no_tables.html",
        season=2026,
        week=1,
        source_url=WEEK1_URL,
    ).fetch_game(
        _game(
            game_id="2026_01_NE_SEA",
            home="SEA",
            away="NE",
            kickoff=datetime(2026, 9, 10, 0, 20, tzinfo=timezone.utc),
        )
    )

    assert report.report_state is ReportState.NOT_YET_PUBLISHED
    assert report.player_results == ()
    assert all(result.game_day_state is not GameDayState.ACTIVE for result in report.player_results)


def test_partial_game_does_not_treat_the_missing_team_as_healthy() -> None:
    report = _source("partial_one_team.html").fetch_game(_game())

    assert report.report_state is ReportState.PARTIAL
    assert report.parsed_teams == frozenset({"CAR"})
    assert report.player_results[0].injury_designation is InjuryDesignation.OUT
    assert "TB" in render_injury_report(report)
    assert all(result.game_day_state is None for result in report.player_results)


def test_wrong_visible_week_is_failed_even_when_tables_exist() -> None:
    report = _source("wrong_week_heading.html").fetch_game(_game())

    assert report.report_state is ReportState.FAILED
    assert report.player_results == ()
    assert any("WEEK 1" in error for error in report.errors)


def test_date_mismatch_prevents_a_complete_report() -> None:
    report = _source("week18_excerpt.html").fetch_game(
        _game(kickoff=datetime(2026, 1, 4, 18, 0, tzinfo=timezone.utc))
    )

    assert report.report_state is ReportState.PARTIAL
    assert any("does not match kickoff" in error for error in report.errors)


def test_game_not_on_page_is_partial_not_healthy() -> None:
    report = _source("week18_excerpt.html").fetch_game(
        _game(game_id="2025_18_DAL_PHI", home="PHI", away="DAL")
    )

    assert report.report_state is ReportState.PARTIAL
    assert report.parsed_teams == frozenset()
    assert report.player_results == ()


def test_unrecognized_game_status_is_unknown_not_out_or_none() -> None:
    assert parse_game_status("Questionable") is InjuryDesignation.QUESTIONABLE
    assert parse_game_status("") is InjuryDesignation.NONE
    assert parse_game_status("Suspended") is InjuryDesignation.UNKNOWN


def test_http_failure_is_failed_not_healthy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="nope")

    source = NFLInjuryReportSource(
        season=2025,
        week=18,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        source_url=WEEK18_URL,
    )

    report = source.fetch_game(_game())

    assert report.report_state is ReportState.FAILED
    assert report.player_results == ()
    assert report.errors
