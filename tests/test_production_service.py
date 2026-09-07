from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.nfl import KickoffPlan
from app.scheduling.production import OperationalSnapshot, run_production_game_day


def test_production_service_exits_cleanly_when_today_has_no_relevant_games(
    monkeypatch,
) -> None:
    started_at = datetime(2026, 9, 8, 11, 0, tzinfo=timezone.utc)
    config = SimpleNamespace(
        alerts=SimpleNamespace(
            prefetch_attempts_minutes_before_kickoff=(95, 75, 15),
            email_minutes_before_kickoff=5,
        ),
        timezone_info=ZoneInfo("America/New_York"),
    )
    monkeypatch.setattr("app.scheduling.production.load_config", lambda _path: config)
    monkeypatch.setattr(
        "app.scheduling.production.load_environment", lambda **_kwargs: object()
    )
    monkeypatch.setattr(
        "app.scheduling.production.load_operational_snapshot",
        lambda *_args, **_kwargs: OperationalSnapshot(
            lineup=object(),  # no jobs means execution state is intentionally unused
            kickoff_plan=KickoffPlan(windows=(), unmatched=()),
            game_weeks={},
        ),
    )

    result = run_production_game_day(
        config_path=Path("config.yaml"),
        env_file=Path(".env"),
        cache_dir=Path("cache"),
        started_at=started_at,
    )

    assert result.jobs == ()
    assert result.failed == ()


def test_systemd_unit_invokes_the_installed_game_day_command() -> None:
    unit = Path("deploy/systemd/fantasy-watchdog.service").read_text(encoding="utf-8")

    assert "fantasy-watchdog run-game-day" in unit
    assert "EnvironmentFile=/etc/fantasy-watchdog/environment" in unit
    assert "ReadWritePaths=/var/lib/fantasy-watchdog" in unit
