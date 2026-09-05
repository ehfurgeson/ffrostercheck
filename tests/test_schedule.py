import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from app.models import FantasyLeague, FantasyPlatform, FantasyPlayer, FantasyRoster
from app.nfl.schedule import (
    NextGameState,
    assign_next_games,
    parse_nfl_schedule,
    render_next_games,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "nflverse"
EASTERN = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _frame(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(list(rows))


def _fixture_frame() -> pl.DataFrame:
    rows = json.loads((FIXTURE_DIR / "schedule_matching.json").read_text(encoding="utf-8"))
    return pl.DataFrame(rows)


def _player(
    *,
    name: str = "Mapped Player",
    team: str | None = "BUF",
    player_id: str = "espn-1",
) -> FantasyPlayer:
    return FantasyPlayer(
        platform_player_id=player_id,
        name=name,
        nfl_team=team,
        position="WR",
        league_id="league-1",
        league_name="Fixture League",
        platform=FantasyPlatform.ESPN,
        lineup_slot="WR",
        eligible_slots=("WR",),
        is_starter=True,
        canonical_player_id="00-001",
    )


def _roster(*players: FantasyPlayer) -> FantasyRoster:
    league = FantasyLeague(
        id="league-1",
        name="Fixture League",
        nickname="Fixture",
        platform=FantasyPlatform.ESPN,
        roster_id="1",
        roster_rules={},
        scoring_settings={},
    )
    return FantasyRoster(league=league, team_name="Fixture Team", players=players)


def test_eastern_gameday_and_gametime_are_stored_as_utc() -> None:
    schedule = parse_nfl_schedule(_fixture_frame())
    thursday = next(game for game in schedule.games if game.game_id == "2026_01_DAL_PHI")

    assert thursday.kickoff.tzinfo is not None
    assert thursday.kickoff == datetime(2026, 9, 11, 0, 20, tzinfo=UTC)
    assert thursday.kickoff.astimezone(EASTERN) == datetime(
        2026, 9, 10, 20, 20, tzinfo=EASTERN
    )


def test_four_oh_five_and_four_twenty_five_remain_distinct_games() -> None:
    schedule = parse_nfl_schedule(_fixture_frame())
    early = next(game for game in schedule.games if game.game_id == "2026_01_MIA_NE")
    late = next(game for game in schedule.games if game.game_id == "2026_01_KC_LAC")

    assert early.kickoff != late.kickoff
    assert early.kickoff.astimezone(EASTERN).strftime("%H:%M") == "16:05"
    assert late.kickoff.astimezone(EASTERN).strftime("%H:%M") == "16:25"


def test_preseason_rows_are_ignored_and_invalid_rows_are_kept() -> None:
    schedule = parse_nfl_schedule(_fixture_frame())

    assert all(game.game_type == "REG" for game in schedule.games)
    assert "2026_PRE_GB_CHI" not in {game.game_id for game in schedule.games}
    assert any(row.game_id == "2026_01_BAD_ROW" for row in schedule.invalid_rows)
    assert next(game for game in schedule.games if game.game_id == "2026_01_LA_SEA").away_team == "LAR"


def test_next_unstarted_game_is_selected_by_normalized_team() -> None:
    schedule = parse_nfl_schedule(_fixture_frame())
    as_of = datetime(2026, 9, 4, 12, 0, tzinfo=EASTERN)

    result = assign_next_games((_roster(_player(team="BUF")),), schedule, as_of=as_of)

    assignment = result.assignments[0]
    assert assignment.state is NextGameState.MATCHED
    assert assignment.game is not None
    assert assignment.game.game_id == "2026_01_BUF_BAL"


def test_bye_week_skips_to_the_next_actual_game() -> None:
    schedule = parse_nfl_schedule(
        _frame(
            {
                "game_id": "2026_01_NYJ_NE",
                "season": 2026,
                "game_type": "REG",
                "week": 1,
                "gameday": "2026-09-13",
                "gametime": "13:00",
                "away_team": "NYJ",
                "home_team": "NE",
            },
            {
                "game_id": "2026_03_CIN_NYJ",
                "season": 2026,
                "game_type": "REG",
                "week": 3,
                "gameday": "2026-09-27",
                "gametime": "13:00",
                "away_team": "CIN",
                "home_team": "NYJ",
            },
        )
    )
    during_bye = datetime(2026, 9, 20, 12, 0, tzinfo=EASTERN)

    result = assign_next_games((_roster(_player(team="NYJ")),), schedule, as_of=during_bye)

    assert result.assignments[0].game is not None
    assert result.assignments[0].game.game_id == "2026_03_CIN_NYJ"


def test_already_started_games_are_skipped_or_reported() -> None:
    schedule = parse_nfl_schedule(_fixture_frame())
    after_week1 = datetime(2026, 9, 13, 17, 0, tzinfo=EASTERN)
    after_season = datetime(2026, 10, 1, 12, 0, tzinfo=EASTERN)

    later = assign_next_games((_roster(_player(team="BUF")),), schedule, as_of=after_week1)
    finished = assign_next_games((_roster(_player(team="KC")),), schedule, as_of=after_season)

    assert later.assignments[0].game is not None
    assert later.assignments[0].game.game_id == "2026_02_NYJ_BUF"
    assert finished.assignments[0].state is NextGameState.ALREADY_STARTED
    assert finished.assignments[0].game is not None
    assert finished.assignments[0].game.game_id == "2026_01_KC_LAC"


def test_missing_team_and_absent_team_are_unmatched_without_guessing() -> None:
    schedule = parse_nfl_schedule(_fixture_frame())
    as_of = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    roster = _roster(
        _player(name="Free Agent", team=None, player_id="fa"),
        _player(name="No Game", team="CIN", player_id="cin"),
    )

    result = assign_next_games((roster,), schedule, as_of=as_of)
    rendered = render_next_games(result)

    assert result.assignments[0].state is NextGameState.MISSING_TEAM
    assert result.assignments[1].state is NextGameState.BYE
    assert result.assignments[0].game is None
    assert result.assignments[1].game is None
    assert "missing_team" in rendered
    assert "bye" in rendered
    assert "CIN" in rendered


def test_invalid_team_rows_do_not_create_a_game() -> None:
    schedule = parse_nfl_schedule(_fixture_frame())
    as_of = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)

    result = assign_next_games((_roster(_player(team="TEN")),), schedule, as_of=as_of)

    assert result.assignments[0].state is NextGameState.INVALID_SCHEDULE
    assert result.assignments[0].game is None
    assert result.data_errors


def test_naive_as_of_is_rejected() -> None:
    schedule = parse_nfl_schedule(_fixture_frame())

    with pytest.raises(ValueError, match="timezone-aware"):
        assign_next_games(
            (_roster(_player()),),
            schedule,
            as_of=datetime(2026, 9, 4, 12, 0),
        )
