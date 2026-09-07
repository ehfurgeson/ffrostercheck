from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.config import EmailConfig, EnvironmentConfig
from app.health import (
    HealthCheck,
    HealthReport,
    collect_health_report,
    render_health_report,
)
from app.models import GameSourceReport, ReportState
from app.nfl import KickoffPlan, KickoffWindow
from app.nfl.depth_chart import DepthChartSnapshot, DepthSnapshotState
from app.nfl.depth_join import DepthJoinIssue, DepthJoinIssueState, OwnedDepthJoinResult
from app.notification.email import SMTPEmailNotifier
from app.scheduling.production import OperationalSnapshot
from app.storage.cache import StatusCache


CHECKED_AT = datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc)
FIXTURES = Path("tests/fixtures")


class FakeSMTP:
    def __init__(self, host: str, port: int, *, timeout: float) -> None:
        self.connection = (host, port, timeout)
        self.calls: list[str] = []

    def __enter__(self) -> "FakeSMTP":
        self.calls.append("enter")
        return self

    def __exit__(self, *args: object) -> None:
        self.calls.append("exit")

    def ehlo(self) -> None:
        self.calls.append("ehlo")

    def starttls(self, *, context: object) -> None:
        self.calls.append("starttls")

    def login(self, username: str, password: str) -> None:
        self.calls.append("login")


def _configure_connection(
    smtp: FakeSMTP,
    host: str,
    port: int,
    timeout: float,
) -> FakeSMTP:
    smtp.connection = (host, port, timeout)
    return smtp


