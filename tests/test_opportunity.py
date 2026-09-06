from datetime import datetime, timezone

from app.analysis import detect_depth_opportunities, render_depth_opportunities
from app.models import (
    Confidence,
    FantasyPlatform,
    FantasyPlayer,
    GameDayState,
    InjuryDesignation,
    NFLPlayerStatus,
    OpportunityLevel,
    ReportState,
    RosterEligibility,
    SourceResult,
)
from app.nfl import (
    DepthChartRow,
    DepthChartSnapshot,
    DepthSnapshotState,
    build_owned_depth_relations,
    join_owned_skill_players,
)


SNAPSHOT_AT = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
DECISION_AT = datetime(2026, 9, 4, 16, 55, tzinfo=timezone.utc)


def _owned(canonical_id: str, *, position: str = "RB") -> FantasyPlayer:
    return FantasyPlayer(
        platform_player_id=canonical_id,
        name=f"Owned {canonical_id}",
        nfl_team="NE",
        position=position,
        league_id="league-1",
        league_name="Fixture League",
        platform=FantasyPlatform.SLEEPER,
        lineup_slot="BN",
        eligible_slots=(position,),
        is_starter=False,
        canonical_player_id=canonical_id,
    )


def _row(
    canonical_id: str | None,
    rank: int,
    *,
    slot: int = 1,
    position: str = "RB",
    formation: str = "11 personnel",
) -> DepthChartRow:
    return DepthChartRow(
        snapshot_at=SNAPSHOT_AT,
        team="NE",
        player_name=canonical_id or "Unresolved Player",
        espn_id=None,
        gsis_id=canonical_id,
        formation=formation,
        position=position,
        position_slot=slot,
        source_rank=rank,
    )


def _relations(players: tuple[FantasyPlayer, ...], *rows: DepthChartRow):
    snapshot = DepthChartSnapshot(
        state=DepthSnapshotState.AVAILABLE,
        season=2026,
        as_of=DECISION_AT,
        max_age_hours=30,
        snapshot_at=SNAPSHOT_AT,
        rows=rows,
    )
    return build_owned_depth_relations(join_owned_skill_players(players, snapshot))


def _status(
    canonical_id: str,
    *,
    game_day: GameDayState = GameDayState.UNKNOWN,
    designation: InjuryDesignation = InjuryDesignation.UNKNOWN,
    source: str | None = None,
    confidence: Confidence = Confidence.OFFICIAL,
) -> NFLPlayerStatus:
    result = (
        SourceResult(
            source=source,
            source_url="https://example.test/status",
            success=True,
            report_state=ReportState.COMPLETE,
            retrieved_at=DECISION_AT,
            game_day_state=game_day,
            injury_designation=designation,
        )
        if source
        else None
    )
    return NFLPlayerStatus(
        canonical_player_id=canonical_id,
        roster_eligibility=RosterEligibility.ELIGIBLE,
        game_day_state=game_day,
        injury_designation=designation,
        confidence=confidence,
        decision_at=DECISION_AT,
        official_inactive=game_day is GameDayState.INACTIVE and source == "nfl_inactives",
        source_results=(result,) if result else (),
    )


def test_official_inactive_promotes_owned_player_to_first_available() -> None:
    relations = _relations(
        (_owned("owned"),),
        _row("starter", 1),
        _row("owned", 4),
    )

    opportunities = detect_depth_opportunities(
        relations,
        (_status("starter", game_day=GameDayState.INACTIVE, source="nfl_inactives"),),
    )

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.level is OpportunityLevel.PROMOTED
    assert opportunity.unavailable_player_ids == ("starter",)
    assert opportunity.previous_order_in_slot == 2
    assert opportunity.effective_order_in_slot == 1
    assert opportunity.promoted_to_first_available is True
    assert opportunity.confidence is Confidence.HIGH


