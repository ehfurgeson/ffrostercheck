from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.analysis import (
    FantasyLeagueStatuses,
    LeaguePlayerStatus,
    determine_fantasy_severity,
    find_valid_bench_substitutes,
    parse_league_roster_eligibility,
)
from app.models import (
    Confidence,
    DepthOpportunity,
    FantasyLeague,
    FantasyPlatform,
    FantasyPlayer,
    GameDayState,
    InjuryDesignation,
    NFLPlayerStatus,
    OpportunityLevel,
    RosterEligibility,
)


NOW = datetime(2026, 9, 6, 16, 55, tzinfo=timezone.utc)


def _league(
    *,
    league_id: str = "league-1",
    platform: FantasyPlatform = FantasyPlatform.SLEEPER,
) -> FantasyLeague:
    rules = (
        {"roster_positions": ("RB", "FLEX", "BN", "BN", "IR", "TAXI")}
        if platform is FantasyPlatform.SLEEPER
        else {"lineupSlotCounts": {"2": 1, "23": 1, "20": 4, "21": 1}}
    )
    return FantasyLeague(
        id=league_id,
        name="Test League",
        nickname="Test",
        platform=platform,
        roster_id="roster-1",
        roster_rules=rules,
        scoring_settings={},
    )


def _player(
    league: FantasyLeague,
    player_id: str,
    *,
    starter: bool,
    lineup_slot: str,
    eligible_slots: tuple[str, ...],
    reserve: bool = False,
    taxi: bool = False,
) -> FantasyPlayer:
    return FantasyPlayer(
        platform_player_id=player_id,
        name=player_id.replace("-", " ").title(),
        nfl_team="NE",
        position="RB",
        league_id=league.id,
        league_name=league.name,
        platform=league.platform,
        lineup_slot=lineup_slot,
        eligible_slots=eligible_slots,
        is_starter=starter,
        is_reserve=reserve,
        is_taxi=taxi,
        canonical_player_id=player_id,
    )


def _status(
    player_id: str,
    *,
    roster_eligibility: RosterEligibility = RosterEligibility.ELIGIBLE,
    game_day: GameDayState = GameDayState.ACTIVE,
    designation: InjuryDesignation = InjuryDesignation.NONE,
    confidence: Confidence = Confidence.OFFICIAL,
) -> NFLPlayerStatus:
    return NFLPlayerStatus(
        canonical_player_id=player_id,
        roster_eligibility=roster_eligibility,
        game_day_state=game_day,
        injury_designation=designation,
        confidence=confidence,
        decision_at=NOW,
    )


def _league_statuses(
    league: FantasyLeague,
    players_and_statuses: tuple[tuple[FantasyPlayer, NFLPlayerStatus | None], ...],
) -> FantasyLeagueStatuses:
    return FantasyLeagueStatuses(
        league=league,
        team_name="Test Team",
        players=tuple(
            LeaguePlayerStatus(player, status, determine_fantasy_severity(player, status))
            for player, status in players_and_statuses
        ),
    )


def _opportunity(player_id: str, level: OpportunityLevel) -> DepthOpportunity:
    return DepthOpportunity(
        beneficiary_player_id=player_id,
        unavailable_player_ids=("blocker",),
        level=level,
        previous_order_in_slot=2,
        effective_order_in_slot=1 if level is OpportunityLevel.PROMOTED else 2,
        promoted_to_first_available=level is OpportunityLevel.PROMOTED,
        confidence=Confidence.HIGH,
        depth_chart_as_of=NOW,
        status_decision_at=NOW,
    )


def test_finds_only_same_league_eligible_verified_bench_players() -> None:
    league = _league()
    starter = _player(
        league, "starter", starter=True, lineup_slot="RB", eligible_slots=("RB",)
    )
    healthy = _player(
        league, "healthy", starter=False, lineup_slot="BN", eligible_slots=("RB",)
    )
    receiver = _player(
        league, "receiver", starter=False, lineup_slot="BN", eligible_slots=("WR",)
    )
    unknown = _player(
        league, "unknown", starter=False, lineup_slot="BN", eligible_slots=("RB",)
    )
    inactive = _player(
        league, "inactive", starter=False, lineup_slot="BN", eligible_slots=("RB",)
    )
    reserve = _player(
        league, "reserve", starter=False, lineup_slot="IR", eligible_slots=("RB",), reserve=True
    )
    taxi = _player(
        league, "taxi", starter=False, lineup_slot="TAXI", eligible_slots=("RB",), taxi=True
    )
    doubtful = _player(
        league, "doubtful", starter=False, lineup_slot="BN", eligible_slots=("RB",)
    )
    out = _player(league, "out", starter=False, lineup_slot="BN", eligible_slots=("RB",))
    ineligible = _player(
        league, "ineligible", starter=False, lineup_slot="BN", eligible_slots=("RB",)
    )
    unmapped = _player(
        league, "unmapped", starter=False, lineup_slot="BN", eligible_slots=("RB",)
    )
    statuses = _league_statuses(
        league,
        (
            (starter, _status("starter", game_day=GameDayState.INACTIVE)),
            (healthy, _status("healthy")),
            (receiver, _status("receiver")),
            (unknown, _status("unknown", game_day=GameDayState.UNKNOWN)),
            (inactive, _status("inactive", game_day=GameDayState.INACTIVE)),
            (reserve, _status("reserve")),
            (taxi, _status("taxi")),
            (doubtful, _status("doubtful", designation=InjuryDesignation.DOUBTFUL)),
            (out, _status("out", designation=InjuryDesignation.OUT)),
            (
                ineligible,
                _status("ineligible", roster_eligibility=RosterEligibility.INELIGIBLE),
            ),
            (unmapped, None),
        ),
    )

    options = find_valid_bench_substitutes(
        statuses,
        parse_league_roster_eligibility(league),
    )

    assert len(options) == 1
    assert options[0].starter.player is starter
    assert [candidate.player_status.player for candidate in options[0].candidates] == [healthy]


