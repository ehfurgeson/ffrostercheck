from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.config import AlertsConfig
from app.models import RelevantGame
from app.nfl.schedule import KickoffPlan, KickoffWindow
from app.scheduling import PlannedJobKind, build_game_day_plan, render_game_day_plan


EASTERN = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _window(hour: int, minute: int, *games: tuple[str, str, str]) -> KickoffWindow:
    kickoff = datetime(2026, 9, 13, hour, minute, tzinfo=EASTERN).astimezone(UTC)
    relevant = tuple(
        RelevantGame(
            game_id=game_id,
            away_team=away,
            home_team=home,
            kickoff=kickoff,
            fantasy_players=(),
        )
        for game_id, away, home in games
    )
    return KickoffWindow(kickoff=kickoff, games=relevant)


def _kickoff_plan(*windows: KickoffWindow) -> KickoffPlan:
    return KickoffPlan(windows=windows, unmatched=())


def test_plans_prefetch_attempts_and_final_job_in_execution_order() -> None:
    plan = build_game_day_plan(
        _kickoff_plan(_window(13, 0, ("early", "BUF", "BAL"))),
        AlertsConfig((95, 75, 15), 5),
        planned_at=datetime(2026, 9, 13, 8, 0, tzinfo=EASTERN),
        display_timezone=EASTERN,
    )

    assert [job.kind for job in plan.jobs] == [
        PlannedJobKind.PREFETCH,
        PlannedJobKind.PREFETCH,
        PlannedJobKind.PREFETCH,
        PlannedJobKind.FINAL,
    ]
    assert [job.run_at.astimezone(EASTERN).strftime("%H:%M") for job in plan.jobs] == [
        "11:25",
        "11:45",
        "12:45",
        "12:55",
    ]
    assert len({job.job_id for job in plan.jobs}) == 4
    assert not plan.missed_jobs


def test_exact_kickoff_windows_stay_separate_and_shared_games_stay_together() -> None:
    plan = build_game_day_plan(
        _kickoff_plan(
            _window(16, 5, ("one", "MIA", "NE"), ("two", "LAR", "SEA")),
            _window(16, 25, ("three", "KC", "LAC")),
        ),
        AlertsConfig((95,), 5),
        planned_at=datetime(2026, 9, 13, 8, 0, tzinfo=EASTERN),
        display_timezone=EASTERN,
    )

    final_jobs = [job for job in plan.jobs if job.kind is PlannedJobKind.FINAL]
    assert len(final_jobs) == 2
    assert len(final_jobs[0].games) == 2
    assert final_jobs[0].kickoff != final_jobs[1].kickoff


def test_only_selected_local_game_day_is_planned() -> None:
    sunday = _window(13, 0, ("sunday", "BUF", "BAL"))
    monday_kickoff = datetime(2026, 9, 14, 20, 15, tzinfo=EASTERN).astimezone(UTC)
    monday = KickoffWindow(
        kickoff=monday_kickoff,
        games=(RelevantGame("monday", "LV", "DEN", monday_kickoff, ()),),
    )

    plan = build_game_day_plan(
        _kickoff_plan(sunday, monday),
        AlertsConfig((95,), 5),
        planned_at=datetime(2026, 9, 13, 8, 0, tzinfo=EASTERN),
        display_timezone=EASTERN,
    )

    assert plan.game_date == date(2026, 9, 13)
    assert [game.game_id for game in plan.games] == ["sunday"]


def test_passed_attempts_are_reported_but_not_scheduled() -> None:
    plan = build_game_day_plan(
        _kickoff_plan(_window(13, 0, ("early", "BUF", "BAL"))),
        AlertsConfig((95, 75, 15), 5),
        planned_at=datetime(2026, 9, 13, 12, 0, tzinfo=EASTERN),
        display_timezone=EASTERN,
    )
    rendered = render_game_day_plan(plan)

    assert [job.minutes_before_kickoff for job in plan.missed_jobs] == [95, 75]
    assert [job.minutes_before_kickoff for job in plan.jobs] == [15, 5]
    assert "Missed jobs (not scheduled):" in rendered
    assert "BUF@BAL" in rendered


def test_explicit_game_date_can_plan_the_next_game_day() -> None:
    sunday = _window(13, 0, ("sunday", "BUF", "BAL"))
    monday_kickoff = datetime(2026, 9, 14, 20, 15, tzinfo=EASTERN).astimezone(UTC)
    monday = KickoffWindow(
        kickoff=monday_kickoff,
        games=(RelevantGame("monday", "LV", "DEN", monday_kickoff, ()),),
    )

    plan = build_game_day_plan(
        _kickoff_plan(sunday, monday),
        AlertsConfig((95,), 5),
        planned_at=datetime(2026, 9, 13, 8, 0, tzinfo=EASTERN),
        display_timezone=EASTERN,
        game_date=date(2026, 9, 14),
    )

    assert [game.game_id for game in plan.games] == ["monday"]
    assert len(plan.jobs) == 2


def test_planning_time_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        build_game_day_plan(
            _kickoff_plan(),
            AlertsConfig(),
            planned_at=datetime(2026, 9, 13, 8, 0),
            display_timezone=EASTERN,
        )
