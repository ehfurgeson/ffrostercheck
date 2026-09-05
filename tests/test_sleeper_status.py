import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.fantasy.sleeper import SleeperClient
from app.models import (
    GameDayState,
    InjuryDesignation,
    RelevantGame,
    ReportState,
    RosterEligibility,
)
from app.nfl.sources.sleeper_status import (
    SleeperStatusSource,
    normalize_sleeper_eligibility,
    normalize_sleeper_injury_status,
    render_sleeper_status_report,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sleeper"
NOW = datetime(2026, 1, 4, 16, 30, tzinfo=timezone.utc)


def _catalog() -> dict:
    return json.loads((FIXTURE_DIR / "status_players.json").read_text(encoding="utf-8"))


def _game() -> RelevantGame:
    return RelevantGame(
        game_id="2025_18_TEN_JAX",
        home_team="JAX",
        away_team="TEN",
        kickoff=datetime(2026, 1, 4, 18, 0, tzinfo=timezone.utc),
        fantasy_players=(),
    )


def _source(**kwargs) -> SleeperStatusSource:
    return SleeperStatusSource(catalog=_catalog(), retrieved_at=NOW, **kwargs)


def test_known_injury_status_values_are_normalized_explicitly() -> None:
    assert normalize_sleeper_injury_status("Questionable") is InjuryDesignation.QUESTIONABLE
    assert normalize_sleeper_injury_status("Doubtful") is InjuryDesignation.DOUBTFUL
    assert normalize_sleeper_injury_status("Out") is InjuryDesignation.OUT
    assert normalize_sleeper_injury_status("IR") is InjuryDesignation.OUT
    assert normalize_sleeper_injury_status("PUP") is InjuryDesignation.OUT
    assert normalize_sleeper_injury_status("Sus") is InjuryDesignation.OUT
    assert normalize_sleeper_injury_status(None) is InjuryDesignation.NONE
    assert normalize_sleeper_injury_status("SomethingWeird") is InjuryDesignation.UNKNOWN


def test_eligibility_uses_status_not_catalog_active() -> None:
    assert (
        normalize_sleeper_eligibility(status="Active", injury_status=None)
        is RosterEligibility.ELIGIBLE
    )
    assert (
        normalize_sleeper_eligibility(status="Injured Reserve", injury_status="IR")
        is RosterEligibility.INELIGIBLE
    )
    assert (
        normalize_sleeper_eligibility(status="Suspended", injury_status="Sus")
        is RosterEligibility.INELIGIBLE
    )
    assert (
        normalize_sleeper_eligibility(status="Inactive", injury_status=None)
        is RosterEligibility.UNKNOWN
    )


def test_catalog_active_never_becomes_game_day_active() -> None:
    report = _source().fetch_game(_game())
    names = {result.player_name: result for result in report.player_results}

    assert report.report_state is ReportState.COMPLETE
    assert report.parsed_teams == frozenset({"TEN", "JAX"})
    assert all(result.game_day_state is None for result in report.player_results)
    assert all(result.game_day_state is not GameDayState.ACTIVE for result in report.player_results)
    assert names["Trevor Lawrence"].injury_designation is InjuryDesignation.NONE
    assert names["Trevor Lawrence"].roster_eligibility is RosterEligibility.ELIGIBLE
    assert "catalog_active=true" in (names["Trevor Lawrence"].detail or "")
    assert "catalog_active_is_not_game_day_active" in (names["Trevor Lawrence"].detail or "")
    assert "Jaguars" not in names
    assert "Other Team" not in names
    rendered = render_sleeper_status_report(report)
    assert "Catalog active is not game-day active" in rendered
    assert "game_day=unset" in rendered


def test_ir_pup_and_suspension_are_ineligible_out() -> None:
    report = _source().fetch_game(_game())
    names = {result.player_name: result for result in report.player_results}

    assert names["Injured Reserve"].roster_eligibility is RosterEligibility.INELIGIBLE
    assert names["Injured Reserve"].injury_designation is InjuryDesignation.OUT
    assert names["Pup Player"].roster_eligibility is RosterEligibility.INELIGIBLE
    assert names["Pup Player"].injury_designation is InjuryDesignation.OUT
    assert names["Suspended Player"].roster_eligibility is RosterEligibility.INELIGIBLE
    assert names["Suspended Player"].injury_designation is InjuryDesignation.OUT
    assert names["Amani Hooker"].injury_designation is InjuryDesignation.QUESTIONABLE
    assert names["Gunnar Helm"].injury_designation is InjuryDesignation.OUT
    assert names["Doubtful Back"].injury_designation is InjuryDesignation.DOUBTFUL
    assert names["Mystery Status"].injury_designation is InjuryDesignation.UNKNOWN


def test_missing_team_is_partial_and_failed_http_is_failed() -> None:
    one_team = {
        "p1": {
            "full_name": "Only Jaguar",
            "team": "JAX",
            "position": "QB",
            "active": True,
            "status": "Active",
        }
    }
    partial = SleeperStatusSource(catalog=one_team, retrieved_at=NOW).fetch_game(_game())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    client = SleeperClient(
        http_client=httpx.Client(
            base_url="https://api.sleeper.app/v1",
            transport=httpx.MockTransport(handler),
        )
    )
    failed = SleeperStatusSource(client=client).fetch_game(_game())

    assert partial.report_state is ReportState.PARTIAL
    assert partial.parsed_teams == frozenset({"JAX"})
    assert "TEN" in " ".join(partial.errors)
    assert failed.report_state is ReportState.FAILED
    assert failed.player_results == ()
    assert all(result.game_day_state is not GameDayState.ACTIVE for result in partial.player_results)


def test_http_catalog_records_cache_age() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/players/nfl")
        return httpx.Response(
            200,
            json=_catalog(),
            headers={"Age": "77", "ETag": '"sleeper-status"'},
        )

    client = SleeperClient(
        http_client=httpx.Client(
            base_url="https://api.sleeper.app/v1",
            transport=httpx.MockTransport(handler),
        )
    )
    report = SleeperStatusSource(client=client).fetch_game(_game())

    assert report.report_state is ReportState.COMPLETE
    assert report.http_cache_age_seconds == 77
    assert report.raw_content_hash
