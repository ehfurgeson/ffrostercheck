from app.models import FantasyLeague, FantasyPlatform, FantasyPlayer, FantasyRoster
from app.nfl.identity import CanonicalPlayer, CanonicalTeamSource, PlayerIdentityResolver
from app.nfl.roster import TeamAssignmentSource, map_rosters_to_nfl, render_roster_mapping


def _player(
    *,
    player_id: str = "espn-1",
    name: str = "Mapped Player",
    team: str | None = "BUF",
    position: str = "WR",
) -> FantasyPlayer:
    return FantasyPlayer(
        platform_player_id=player_id,
        name=name,
        nfl_team=team,
        position=position,
        league_id="league-1",
        league_name="Fixture League",
        platform=FantasyPlatform.ESPN,
        lineup_slot="WR",
        eligible_slots=("WR",),
        is_starter=True,
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


def test_current_roster_team_and_canonical_id_are_applied() -> None:
    identity = CanonicalPlayer(
        canonical_player_id="00-001",
        name="Mapped Player",
        team="MIA",
        position="WR",
        espn_id="espn-1",
        team_source=CanonicalTeamSource.CURRENT_ROSTER,
    )

    result = map_rosters_to_nfl((_roster(_player()),), PlayerIdentityResolver((identity,)))

    mapped = result.rosters[0].players[0]
    diagnostic = result.players[0]
    assert mapped.canonical_player_id == "00-001"
    assert mapped.nfl_team == "MIA"
    assert diagnostic.team_source is TeamAssignmentSource.CURRENT_ROSTER
    assert diagnostic.team_conflict


def test_platform_team_is_preferred_when_current_roster_context_is_absent() -> None:
    identity = CanonicalPlayer(
        canonical_player_id="00-001",
        name="Mapped Player",
        team="MIA",
        position="WR",
        espn_id="espn-1",
        team_source=CanonicalTeamSource.PLAYER_METADATA,
    )

    result = map_rosters_to_nfl((_roster(_player()),), PlayerIdentityResolver((identity,)))

    assert result.rosters[0].players[0].nfl_team == "BUF"
    assert result.players[0].team_source is TeamAssignmentSource.FANTASY_PLATFORM
    assert result.players[0].team_conflict


def test_nflverse_metadata_is_used_when_platform_team_is_missing() -> None:
    identity = CanonicalPlayer(
        canonical_player_id="00-001",
        name="Mapped Player",
        team="MIA",
        position="WR",
        espn_id="espn-1",
        team_source=CanonicalTeamSource.PLAYER_METADATA,
    )

    result = map_rosters_to_nfl(
        (_roster(_player(team=None)),),
        PlayerIdentityResolver((identity,)),
    )

    assert result.rosters[0].players[0].nfl_team == "MIA"
    assert result.players[0].team_source is TeamAssignmentSource.NFLVERSE_METADATA


def test_team_aliases_and_defense_labels_are_normalized() -> None:
    defense = _player(
        player_id="-16014",
        name="Los Angeles Rams",
        team="LA",
        position="D/ST",
    )

    result = map_rosters_to_nfl((_roster(defense),), PlayerIdentityResolver(()))

    mapped = result.rosters[0].players[0]
    assert mapped.canonical_player_id == "DST:LAR"
    assert mapped.nfl_team == "LAR"
    assert result.players[0].team_source is TeamAssignmentSource.TEAM_DEFENSE
    assert not result.players[0].team_conflict


def test_unresolved_identity_is_reported_without_discarding_platform_team() -> None:
    result = map_rosters_to_nfl(
        (_roster(_player(player_id="missing", name="Unknown Player")),),
        PlayerIdentityResolver(()),
    )

    mapped = result.rosters[0].players[0]
    assert mapped.canonical_player_id is None
    assert mapped.nfl_team == "BUF"
    assert len(result.unresolved) == 1
    assert result.players[0].team_source is TeamAssignmentSource.FANTASY_PLATFORM
    assert "No unique canonical player match" in render_roster_mapping(result)
