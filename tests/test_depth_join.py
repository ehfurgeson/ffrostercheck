from datetime import datetime, timezone

from app.models import FantasyPlatform, FantasyPlayer
from app.nfl import (
    CanonicalPlayer,
    DepthChartRow,
    DepthChartSnapshot,
    DepthJoinIssueState,
    DepthJoinMethod,
    DepthSnapshotState,
    build_depth_chart_index,
    join_owned_skill_players,
    render_owned_depth_join,
)


SNAPSHOT_AT = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def _player(
    *,
    player_id: str,
    canonical_id: str | None,
    name: str = "Owned Player",
    position: str = "RB",
    platform: FantasyPlatform = FantasyPlatform.SLEEPER,
    league_id: str = "league-1",
) -> FantasyPlayer:
    return FantasyPlayer(
        platform_player_id=player_id,
        name=name,
        nfl_team="NE",
        position=position,
        league_id=league_id,
        league_name=f"Fixture {league_id}",
        platform=platform,
        lineup_slot=position,
        eligible_slots=(position,),
        is_starter=True,
        canonical_player_id=canonical_id,
    )


def _row(
    *,
    gsis_id: str | None = "00-001",
    espn_id: str | None = "1001",
    name: str = "Owned Player",
) -> DepthChartRow:
    return DepthChartRow(
        snapshot_at=SNAPSHOT_AT,
        team="NE",
        player_name=name,
        espn_id=espn_id,
        gsis_id=gsis_id,
        formation="Offense",
        position="RB",
        position_slot=1,
        source_rank=1,
    )


def _snapshot(*rows: DepthChartRow) -> DepthChartSnapshot:
    return DepthChartSnapshot(
        state=DepthSnapshotState.AVAILABLE,
        season=2026,
        as_of=datetime(2026, 9, 4, 18, 0, tzinfo=timezone.utc),
        max_age_hours=30,
        snapshot_at=SNAPSHOT_AT,
        rows=rows,
    )


def test_snapshot_is_indexed_by_both_exact_provider_ids() -> None:
    row = _row()
    index = build_depth_chart_index(_snapshot(row))

    assert index.by_gsis_id["00-001"] == (row,)
    assert index.by_espn_id["1001"] == (row,)


def test_gsis_join_deduplicates_cross_league_fantasy_instances() -> None:
    sleeper = _player(player_id="sleeper-1", canonical_id="00-001")
    espn = _player(
        player_id="1001",
        canonical_id="00-001",
        platform=FantasyPlatform.ESPN,
        league_id="league-2",
    )

    result = join_owned_skill_players((sleeper, espn), _snapshot(_row()))

    assert len(result.matches) == 1
    assert result.matches[0].method is DepthJoinMethod.GSIS_ID
    assert result.matches[0].fantasy_players == (sleeper, espn)
    assert result.matched_fantasy_instances == 2


def test_known_espn_id_is_used_only_when_gsis_is_absent_from_depth_row() -> None:
    player = _player(player_id="sleeper-1", canonical_id="00-001")
    identity = CanonicalPlayer(
        canonical_player_id="00-001",
        name="Owned Player",
        team="NE",
        position="RB",
        espn_id="1001",
    )

    result = join_owned_skill_players(
        (player,),
        _snapshot(_row(gsis_id=None)),
        identities=(identity,),
    )

    assert result.matches[0].method is DepthJoinMethod.ESPN_ID
    assert result.matches[0].depth_row.espn_id == "1001"


def test_espn_fantasy_id_can_supply_the_exact_fallback() -> None:
    player = _player(
        player_id="1001",
        canonical_id="00-001",
        platform=FantasyPlatform.ESPN,
    )

    result = join_owned_skill_players((player,), _snapshot(_row(gsis_id=None)))

    assert result.matches[0].method is DepthJoinMethod.ESPN_ID


def test_missing_ids_do_not_fall_back_to_name_team_or_position() -> None:
    player = _player(player_id="sleeper-1", canonical_id="00-missing")

    result = join_owned_skill_players(
        (player,),
        _snapshot(_row(name=player.name)),
    )

    assert result.matches == ()
    assert result.issues[0].state is DepthJoinIssueState.NOT_IN_SNAPSHOT
    assert "GSIS 00-missing" in (result.issues[0].detail or "")


def test_unresolved_defense_and_non_skill_players_are_explicitly_excluded() -> None:
    unresolved = _player(player_id="unknown", canonical_id=None)
    defense = _player(
        player_id="def",
        canonical_id="DST:NE",
        name="New England Patriots",
        position="D/ST",
    )
    kicker = _player(player_id="kicker", canonical_id="00-009", position="K")

    result = join_owned_skill_players((unresolved, defense, kicker), _snapshot())

    assert [issue.state for issue in result.issues] == [
        DepthJoinIssueState.UNRESOLVED_IDENTITY,
        DepthJoinIssueState.TEAM_DEFENSE,
        DepthJoinIssueState.NON_SKILL_POSITION,
    ]
    rendered = render_owned_depth_join(result)
    assert "unresolved_identity=1" in rendered
    assert "team_defense=1" in rendered
    assert "non_skill_position=1" in rendered


def test_duplicate_provider_id_is_ambiguous_instead_of_guessed() -> None:
    player = _player(player_id="sleeper-1", canonical_id="00-001")

    result = join_owned_skill_players(
        (player,),
        _snapshot(_row(name="First row"), _row(name="Second row")),
    )

    assert result.matches == ()
    assert result.issues[0].state is DepthJoinIssueState.AMBIGUOUS_DEPTH_ID
    assert "multiple depth rows" in (result.issues[0].detail or "")


def test_empty_or_stale_snapshot_remains_non_blocking_join_input() -> None:
    player = _player(player_id="sleeper-1", canonical_id="00-001")
    snapshot = _snapshot()
    stale = DepthChartSnapshot(
        state=DepthSnapshotState.STALE,
        season=snapshot.season,
        as_of=snapshot.as_of,
        max_age_hours=snapshot.max_age_hours,
        snapshot_at=snapshot.snapshot_at,
        rows=(_row(),),
        detail="stale fixture",
    )

    missing_result = join_owned_skill_players((player,), snapshot)
    stale_result = join_owned_skill_players((player,), stale)

    assert missing_result.issues[0].state is DepthJoinIssueState.NOT_IN_SNAPSHOT
    assert stale_result.matches[0].depth_row.player_name == "Owned Player"
    assert stale_result.index.snapshot.state is DepthSnapshotState.STALE
