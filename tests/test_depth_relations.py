from datetime import datetime, timezone

from app.models import FantasyPlatform, FantasyPlayer
from app.nfl import (
    DepthChartRow,
    DepthChartSnapshot,
    DepthSlotKey,
    DepthSnapshotState,
    build_owned_depth_relations,
    join_owned_skill_players,
    render_owned_depth_relations,
)


SNAPSHOT_AT = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def _row(
    name: str,
    gsis_id: str | None,
    rank: int | None,
    *,
    team: str = "NEP",
    formation: str | None = " 11  personnel ",
    position: str | None = "rb",
    slot: int | None = 1,
) -> DepthChartRow:
    return DepthChartRow(
        snapshot_at=SNAPSHOT_AT,
        team=team,
        player_name=name,
        espn_id=None,
        gsis_id=gsis_id,
        formation=formation,
        position=position,
        position_slot=slot,
        source_rank=rank,
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


def _owned(canonical_id: str) -> FantasyPlayer:
    return FantasyPlayer(
        platform_player_id="owned",
        name="Owned Player",
        nfl_team="NE",
        position="RB",
        league_id="league-1",
        league_name="Fixture League",
        platform=FantasyPlatform.SLEEPER,
        lineup_slot="RB",
        eligible_slots=("RB",),
        is_starter=False,
        canonical_player_id=canonical_id,
    )


def _relations(owned_id: str, *rows: DepthChartRow):
    snapshot = _snapshot(*rows)
    joined = join_owned_skill_players((_owned(owned_id),), snapshot)
    return build_owned_depth_relations(joined)


def test_rows_are_grouped_by_normalized_team_formation_position_and_slot() -> None:
    result = _relations(
        "00-owned",
        _row("Owned Player", "00-owned", 3),
        _row("Same Slot", "00-same", 1, team="NE", formation="11 PERSONNEL"),
        _row("Other Formation", "00-form", 1, formation="12 personnel"),
        _row("Other Position", "00-pos", 1, position="WR"),
        _row("Other Slot", "00-slot", 1, slot=2),
        _row("Other Team", "00-team", 1, team="NYJ"),
    )

    owned_key = DepthSlotKey("NE", "11 PERSONNEL", "RB", 1)
    assert len(result.chains) == 5
    assert [row.player_name for row in result.chains[owned_key].players] == [
        "Same Slot",
        "Owned Player",
    ]


def test_non_contiguous_source_ranks_define_order_and_players_ahead() -> None:
    result = _relations(
        "00-owned",
        _row("Owned Player", "00-owned", 9),
        _row("First", "00-first", 1),
        _row("Middle", "00-middle", 4),
        _row("Behind", "00-behind", 12),
    )

    relation = result.relations["00-owned"]
    assert relation.source_rank == 9
    assert [blocker.depth_row.player_name for blocker in relation.players_ahead] == [
        "First",
        "Middle",
    ]
    assert relation.resolved_player_ids_ahead == ("00-first", "00-middle")


def test_equal_rank_is_not_arbitrarily_treated_as_ahead() -> None:
    result = _relations(
        "00-owned",
        _row("Owned Player", "00-owned", 2),
        _row("Same Rank", "00-same-rank", 2),
    )

    assert result.relations["00-owned"].players_ahead == ()


def test_unresolved_blocker_is_preserved_for_later_degradation_logic() -> None:
    result = _relations(
        "00-owned",
        _row("Unknown ID", None, 1),
        _row("Owned Player", "00-owned", 2),
    )

    relation = result.relations["00-owned"]
    assert len(relation.players_ahead) == 1
    assert relation.players_ahead[0].canonical_player_id is None
    assert relation.resolved_player_ids_ahead == ()
    assert "Unknown ID (unresolved GSIS)" in render_owned_depth_relations(result)


def test_incomplete_chain_rows_are_excluded_with_an_explicit_issue() -> None:
    incomplete = _row("No Slot", "00-incomplete", 1, slot=None)
    result = _relations(
        "00-owned",
        _row("Owned Player", "00-owned", 2),
        incomplete,
    )

    assert len(result.issues) == 1
    assert result.issues[0].depth_row is incomplete
    assert "position_slot" in result.issues[0].detail
