"""Platform-neutral orchestration for fantasy roster ingestion."""

from __future__ import annotations

from typing import Protocol, Sequence

from app.config import AppConfig, EnvironmentConfig, LeagueConfig
from app.fantasy.espn import ESPNLeagueRoster
from app.fantasy.sleeper import SleeperLeagueRoster
from app.models import FantasyLeague, FantasyPlatform, FantasyPlayer, FantasyRoster


class FantasyManagerError(RuntimeError):
    """Raised when no usable fantasy integration is configured."""


class SleeperRosterLoader(Protocol):
    def load_rosters(
        self,
        *,
        username: str,
        season: int,
        configured_leagues: Sequence[LeagueConfig] = (),
    ) -> tuple[SleeperLeagueRoster, ...]: ...


class ESPNRosterLoader(Protocol):
    def load_roster(
        self,
        *,
        season: int,
        league_id: str,
        team_id: str | None = None,
        nickname: str = "ESPN",
    ) -> ESPNLeagueRoster: ...


class FantasyManager:
    """Load all enabled platforms and expose one normalized roster interface."""

    def __init__(self, *, sleeper: SleeperRosterLoader, espn: ESPNRosterLoader) -> None:
        self.sleeper = sleeper
        self.espn = espn

    def get_all_rosters(
        self,
        config: AppConfig,
        environment: EnvironmentConfig,
    ) -> tuple[FantasyRoster, ...]:
        rosters: list[FantasyRoster] = []

        sleeper_username = environment.sleeper_user or config.sleeper.username
        if sleeper_username:
            source_rosters = self.sleeper.load_rosters(
                username=sleeper_username,
                season=config.season,
                configured_leagues=config.sleeper.leagues,
            )
            rosters.extend(normalize_sleeper_roster(roster) for roster in source_rosters)

        espn_settings = resolve_espn_settings(config, environment)
        if espn_settings is not None:
            league_id, team_id, nickname = espn_settings
            source_roster = self.espn.load_roster(
                season=config.season,
                league_id=league_id,
                team_id=team_id,
                nickname=nickname,
            )
            rosters.append(normalize_espn_roster(source_roster))

        if not rosters:
            raise FantasyManagerError(
                "No fantasy platform is configured; set SLEEPER_USER and/or ESPN_LEAGUE_ID"
            )
        return tuple(rosters)

    def get_all_leagues(
        self,
        config: AppConfig,
        environment: EnvironmentConfig,
    ) -> tuple[FantasyLeague, ...]:
        return tuple(roster.league for roster in self.get_all_rosters(config, environment))


def normalize_sleeper_roster(source: SleeperLeagueRoster) -> FantasyRoster:
    league = FantasyLeague(
        id=source.league_id,
        name=source.league_name,
        nickname=source.nickname,
        platform=FantasyPlatform.SLEEPER,
        roster_id=source.roster_id,
        roster_rules={"roster_positions": source.roster_positions},
        scoring_settings=source.scoring_settings,
    )
    players = tuple(
        FantasyPlayer(
            platform_player_id=player.player_id,
            name=player.name,
            nfl_team=player.nfl_team,
            position=player.position,
            league_id=league.id,
            league_name=league.name,
            platform=league.platform,
            lineup_slot=player.lineup_slot,
            eligible_slots=player.eligible_positions,
            is_starter=player.is_starter,
            is_reserve=player.is_reserve,
            is_taxi=player.is_taxi,
        )
        for player in source.players
    )
    return FantasyRoster(league=league, team_name=f"Roster {source.roster_id}", players=players)


def normalize_espn_roster(source: ESPNLeagueRoster) -> FantasyRoster:
    league = FantasyLeague(
        id=source.league_id,
        name=source.league_name,
        nickname=source.nickname,
        platform=FantasyPlatform.ESPN,
        roster_id=source.team_id,
        roster_rules=source.roster_rules,
        scoring_settings=source.scoring_settings,
    )
    players = tuple(
        FantasyPlayer(
            platform_player_id=player.player_id,
            name=player.name,
            nfl_team=player.nfl_team,
            position=player.position,
            league_id=league.id,
            league_name=league.name,
            platform=league.platform,
            lineup_slot=player.lineup_slot,
            eligible_slots=tuple(str(slot_id) for slot_id in player.eligible_slot_ids),
            is_starter=player.is_starter,
            is_reserve=player.is_reserve,
            platform_injury_status=player.injury_status,
        )
        for player in source.players
    )
    return FantasyRoster(league=league, team_name=source.team_name, players=players)


def resolve_espn_settings(
    config: AppConfig,
    environment: EnvironmentConfig,
) -> tuple[str, str | None, str] | None:
    configured = None
    if environment.espn_league_id:
        configured = next(
            (
                league
                for league in config.espn.leagues
                if league.id == environment.espn_league_id
            ),
            config.espn.leagues[0] if len(config.espn.leagues) == 1 else None,
        )
        league_id = environment.espn_league_id
    else:
        configured = config.espn.leagues[0] if len(config.espn.leagues) == 1 else None
        league_id = configured.id if configured else None

    if not league_id or _is_placeholder(league_id):
        return None
    team_id = configured.team_id if configured and not _is_placeholder(configured.team_id) else None
    nickname = configured.nickname if configured else "ESPN"
    return league_id, team_id, nickname


def render_all_rosters(rosters: Sequence[FantasyRoster]) -> str:
    """Render the platform-neutral Phase 1 roster diagnostic."""

    lines = [f"Fantasy leagues: {len(rosters)}"]
    for roster in rosters:
        platform = roster.league.platform.value.upper()
        lines.extend(
            (
                "",
                f"{roster.league.nickname} — {platform} — {roster.team_name}",
                f"  Starters ({len(roster.starters)}):",
            )
        )
        lines.extend(f"    {_player_line(player)}" for player in roster.starters)
        lines.append(f"  Bench ({len(roster.bench)}):")
        lines.extend(f"    {_player_line(player)}" for player in roster.bench)
    return "\n".join(lines)


def _player_line(player: FantasyPlayer) -> str:
    team_position = "/".join(value for value in (player.nfl_team, player.position) if value)
    detail = f" ({team_position})" if team_position else ""
    flags = []
    if player.is_reserve:
        flags.append("reserve")
    if player.is_taxi:
        flags.append("taxi")
    if player.platform_injury_status:
        flags.append(player.platform_injury_status)
    suffix = f" [{', '.join(flags)}]" if flags else ""
    return f"{player.lineup_slot}: {player.name}{detail}{suffix}"


def _is_placeholder(value: str) -> bool:
    return value.startswith("REPLACE_WITH_")
