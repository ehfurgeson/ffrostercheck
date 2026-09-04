from typing import Sequence

from app.config import EnvironmentConfig, LeagueConfig, load_config
from app.fantasy.espn import ESPNLeagueRoster, ESPNPlayer
from app.fantasy.manager import (
    FantasyManager,
    normalize_espn_roster,
    normalize_sleeper_roster,
    render_all_rosters,
)
from app.fantasy.sleeper import SleeperLeagueRoster, SleeperPlayer
from app.models import FantasyPlatform


def _sleeper_roster(index: int) -> SleeperLeagueRoster:
    starter = SleeperPlayer(
        player_id="shared-player",
        name="Shared Player",
        nfl_team="BUF",
        position="WR",
        eligible_positions=("WR",),
        lineup_slot="WR",
        is_starter=True,
    )
    bench = SleeperPlayer(
        player_id=f"bench-{index}",
        name=f"Bench Player {index}",
        nfl_team="GB",
        position="RB",
        eligible_positions=("RB",),
        lineup_slot="BN",
        is_starter=False,
        is_reserve=index == 3,
    )
    return SleeperLeagueRoster(
        league_id=f"sleeper-{index}",
        league_name=f"Sleeper League {index}",
        nickname=f"S{index}",
        roster_id=str(index),
        roster_positions=("WR", "BN"),
        scoring_settings={"rec": 1.0},
        starters=(starter,),
        bench=(bench,),
    )


def _espn_roster() -> ESPNLeagueRoster:
    starter = ESPNPlayer(
        player_id="espn-1",
        name="ESPN Starter",
        nfl_team="CIN",
        position="QB",
        lineup_slot="QB",
        lineup_slot_id=0,
        eligible_slot_ids=(0, 7, 20),
        injury_status="QUESTIONABLE",
        is_starter=True,
    )
    bench = ESPNPlayer(
        player_id="espn-2",
        name="ESPN Bench",
        nfl_team="DAL",
        position="TE",
        lineup_slot="BE",
        lineup_slot_id=20,
        eligible_slot_ids=(6, 23, 20),
        injury_status="ACTIVE",
        is_starter=False,
    )
    return ESPNLeagueRoster(
        league_id="espn-987",
        league_name="ESPN League",
        nickname="Main",
        team_id="9",
        team_name="Watchdog Team",
        roster_rules={"lineupSlotCounts": {"0": 1}},
        scoring_settings={"scoringItems": []},
        starters=(starter,),
        bench=(bench,),
    )


class FakeSleeperClient:
    def __init__(self) -> None:
        self.call: tuple[str, int, Sequence[LeagueConfig]] | None = None

    def load_rosters(
        self,
        *,
        username: str,
        season: int,
        configured_leagues: Sequence[LeagueConfig] = (),
    ) -> tuple[SleeperLeagueRoster, ...]:
        self.call = (username, season, configured_leagues)
        return tuple(_sleeper_roster(index) for index in range(1, 4))


class FakeESPNClient:
    def __init__(self) -> None:
        self.call: tuple[int, str, str | None, str] | None = None

    def load_roster(
        self,
        *,
        season: int,
        league_id: str,
        team_id: str | None = None,
        nickname: str = "ESPN",
    ) -> ESPNLeagueRoster:
        self.call = (season, league_id, team_id, nickname)
        return _espn_roster()


def test_sleeper_normalization_preserves_source_context() -> None:
    roster = normalize_sleeper_roster(_sleeper_roster(3))

    assert roster.league.platform is FantasyPlatform.SLEEPER
    assert roster.league.roster_rules == {"roster_positions": ("WR", "BN")}
    assert roster.starters[0].platform_player_id == "shared-player"
    assert roster.starters[0].canonical_player_id is None
    assert roster.bench[0].is_reserve
    assert roster.bench[0].eligible_slots == ("RB",)


def test_espn_normalization_preserves_status_and_eligibility() -> None:
    roster = normalize_espn_roster(_espn_roster())

    assert roster.league.platform is FantasyPlatform.ESPN
    assert roster.league.roster_id == "9"
    assert roster.starters[0].platform_injury_status == "QUESTIONABLE"
    assert roster.starters[0].eligible_slots == ("0", "7", "20")


def test_manager_combines_three_sleeper_rosters_and_one_espn_roster() -> None:
    sleeper = FakeSleeperClient()
    espn = FakeESPNClient()
    manager = FantasyManager(sleeper=sleeper, espn=espn)
    config = load_config("config.example.yaml")
    environment = EnvironmentConfig(
        sleeper_user="runtime-user",
        espn_league_id="espn-987",
    )

    rosters = manager.get_all_rosters(config, environment)

    assert len(rosters) == 4
    assert [roster.league.platform for roster in rosters] == [
        FantasyPlatform.SLEEPER,
        FantasyPlatform.SLEEPER,
        FantasyPlatform.SLEEPER,
        FantasyPlatform.ESPN,
    ]
    assert sleeper.call is not None and sleeper.call[0] == "runtime-user"
    assert espn.call == (2026, "espn-987", None, "Main")
    shared_instances = [
        player
        for roster in rosters
        for player in roster.players
        if player.platform_player_id == "shared-player"
    ]
    assert len(shared_instances) == 3
    assert all(player.canonical_player_id is None for player in shared_instances)


def test_combined_report_has_platform_and_roster_counts() -> None:
    rosters = (
        normalize_sleeper_roster(_sleeper_roster(1)),
        normalize_espn_roster(_espn_roster()),
    )

    report = render_all_rosters(rosters)

    assert report.startswith("Fantasy leagues: 2")
    assert "S1 — SLEEPER — Roster 1" in report
    assert "Main — ESPN — Watchdog Team" in report
    assert "Starters (1):" in report
    assert "Bench (1):" in report
