from datetime import datetime, timezone

from app.analysis import (
    StatusScopeIssueState,
    build_status_scope,
    render_status_scope,
)
from app.models import FantasyPlatform, FantasyPlayer
from app.nfl import (
    DepthChartRow,
    DepthChartSnapshot,
    DepthSnapshotState,
    build_owned_depth_relations,
    join_owned_skill_players,
)


SNAPSHOT_AT = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def _player(canonical_id: str, name: str, *, league_id: str = "league-1") -> FantasyPlayer:
    return FantasyPlayer(
        platform_player_id=f"platform-{canonical_id}-{league_id}",
        name=name,
        nfl_team="NE",
        position="RB",
        league_id=league_id,
        league_name="Fixture League",
        platform=FantasyPlatform.SLEEPER,
        lineup_slot="BN",
        eligible_slots=("RB",),
        is_starter=False,
        canonical_player_id=canonical_id,
    )


def _row(name: str, canonical_id: str | None, rank: int) -> DepthChartRow:
    return DepthChartRow(
        snapshot_at=SNAPSHOT_AT,
        team="NE",
        player_name=name,
        espn_id=None,
        gsis_id=canonical_id,
        formation="11 personnel",
        position="RB",
        position_slot=1,
        source_rank=rank,
    )


def _relations(players: tuple[FantasyPlayer, ...], *rows: DepthChartRow):
    snapshot = DepthChartSnapshot(
        state=DepthSnapshotState.AVAILABLE,
        season=2026,
        as_of=datetime(2026, 9, 4, 18, 0, tzinfo=timezone.utc),
        max_age_hours=30,
        snapshot_at=SNAPSHOT_AT,
        rows=rows,
    )
    return build_owned_depth_relations(join_owned_skill_players(players, snapshot))


def test_resolved_blockers_are_deduplicated_and_added_as_unowned_subjects() -> None:
    owned = (
        _player("00-second", "Second Back"),
        _player("00-third", "Third Back"),
    )
    relations = _relations(
        owned,
        _row("First Back", "00-first", 1),
        _row("Second Back", "00-second", 2),
        _row("Third Back", "00-third", 3),
    )

    scope = build_status_scope(owned, relations)

    assert [subject.canonical_player_id for subject in scope.subjects] == [
        "00-second",
        "00-third",
        "00-first",
    ]
    assert scope.resolved_blocker_ids == frozenset({"00-first", "00-second"})
    assert scope.unowned_blocker_ids == frozenset({"00-first"})
    assert scope.issues == ()


def test_owned_blocker_reuses_the_owned_subject_without_creating_an_instance() -> None:
    owned = (
        _player("00-first", "Owned First", league_id="a"),
        _player("00-first", "Owned First", league_id="b"),
        _player("00-second", "Owned Second"),
    )
    relations = _relations(
        owned,
        _row("Depth Name", "00-first", 1),
        _row("Owned Second", "00-second", 2),
    )

    scope = build_status_scope(owned, relations)

    assert len(scope.subjects) == 2
    assert scope.subjects[0].name == "Owned First"
    assert scope.resolved_blocker_ids == frozenset({"00-first"})
    assert scope.unowned_blocker_ids == frozenset()


def test_unresolved_blocker_is_an_explicit_deduplicated_limitation() -> None:
    owned = (
        _player("00-second", "Second Back"),
        _player("00-third", "Third Back"),
    )
    relations = _relations(
        owned,
        _row("Unknown Back", None, 1),
        _row("Second Back", "00-second", 2),
        _row("Third Back", "00-third", 3),
    )

    scope = build_status_scope(owned, relations)

    assert len(scope.subjects) == 2
    assert len(scope.issues) == 1
    issue = scope.issues[0]
    assert issue.state is StatusScopeIssueState.UNRESOLVED_BLOCKER_IDENTITY
    assert issue.affected_player_ids == ("00-second", "00-third")
    assert "availability remains unknown" in issue.detail
    rendered = render_status_scope(scope)
    assert "Unresolved blocker limitations: 1" in rendered
    assert "affects 00-second, 00-third" in rendered