def test_multiple_unavailable_blockers_can_create_a_role_boost_or_promotion() -> None:
    relations = _relations(
        (_owned("owned"),),
        _row("first", 1),
        _row("second", 4),
        _row("third", 7),
        _row("owned", 10),
    )
    statuses = (
        _status("first", designation=InjuryDesignation.OUT, source="nfl_injuries"),
        _status("second", game_day=GameDayState.ACTIVE, source="nfl_inactives"),
        _status("third", game_day=GameDayState.INACTIVE, source="nfl_inactives"),
    )

    opportunity = detect_depth_opportunities(relations, statuses)[0]

    assert opportunity.level is OpportunityLevel.ROLE_BOOST
    assert opportunity.unavailable_player_ids == ("first", "third")
    assert opportunity.previous_order_in_slot == 4
    assert opportunity.effective_order_in_slot == 2
    assert opportunity.promoted_to_first_available is False


def test_unknown_and_fallback_only_blockers_remain_ahead() -> None:
    relations = _relations(
        (_owned("owned"),),
        _row(None, 1),
        _row("fallback-out", 2),
        _row("official-out", 3),
        _row("owned", 4),
    )
    statuses = (
        _status(
            "fallback-out",
            designation=InjuryDesignation.OUT,
            source="sleeper_status",
            confidence=Confidence.MEDIUM,
        ),
        _status("official-out", designation=InjuryDesignation.OUT, source="nfl_injuries"),
    )

    opportunity = detect_depth_opportunities(relations, statuses)[0]

    assert opportunity.level is OpportunityLevel.ROLE_BOOST
    assert opportunity.unavailable_player_ids == ("official-out",)
    assert opportunity.previous_order_in_slot == 4
    assert opportunity.effective_order_in_slot == 3


def test_other_slot_starter_creates_only_broad_positional_opportunity() -> None:
    relations = _relations(
        (_owned("owned-wr", position="WR"),),
        _row("own-slot-starter", 1, slot=1, position="WR"),
        _row("owned-wr", 4, slot=1, position="WR"),
        _row("other-slot-starter", 2, slot=2, position="WR"),
        _row("other-slot-backup", 5, slot=2, position="WR"),
    )

    opportunity = detect_depth_opportunities(
        relations,
        (
            _status(
                "other-slot-starter",
                game_day=GameDayState.INACTIVE,
                source="nfl_inactives",
            ),
        ),
    )[0]

    assert opportunity.level is OpportunityLevel.POSITIONAL_OPPORTUNITY
    assert opportunity.unavailable_player_ids == ("other-slot-starter",)
    assert opportunity.previous_order_in_slot == opportunity.effective_order_in_slot == 2
    assert opportunity.promoted_to_first_available is False
    assert opportunity.confidence is Confidence.MEDIUM


def test_other_position_or_formation_does_not_create_broad_opportunity() -> None:
    relations = _relations(
        (_owned("owned"),),
        _row("owned", 1),
        _row("other-position", 1, slot=2, position="WR"),
        _row("other-formation", 1, slot=2, formation="12 personnel"),
    )
    statuses = (
        _status("other-position", game_day=GameDayState.INACTIVE, source="nfl_inactives"),
        _status("other-formation", game_day=GameDayState.INACTIVE, source="nfl_inactives"),
    )

    assert detect_depth_opportunities(relations, statuses) == ()


def test_same_slot_signal_wins_over_broad_positional_context() -> None:
    relations = _relations(
        (_owned("owned"),),
        _row("same-slot-starter", 1),
        _row("owned", 4),
        _row("other-slot-starter", 2, slot=2),
    )
    statuses = (
        _status("same-slot-starter", game_day=GameDayState.INACTIVE, source="nfl_inactives"),
        _status("other-slot-starter", game_day=GameDayState.INACTIVE, source="nfl_inactives"),
    )

    opportunity = detect_depth_opportunities(relations, statuses)[0]

    assert opportunity.level is OpportunityLevel.PROMOTED
    assert opportunity.unavailable_player_ids == ("same-slot-starter",)
    assert "order 2 -> 1" in render_depth_opportunities((opportunity,))
