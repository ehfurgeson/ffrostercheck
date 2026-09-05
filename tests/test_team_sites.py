from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.models import GameDayState, InjuryDesignation, RelevantGame, ReportState
from app.nfl.sources.team_sites import (
    ATTRIBUTED_DISCLAIMER,
    OfficialTeamArticle,
    OfficialTeamSource,
    parse_team_article,
    render_team_status_report,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "team_sites"
NOW = datetime(2026, 1, 4, 16, 30, tzinfo=timezone.utc)


def _html(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def _game(*, home: str, away: str, game_id: str = "manual") -> RelevantGame:
    return RelevantGame(
        game_id=game_id,
        home_team=home,
        away_team=away,
        kickoff=datetime(2026, 1, 4, 18, 0, tzinfo=timezone.utc),
        fantasy_players=(),
    )


def _source(articles: dict[str, OfficialTeamArticle], **kwargs) -> OfficialTeamSource:
    return OfficialTeamSource(articles=articles, retrieved_at=NOW, **kwargs)


def test_packers_lists_name_players_without_setting_binary_status() -> None:
    report = _source(
        {
            "GB": OfficialTeamArticle(
                team="GB",
                html=_html("packers_lists.html"),
                url="https://www.packers.com/news/packers-bears-inactives-week-18-2024",
            )
        }
    ).fetch_game(_game(home="CHI", away="GB", game_id="2024_18_CHI_GB"))

    names = {result.player_name: result for result in report.player_results if result.player_name}

    assert report.report_state is ReportState.PARTIAL
    assert report.parsed_teams == frozenset({"GB"})
    assert report.published_at == datetime(2025, 1, 5, 16, 40, tzinfo=timezone.utc)
    assert {"Jayden Reed", "Carrington Valentine", "Rome Odunze"} <= set(names)
    assert names["Jayden Reed"].nfl_team == "GB"
    assert names["Jayden Reed"].position == "WR"
    assert names["Rome Odunze"].nfl_team == "CHI"
    assert all(result.game_day_state is None for result in report.player_results)
    assert all(result.injury_designation is None for result in report.player_results)
    assert ATTRIBUTED_DISCLAIMER in (names["Jayden Reed"].detail or "")
    rendered = render_team_status_report(report)
    assert "Team narrative is not binary game-day status" in rendered
    assert "game_day=unset" in rendered


def test_chiefs_and_patriots_keep_narrative_as_attributed_excerpt() -> None:
    chiefs = parse_team_article(
        _html("chiefs_paragraphs.html"),
        team="KC",
        source_url="https://www.chiefs.com/news/week-18-inactive-players-chiefs-vs-raiders",
        retrieved_at=NOW,
    )
    patriots = parse_team_article(
        _html("patriots_narrative.html"),
        team="NE",
        source_url="https://www.patriots.com/news/inactives-analysis",
        retrieved_at=NOW,
    )

    assert chiefs.adapter == "chiefs_paragraphs"
    assert patriots.adapter == "patriots_narrative"
    assert chiefs.notes == ()
    assert patriots.notes == ()
    assert chiefs.excerpt and "expected to play" in chiefs.excerpt
    assert chiefs.excerpt and "inactive players" in chiefs.excerpt
    assert patriots.excerpt and "officially active" in patriots.excerpt
    assert "Drake Maye will start" in (patriots.excerpt or "")
    assert ATTRIBUTED_DISCLAIMER in _source(
        {"KC": OfficialTeamArticle(team="KC", html=_html("chiefs_paragraphs.html"))}
    ).fetch_game(_game(home="KC", away="LV")).player_results[0].detail


def test_generic_keyword_extraction_does_not_create_player_status() -> None:
    parsed = parse_team_article(
        _html("chiefs_paragraphs.html"),
        team="JAX",
        source_url=None,
        retrieved_at=NOW,
    )
    report = _source(
        {"JAX": OfficialTeamArticle(team="JAX", html=_html("chiefs_paragraphs.html"))}
    ).fetch_game(_game(home="JAX", away="TEN"))

    named = [result for result in report.player_results if result.player_name]
    assert parsed.adapter == "generic_excerpt"
    assert parsed.notes == ()
    assert named == []
    assert all(result.game_day_state is not GameDayState.ACTIVE for result in report.player_results)
    assert all(result.game_day_state is not GameDayState.INACTIVE for result in report.player_results)


def test_missing_team_article_is_optional_unless_required() -> None:
    optional = _source(
        {"GB": OfficialTeamArticle(team="GB", html=_html("packers_lists.html"))}
    ).fetch_game(_game(home="CHI", away="GB"))
    required = _source(
        {"GB": OfficialTeamArticle(team="GB", html=_html("packers_lists.html"))},
        required=True,
    ).fetch_game(_game(home="CHI", away="GB"))
    unpublished = _source({}).fetch_game(_game(home="CHI", away="GB"))
    required_missing = _source({}, required=True).fetch_game(_game(home="CHI", away="GB"))

    assert optional.report_state is ReportState.PARTIAL
    assert required.report_state is ReportState.FAILED
    assert unpublished.report_state is ReportState.NOT_YET_PUBLISHED
    assert required_missing.report_state is ReportState.FAILED
    assert "Official team article missing for CHI" in " ".join(required.errors)


def test_failed_http_is_failed_and_records_cache_age_on_success() -> None:
    def failed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    failed = OfficialTeamSource(
        articles={
            "GB": OfficialTeamArticle(
                team="GB",
                url="https://www.packers.com/news/packers-bears-inactives-week-18-2024",
            )
        },
        http_client=httpx.Client(transport=httpx.MockTransport(failed_handler)),
        retrieved_at=NOW,
    ).fetch_game(_game(home="CHI", away="GB"))

    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=_html("packers_lists.html"),
            headers={"Age": "41"},
        )

    fetched = OfficialTeamSource(
        articles={
            "GB": OfficialTeamArticle(
                team="GB",
                url="https://www.packers.com/news/packers-bears-inactives-week-18-2024",
            )
        },
        http_client=httpx.Client(transport=httpx.MockTransport(ok_handler)),
        retrieved_at=NOW,
    ).fetch_game(_game(home="CHI", away="GB"))

    assert failed.report_state is ReportState.FAILED
    assert failed.player_results == ()
    assert "request failed" in " ".join(failed.errors)
    assert fetched.report_state is ReportState.PARTIAL
    assert fetched.http_cache_age_seconds == 41
    assert fetched.raw_content_hash
    assert all(
        result.injury_designation is not InjuryDesignation.OUT
        for result in fetched.player_results
    )
