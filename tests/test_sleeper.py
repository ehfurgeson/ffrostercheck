import json
from collections import Counter
from pathlib import Path

import httpx
import pytest

from app.config import LeagueConfig
from app.fantasy.sleeper import (
    SleeperAPIError,
    SleeperClient,
    render_sleeper_rosters,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sleeper"


def _fixture(name: str) -> object:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _mock_client() -> tuple[httpx.Client, Counter[str]]:
    responses = {
        "/v1/user/watchdog-user": _fixture("user.json"),
        "/v1/user/user-1/leagues/nfl/2026": _fixture("leagues.json"),
        "/v1/league/league-1": _fixture("league_1.json"),
        "/v1/league/league-1/rosters": _fixture("rosters_1.json"),
        "/v1/league/league-2": _fixture("league_2.json"),
        "/v1/league/league-2/rosters": _fixture("rosters_2.json"),
        "/v1/players/nfl": _fixture("players.json"),
    }
    calls: Counter[str] = Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls[path] += 1
        if path not in responses:
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(
            200,
            json=responses[path],
            headers={"Age": "42", "ETag": '"fixture-etag"'},
        )

    return (
        httpx.Client(
            base_url="https://api.sleeper.app/v1",
            transport=httpx.MockTransport(handler),
        ),
        calls,
    )


def test_load_roster_uses_membership_and_preserves_starter_slots() -> None:
    http_client, calls = _mock_client()
    client = SleeperClient(http_client=http_client)

    rosters = client.load_rosters(
        username="watchdog-user",
        season=2026,
        configured_leagues=(LeagueConfig("league-1", "8", "My League"),),
    )

    assert len(rosters) == 1
    roster = rosters[0]
    assert roster.nickname == "My League"
    assert [(player.name, player.lineup_slot) for player in roster.starters] == [
        ("Quarter Back", "QB"),
        ("Wide Receiver", "WR"),
        ("Flex Player", "FLEX"),
    ]
    assert [player.player_id for player in roster.bench] == ["p4", "p5", "p6"]
    assert roster.bench[1].is_reserve
    assert roster.bench[2].is_taxi
    assert len(roster.players) == 6
    assert calls["/v1/players/nfl"] == 1


def test_auto_discovery_supports_co_owned_rosters_and_fetches_catalog_once() -> None:
    http_client, calls = _mock_client()
    client = SleeperClient(http_client=http_client)

    rosters = client.load_rosters(username="watchdog-user", season=2026)

    assert [roster.roster_id for roster in rosters] == ["8", "3"]
    assert [player.player_id for player in rosters[1].bench] == ["p9"]
    assert calls["/v1/players/nfl"] == 1
    assert client.response_metadata["/players/nfl"].cache_age_seconds == 42


def test_configured_league_must_belong_to_user_for_season() -> None:
    http_client, _ = _mock_client()
    client = SleeperClient(http_client=http_client)

    with pytest.raises(SleeperAPIError, match="missing-league"):
        client.load_rosters(
            username="watchdog-user",
            season=2026,
            configured_leagues=(LeagueConfig("missing-league", "1", "Missing"),),
        )


def test_rendered_report_separates_starters_and_bench() -> None:
    http_client, _ = _mock_client()
    client = SleeperClient(http_client=http_client)
    roster = client.load_rosters(
        username="watchdog-user",
        season=2026,
        configured_leagues=(LeagueConfig("league-1", "8", "My League"),),
    )[0]

    report = render_sleeper_rosters((roster,))

    assert "My League (First League)" in report
    assert "QB: Quarter Back (BUF/QB)" in report
    assert "BN: Reserve Player (DAL/RB) [reserve]" in report
    assert report.index("Starters:") < report.index("Bench:")