def test_questionable_active_candidate_ranks_below_healthy_before_opportunity() -> None:
    league = _league()
    starter = _player(
        league, "starter", starter=True, lineup_slot="FLEX", eligible_slots=("RB",)
    )
    healthy = _player(
        league, "healthy", starter=False, lineup_slot="BN", eligible_slots=("RB",)
    )
    questionable = _player(
        league, "questionable", starter=False, lineup_slot="BN", eligible_slots=("RB",)
    )
    statuses = _league_statuses(
        league,
        (
            (starter, _status("starter", designation=InjuryDesignation.QUESTIONABLE)),
            (healthy, _status("healthy", confidence=Confidence.LOW)),
            (
                questionable,
                _status("questionable", designation=InjuryDesignation.QUESTIONABLE),
            ),
        ),
    )

    options = find_valid_bench_substitutes(
        statuses,
        parse_league_roster_eligibility(league),
        (_opportunity("questionable", OpportunityLevel.PROMOTED),),
    )

    assert [candidate.player_status.player for candidate in options[0].candidates] == [
        healthy,
        questionable,
    ]


def test_confidence_then_opportunity_provide_deterministic_bounded_ranking() -> None:
    league = _league()
    starter = _player(
        league, "starter", starter=True, lineup_slot="RB", eligible_slots=("RB",)
    )
    promoted = _player(
        league, "promoted", starter=False, lineup_slot="BN", eligible_slots=("RB",)
    )
    official = _player(
        league, "official", starter=False, lineup_slot="BN", eligible_slots=("RB",)
    )
    low = _player(
        league, "low", starter=False, lineup_slot="BN", eligible_slots=("RB",)
    )
    statuses = _league_statuses(
        league,
        (
            (starter, _status("starter", roster_eligibility=RosterEligibility.INELIGIBLE)),
            (official, _status("official")),
            (low, _status("low", confidence=Confidence.LOW)),
            (promoted, _status("promoted", confidence=Confidence.LOW)),
        ),
    )

    options = find_valid_bench_substitutes(
        statuses,
        parse_league_roster_eligibility(league),
        (_opportunity("promoted", OpportunityLevel.PROMOTED),),
    )

    assert [candidate.player_status.player for candidate in options[0].candidates] == [
        official,
        promoted,
        low,
    ]
    assert options[0].candidates[1].opportunity is not None


def test_normal_starters_are_not_given_replacement_options() -> None:
    league = _league()
    starter = _player(
        league, "starter", starter=True, lineup_slot="RB", eligible_slots=("RB",)
    )
    bench = _player(
        league, "bench", starter=False, lineup_slot="BN", eligible_slots=("RB",)
    )
    statuses = _league_statuses(
        league,
        ((starter, _status("starter")), (bench, _status("bench"))),
    )

    assert find_valid_bench_substitutes(
        statuses,
        parse_league_roster_eligibility(league),
    ) == ()


def test_espn_uses_exact_slot_id_eligibility_and_rejects_wrong_rules() -> None:
    league = _league(platform=FantasyPlatform.ESPN)
    starter = _player(
        league, "starter", starter=True, lineup_slot="RB", eligible_slots=("2",)
    )
    flex_only = _player(
        league, "flex-only", starter=False, lineup_slot="BE", eligible_slots=("23", "20")
    )
    running_back = _player(
        league, "running-back", starter=False, lineup_slot="BE", eligible_slots=("2", "20")
    )
    statuses = _league_statuses(
        league,
        (
            (starter, _status("starter", game_day=GameDayState.INACTIVE)),
            (flex_only, _status("flex-only")),
            (running_back, _status("running-back")),
        ),
    )

    options = find_valid_bench_substitutes(
        statuses,
        parse_league_roster_eligibility(league),
    )
    assert [candidate.player_status.player for candidate in options[0].candidates] == [
        running_back
    ]

    other_rules = parse_league_roster_eligibility(replace(league, id="other"))
    with pytest.raises(ValueError, match="does not belong"):
        find_valid_bench_substitutes(statuses, other_rules)
