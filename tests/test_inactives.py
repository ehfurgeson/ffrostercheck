from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.models import GameDayState, RelevantGame, ReportState
from app.nfl.sources.nfl_inactives import (
    NFLInactivesSource,
    discover_inactives_article,
    parse_inactive_line,
    parse_inactives_article,
    render_inactives_report,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "nfl_inactives"


def _html(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def _game(*, game_id: str = "2025_18_TEN_JAX", home: str = "JAX", away: str = "TEN") -> RelevantGame:
    return RelevantGame(
        game_id=game_id,
        home_team=home,
        away_team=away,
        kickoff=datetime(2026, 1, 4, 18, 0, tzinfo=timezone.utc),
        fantasy_players=(),
    )


def test_complete_game_lists_inactives_without_inferring_active() -> None:
    source = NFLInactivesSource(
        article_html=_html("week18_excerpt.html"),
        article_url="https://www.nfl.com/news/week-18-inactives",
    )

    report = source.fetch_game(_game())

    assert report.report_state is ReportState.COMPLETE
    assert report.parsed_teams == frozenset({"TEN", "JAX"})
    names = {result.player_name for result in report.player_results}
    assert {"Amani Hooker", "Keith Taylor"} <= names
    assert all(result.game_day_state is GameDayState.INACTIVE for result in report.player_results)
    assert all(result.game_day_state is not GameDayState.ACTIVE for result in report.player_results)
    assert report.published_at == datetime(2026, 1, 4, 16, 35, 33, 656000, tzinfo=timezone.utc)
    assert report.source_updated_at == datetime(2026, 1, 4, 23, 54, 20, 728000, tzinfo=timezone.utc)


def test_partial_game_does_not_treat_the_missing_team_as_active() -> None:
    source = NFLInactivesSource(article_html=_html("partial_one_team.html"))

    report = source.fetch_game(_game(game_id="2025_18_DAL_NYG", home="NYG", away="DAL"))

    assert report.report_state is ReportState.PARTIAL
    assert report.parsed_teams == frozenset({"DAL"})
    assert report.player_results[0].game_day_state is GameDayState.INACTIVE
    assert "NYG" in render_inactives_report(report)
    assert all(result.game_day_state is not GameDayState.ACTIVE for result in report.player_results)


def test_unpublished_landing_and_empty_article_are_not_active() -> None:
    landing = NFLInactivesSource(
        landing_html=_html("landing_not_published.html"),
        landing_url="https://www.nfl.com/inactives/",
    )
    empty = NFLInactivesSource(article_html=_html("empty.html"))

    landing_report = landing.fetch_game(_game())
    empty_report = empty.fetch_game(_game())

    assert landing_report.report_state is ReportState.NOT_YET_PUBLISHED
    assert empty_report.report_state is ReportState.NOT_YET_PUBLISHED
    assert landing_report.player_results == ()
    assert empty_report.player_results == ()


def test_article_with_other_games_is_partial_for_a_missing_matchup() -> None:
    source = NFLInactivesSource(article_html=_html("week18_excerpt.html"))

    report = source.fetch_game(_game(game_id="2025_18_DAL_PHI", home="PHI", away="DAL"))

    assert report.report_state is ReportState.PARTIAL
    assert report.parsed_teams == frozenset()
    assert report.player_results == ()


def test_emergency_qb_annotation_is_preserved_then_stripped() -> None:
    player = parse_inactive_line("QB Jake Browning (emergency third QB)", "CIN")

    assert player is not None
    assert player.raw_text == "QB Jake Browning (emergency third QB)"
    assert player.name == "Jake Browning"
    assert player.position == "QB"
    assert player.annotations == ("emergency third QB",)


def test_discovery_finds_inactives_article_and_ignores_generic_news_links() -> None:
    article = discover_inactives_article(
        _html("landing_with_article.html"),
        base_url="https://www.nfl.com/inactives/",
    )
    unpublished = discover_inactives_article(
        _html("landing_not_published.html"),
        base_url="https://www.nfl.com/inactives/",
    )

    assert article == (
        "https://www.nfl.com/news/nfl-week-18-inactives-players-ruled-out-for-sunday-s-14-games"
    )
    assert unpublished is None


def test_http_failure_is_failed_not_active() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="nope")

    source = NFLInactivesSource(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        landing_url="https://www.nfl.com/inactives/",
    )

    report = source.fetch_game(_game())

    assert report.report_state is ReportState.FAILED
    assert report.player_results == ()
    assert report.errors


def test_team_nicknames_and_aliases_resolve_in_the_article() -> None:
    document = parse_inactives_article(
        _html("week18_excerpt.html"),
        source_url="https://www.nfl.com/news/week-18-inactives",
        retrieved_at=datetime(2026, 1, 4, 20, 0, tzinfo=timezone.utc),
    )
    source = NFLInactivesSource(article_html=_html("week18_excerpt.html"))

    report = source.fetch_game(_game(game_id="2025_18_ARI_LAR", home="LAR", away="ARI"))

    assert "TEN" in document.parsed_teams
    assert "JAX" in document.parsed_teams
    assert report.report_state is ReportState.COMPLETE
    assert {player.nfl_team for player in report.player_results} == {"LAR", "ARI"}
