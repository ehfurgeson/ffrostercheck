import json
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from app.models import GameDayState, InjuryDesignation, RelevantGame, ReportState
from app.nfl.nflverse import DatasetLoad, DatasetState, NFLVerseSource
from app.nfl.sources.nflverse import (
    NFLVerseStatusSource,
    normalize_nflverse_report_status,
    render_nflverse_status_report,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "nflverse"
NOW = datetime(2026, 1, 4, 16, 30, tzinfo=timezone.utc)


def _rows() -> list[dict]:
    return json.loads((FIXTURE_DIR / "injuries.json").read_text(encoding="utf-8"))


def _game() -> RelevantGame:
    return RelevantGame(
        game_id="2025_18_TEN_JAX",
        home_team="JAX",
        away_team="TEN",
        kickoff=datetime(2026, 1, 4, 18, 0, tzinfo=timezone.utc),
        fantasy_players=(),
    )


def _source(**kwargs) -> NFLVerseStatusSource:
    return NFLVerseStatusSource(
        season=2025,
        week=18,
        rows=_rows(),
        retrieved_at=NOW,
        **kwargs,
    )


def test_known_report_status_values_are_normalized_explicitly() -> None:
    assert normalize_nflverse_report_status("Questionable") is InjuryDesignation.QUESTIONABLE
    assert normalize_nflverse_report_status("Doubtful") is InjuryDesignation.DOUBTFUL
    assert normalize_nflverse_report_status("Out") is InjuryDesignation.OUT
    assert normalize_nflverse_report_status("IR") is InjuryDesignation.OUT
    assert normalize_nflverse_report_status(None) is InjuryDesignation.NONE
    assert normalize_nflverse_report_status("SomethingWeird") is InjuryDesignation.UNKNOWN


def test_fixture_rows_are_filtered_to_the_game_week_and_never_mark_active() -> None:
    report = _source().fetch_game(_game())
    names = {result.player_name: result for result in report.player_results}

    assert report.report_state is ReportState.COMPLETE
    assert report.parsed_teams == frozenset({"TEN", "JAX"})
    assert all(result.game_day_state is None for result in report.player_results)
    assert "Other Conference" not in names
    assert "Wrong Week" not in names
    assert "Postseason Receiver" not in names
    assert names["Amani Hooker"].injury_designation is InjuryDesignation.QUESTIONABLE
    assert names["Amani Hooker"].canonical_player_id == "00-0035678"
    assert names["Gunnar Helm"].injury_designation is InjuryDesignation.OUT
    assert names["Doubtful Back"].injury_designation is InjuryDesignation.DOUBTFUL
    assert names["Mystery Status"].injury_designation is InjuryDesignation.UNKNOWN
    assert report.source_updated_at == datetime(2026, 1, 3, 18, 5, tzinfo=timezone.utc)
    rendered = render_nflverse_status_report(report)
    assert "nflverse injuries never set game-day active" in rendered
    assert "game_day=unset" in rendered


def test_missing_week_is_not_yet_published() -> None:
    report = NFLVerseStatusSource(
        season=2025,
        week=1,
        rows=_rows(),
        retrieved_at=NOW,
    ).fetch_game(_game())

    assert report.report_state is ReportState.NOT_YET_PUBLISHED
    assert report.player_results == ()
    assert all(result.game_day_state is not GameDayState.ACTIVE for result in report.player_results)


def test_unsupported_season_is_a_source_failure() -> None:
    dataset = DatasetLoad(
        name="injuries",
        state=DatasetState.UNSUPPORTED_SEASON,
        frame=None,
        season=2026,
        detail="Season must be between 2009 and 2025",
    )
    report = NFLVerseStatusSource(
        season=2026,
        week=1,
        dataset=dataset,
        retrieved_at=NOW,
    ).fetch_game(_game())

    assert report.report_state is ReportState.FAILED
    assert report.player_results == ()
    assert "Season must be between 2009 and 2025" in " ".join(report.errors)


def test_loader_download_failure_is_unsupported_not_an_app_error() -> None:
    class FailingInjuries:
        def load_injuries(self, season: int) -> pl.DataFrame:
            raise ConnectionError(
                "Failed to download injuries/injuries_2026.parquet: 404 Client Error"
            )

    dataset = NFLVerseSource(FailingInjuries()).load_injuries(2026)
    report = NFLVerseStatusSource(
        season=2026,
        week=1,
        dataset=dataset,
        retrieved_at=NOW,
    ).fetch_game(_game())

    assert dataset.state is DatasetState.UNSUPPORTED_SEASON
    assert report.report_state is ReportState.FAILED
    assert report.player_results == ()


def test_schema_failure_still_fails_the_status_source() -> None:
    class BadInjuries:
        def load_injuries(self, season: int) -> pl.DataFrame:
            return pl.DataFrame([{"season": season}])

    report = NFLVerseStatusSource(
        season=2025,
        week=18,
        nflverse=NFLVerseSource(BadInjuries()),
        retrieved_at=NOW,
    ).fetch_game(_game())

    assert report.report_state is ReportState.FAILED
    assert "missing required columns" in " ".join(report.errors)