def _config(**overrides):
    base = {
        "season": 2026,
        "timezone": "America/New_York",
        "timezone_info": ZoneInfo("America/New_York"),
        "alerts": SimpleNamespace(
            prefetch_attempts_minutes_before_kickoff=(95, 75, 15),
            email_minutes_before_kickoff=5,
        ),
        "sleeper": SimpleNamespace(username="worldwideworm", user_id=None, leagues=()),
        "espn": SimpleNamespace(
            leagues=(SimpleNamespace(id="987", team_id="1", nickname="Main"),)
        ),
        "email": EmailConfig("smtp.gmail.com", 587, "recipient@example.com"),
        "depth_chart": SimpleNamespace(enabled=True, max_age_hours=30),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _environment() -> EnvironmentConfig:
    return EnvironmentConfig(
        sleeper_user="worldwideworm",
        espn_league_id="987",
        espn_swid="{SWID}",
        espn_s2="espn-s2",
        smtp_user="sender@example.com",
        smtp_app_password="app-password",
    )


class _FakeSleeper:
    def resolve_user(self, username: str):
        assert username == "worldwideworm"
        return {"user_id": "1024779386450538496"}

    def get_leagues(self, user_id: str, season: int):
        assert user_id == "1024779386450538496"
        assert season == 2026
        return [{"league_id": "1"}, {"league_id": "2"}, {"league_id": "3"}]

    def close(self) -> None:
        return None


class _FakeESPN:
    def fetch_league(self, *, season: int, league_id: str):
        assert season == 2026
        assert league_id == "987"
        return {"teams": [{"id": 1}, {"id": 2}]}

    def close(self) -> None:
        return None


class _FakeInactives:
    def __init__(self, state: ReportState = ReportState.NOT_YET_PUBLISHED) -> None:
        self._state = state

    def load_document(self):
        return SimpleNamespace(
            report_state=self._state,
            parsed_teams=frozenset(),
            errors=("boom",) if self._state is ReportState.FAILED else (),
        )

    def close(self) -> None:
        return None


class _FakeInjuries:
    def __init__(self, state: ReportState = ReportState.NOT_YET_PUBLISHED) -> None:
        self._state = state

    def load_document(self):
        return SimpleNamespace(
            report_state=self._state,
            errors=("boom",) if self._state is ReportState.FAILED else (),
        )

    def close(self) -> None:
        return None


def _game(game_id: str = "2026_01_GB_CHI"):
    return SimpleNamespace(
        game_id=game_id,
        home_team="CHI",
        away_team="GB",
        kickoff=datetime(2026, 9, 7, 20, 25, tzinfo=timezone.utc),
        fantasy_players=(),
    )


def _operational() -> OperationalSnapshot:
    game = _game()
    window = KickoffWindow(kickoff=game.kickoff, games=(game,))
    return OperationalSnapshot(
        lineup=SimpleNamespace(rosters=()),
        kickoff_plan=KickoffPlan(windows=(window,), unmatched=()),
        game_weeks={game.game_id: (2026, 1)},
    )


def _depth_snapshot(state: DepthSnapshotState = DepthSnapshotState.AVAILABLE):
    return DepthChartSnapshot(
        state=state,
        season=2026,
        as_of=CHECKED_AT,
        max_age_hours=30,
        snapshot_at=datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
        rows=(),
        detail="missing" if state is DepthSnapshotState.MISSING else None,
    )


def _join_result(*, unresolved: int = 0) -> OwnedDepthJoinResult:
    issues = tuple(
        DepthJoinIssue(
            DepthJoinIssueState.NOT_IN_SNAPSHOT,
            fantasy_players=(),
            canonical_player_id=f"player-{index}",
            detail="missing",
        )
        for index in range(unresolved)
    )
    return OwnedDepthJoinResult(
        index=SimpleNamespace(snapshot=_depth_snapshot()),  # type: ignore[arg-type]
        matches=(),
        issues=issues,
    )


def test_render_health_report_matches_operator_summary() -> None:
    report = HealthReport(
        checked_at=CHECKED_AT,
        checks=(
            HealthCheck("Sleeper", True, "3 leagues"),
            HealthCheck("ESPN", False, "unauthorized"),
        ),
    )

    text = render_health_report(report)

    assert "Sleeper: OK — 3 leagues" in text
    assert "ESPN: FAIL — unauthorized" in text
    assert "Overall: FAIL" in text


def test_collect_health_report_succeeds_with_injected_probes(tmp_path: Path) -> None:
    smtp = FakeSMTP("unused", 0, timeout=0)
    notifier = SMTPEmailNotifier(
        EmailConfig("smtp.gmail.com", 587, "recipient@example.com"),
        _environment(),
        smtp_factory=lambda host, port, *, timeout: _configure_connection(
            smtp, host, port, timeout
        ),
    )
    cache = StatusCache(tmp_path)
    cache.save_prefetch(
        game_id="2026_01_GB_CHI",
        reports=(
            GameSourceReport(
                source="nfl_inactives",
                game_id="2026_01_GB_CHI",
                report_state=ReportState.COMPLETE,
                expected_teams=frozenset({"GB", "CHI"}),
                parsed_teams=frozenset({"GB", "CHI"}),
                player_results=(),
                retrieved_at=CHECKED_AT,
            ),
        ),
        statuses=(),
        cached_at=CHECKED_AT,
    )

    report = collect_health_report(
        _config(),
        _environment(),
        cache_dir=tmp_path,
        checked_at=CHECKED_AT,
        sleeper_client=_FakeSleeper(),  # type: ignore[arg-type]
        espn_client=_FakeESPN(),  # type: ignore[arg-type]
        inactives_source=_FakeInactives(),  # type: ignore[arg-type]
        injury_source=_FakeInjuries(),  # type: ignore[arg-type]
        smtp_notifier=notifier,
        operational_loader=lambda *_args, **_kwargs: _operational(),
        depth_loader=lambda *_args, **_kwargs: _depth_snapshot(),
        depth_joiner=lambda *_args, **_kwargs: _join_result(),
    )

    by_name = {check.name: check for check in report.checks}
    assert report.ok
    assert by_name["Sleeper"].ok
    assert by_name["ESPN"].ok
    assert by_name["NFL inactives scraper"].detail.startswith("not_yet_published")
    assert by_name["NFL injury scraper"].detail.startswith("2026 WEEK 1")
    assert by_name["Depth chart"].ok
    assert by_name["Unresolved owned depth joins"].detail == "0"
    assert "complete snapshot" in by_name["Status cache"].detail
    assert by_name["SMTP"].ok
    assert "login" in smtp.calls
    assert "send_message" not in smtp.calls
    assert by_name["Next job"].ok


def test_failed_official_scraper_and_unresolved_depth_fail_health(tmp_path: Path) -> None:
    smtp = FakeSMTP("unused", 0, timeout=0)
    notifier = SMTPEmailNotifier(
        EmailConfig("smtp.gmail.com", 587, "recipient@example.com"),
        _environment(),
        smtp_factory=lambda host, port, *, timeout: _configure_connection(
            smtp, host, port, timeout
        ),
    )

    report = collect_health_report(
        _config(),
        _environment(),
        cache_dir=tmp_path,
        checked_at=CHECKED_AT,
        sleeper_client=_FakeSleeper(),  # type: ignore[arg-type]
        espn_client=_FakeESPN(),  # type: ignore[arg-type]
        inactives_source=_FakeInactives(ReportState.FAILED),  # type: ignore[arg-type]
        injury_source=_FakeInjuries(),  # type: ignore[arg-type]
        smtp_notifier=notifier,
        operational_loader=lambda *_args, **_kwargs: _operational(),
        depth_loader=lambda *_args, **_kwargs: _depth_snapshot(),
        depth_joiner=lambda *_args, **_kwargs: _join_result(unresolved=2),
    )

    by_name = {check.name: check for check in report.checks}
    assert not report.ok
    assert not by_name["NFL inactives scraper"].ok
    assert by_name["Unresolved owned depth joins"].detail == "2"
    assert not by_name["Unresolved owned depth joins"].ok


def test_smtp_verification_failure_is_reported_without_secrets(tmp_path: Path) -> None:
    class RaisingSMTP(FakeSMTP):
        def login(self, username: str, password: str) -> None:
            raise OSError("auth failed")

    raising = RaisingSMTP("unused", 0, timeout=0)
    notifier = SMTPEmailNotifier(
        EmailConfig("smtp.gmail.com", 587, "recipient@example.com"),
        _environment(),
        smtp_factory=lambda host, port, *, timeout: _configure_connection(
            raising, host, port, timeout
        ),
    )

    report = collect_health_report(
        _config(),
        _environment(),
        cache_dir=tmp_path,
        checked_at=CHECKED_AT,
        sleeper_client=_FakeSleeper(),  # type: ignore[arg-type]
        espn_client=_FakeESPN(),  # type: ignore[arg-type]
        inactives_source=_FakeInactives(),  # type: ignore[arg-type]
        injury_source=_FakeInjuries(),  # type: ignore[arg-type]
        smtp_notifier=notifier,
        operational_loader=lambda *_args, **_kwargs: _operational(),
        depth_loader=lambda *_args, **_kwargs: _depth_snapshot(),
        depth_joiner=lambda *_args, **_kwargs: _join_result(),
    )

    smtp_check = next(check for check in report.checks if check.name == "SMTP")
    assert not smtp_check.ok
    assert "app-password" not in smtp_check.detail
    assert "OSError" in smtp_check.detail


def test_fixture_inactives_source_is_ok_when_not_yet_published() -> None:
    from app.nfl.sources.nfl_inactives import NFLInactivesSource

    html = (FIXTURES / "nfl_inactives" / "landing_not_published.html").read_text(
        encoding="utf-8"
    )
    with NFLInactivesSource(landing_html=html) as source:
        report = collect_health_report(
            _config(depth_chart=SimpleNamespace(enabled=False, max_age_hours=30)),
            _environment(),
            checked_at=CHECKED_AT,
            sleeper_client=_FakeSleeper(),  # type: ignore[arg-type]
            espn_client=_FakeESPN(),  # type: ignore[arg-type]
            inactives_source=source,
            injury_source=_FakeInjuries(),  # type: ignore[arg-type]
            smtp_notifier=SimpleNamespace(verify_connection=lambda: None),  # type: ignore[arg-type]
            operational_loader=lambda *_args, **_kwargs: _operational(),
        )

    inactives = next(
        item for item in report.checks if item.name == "NFL inactives scraper"
    )
    assert inactives.ok
    assert "not_yet_published" in inactives.detail
