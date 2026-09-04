import json
from pathlib import Path

import httpx
import pytest

from app.fantasy.espn import ESPNAPIError, ESPNClient, ESPN_VIEWS, render_espn_roster


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "espn"


def _fixture() -> object:
    return json.loads((FIXTURE_DIR / "league.json").read_text(encoding="utf-8"))


def _mock_client(
    response_data: object | None = None,
    *,
    status_code: int = 200,
) -> tuple[httpx.Client, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status_code,
            json=_fixture() if response_data is None else response_data,
            headers={"Age": "7", "ETag": '"espn-fixture"'},
        )

    return (
        httpx.Client(
            base_url="https://lm-api-reads.fantasy.espn.com",
            transport=httpx.MockTransport(handler),
        ),
        requests,
    )


def test_private_roster_is_selected_by_normalized_swid() -> None:
    http_client, requests = _mock_client()
    client = ESPNClient(swid="{owner-guid}", espn_s2="private-cookie", http_client=http_client)

    roster = client.load_roster(season=2026, league_id="987", nickname="Main")

    assert roster.league_id == "987"
    assert roster.team_id == "1"
    assert roster.team_name == "Watchdog Team"
    assert [player.name for player in roster.starters] == ["Starting Quarterback"]
    assert [player.name for player in roster.bench] == ["Bench Runner", "Reserve Receiver"]
    assert roster.bench[1].is_reserve
    assert requests[0].url.params.get_list("view") == list(ESPN_VIEWS)
    assert client.response_metadata is not None
    assert client.response_metadata.cache_age_seconds == 7


def test_player_uses_nested_injury_status_and_maps_team_and_position() -> None:
    http_client, _ = _mock_client()
    client = ESPNClient(http_client=http_client)

    roster = client.load_roster(season=2026, league_id="987", team_id="1")
    starter = roster.starters[0]

    assert starter.injury_status == "QUESTIONABLE"
    assert starter.nfl_team == "BUF"
    assert starter.position == "QB"
    assert starter.lineup_slot == "QB"
    assert starter.eligible_slot_ids == (0, 7, 20)


def test_public_multi_team_league_requires_explicit_team() -> None:
    http_client, _ = _mock_client()
    client = ESPNClient(http_client=http_client)

    with pytest.raises(ESPNAPIError, match="Unable to identify your ESPN team"):
        client.load_roster(season=2026, league_id="987")


def test_missing_roster_view_is_rejected_even_on_http_200() -> None:
    invalid = _fixture()
    assert isinstance(invalid, dict)
    invalid["teams"][0].pop("roster")
    http_client, _ = _mock_client(invalid)
    client = ESPNClient(http_client=http_client)

    with pytest.raises(ESPNAPIError, match=r"teams\[\]\.roster\.entries"):
        client.fetch_league(season=2026, league_id="987")


def test_authentication_failure_is_explicit() -> None:
    http_client, _ = _mock_client({"message": "forbidden"}, status_code=403)
    client = ESPNClient(http_client=http_client)

    with pytest.raises(ESPNAPIError, match="authentication failed"):
        client.fetch_league(season=2026, league_id="987")


def test_partial_cookie_pair_is_rejected() -> None:
    with pytest.raises(ESPNAPIError, match="must be provided together"):
        ESPNClient(swid="{owner-guid}")


def test_rendered_report_separates_starters_and_bench() -> None:
    http_client, _ = _mock_client()
    client = ESPNClient(http_client=http_client)
    roster = client.load_roster(season=2026, league_id="987", team_id="1", nickname="Main")

    report = render_espn_roster(roster)

    assert "Main (Fixture League)" in report
    assert "QB: Starting Quarterback (BUF/QB) [QUESTIONABLE]" in report
    assert "IR: Reserve Receiver (CIN/WR) [INJURY_RESERVE]" in report
    assert report.index("Starters:") < report.index("Bench:")
