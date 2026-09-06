from datetime import datetime, timezone

import pytest

from app.analysis import (
    FantasyStatusIssueState,
    FantasyStatusMappingError,
    map_nfl_statuses_to_fantasy_leagues,
)
from app.models import (
    Confidence,
    FantasyLeague,
    FantasyPlatform,
    FantasyPlayer,
    FantasyRoster,
    GameDayState,
    InjuryDesignation,
    NFLPlayerStatus,
    RosterEligibility,
)


NOW = datetime(2026, 9, 6, 16, 55, tzinfo=timezone.utc)


def _roster(
    league_id: str,
    *,
    canonical_id: str | None = "00-001",
    is_starter: bool = True,
) -> FantasyRoster:
    league = FantasyLeague(
        id=league_id,
        name=f"League {league_id}",
        nickname=league_id,
        platform=FantasyPlatform.SLEEPER,
        roster_id=f"roster-{league_id}",
        roster_rules={},
        scoring_settings={},
    )
    player = FantasyPlayer(
        platform_player_id=f"platform-{league_id}",
        name="Shared Player",
        nfl_team="NE",
        position="RB",
        league_id=league.id,
        league_name=league.name,
        platform=league.platform,
        lineup_slot="RB" if is_starter else "BN",
        eligible_slots=("RB", "FLEX"),
        is_starter=is_starter,
        canonical_player_id=canonical_id,
    )
    return FantasyRoster(league=league, team_name=f"Team {league_id}", players=(player,))


def _status(canonical_id: str = "00-001") -> NFLPlayerStatus:
    return NFLPlayerStatus(
        canonical_player_id=canonical_id,
        roster_eligibility=RosterEligibility.ELIGIBLE,
        game_day_state=GameDayState.ACTIVE,
        injury_designation=InjuryDesignation.NONE,
        confidence=Confidence.OFFICIAL,
        decision_at=NOW,
    )


def test_one_nfl_status_maps_to_every_fantasy_league_instance() -> None:
    status = _status()
    mapping = map_nfl_statuses_to_fantasy_leagues(
        (
            _roster("friends"),
            _roster("family"),
            _roster("main", is_starter=False),
        ),
        (status,),
    )

    assert mapping.is_complete is True
    assert [league.league.id for league in mapping.leagues] == ["friends", "family", "main"]
    mapped = [league.players[0] for league in mapping.leagues]
    assert all(item.status is status for item in mapped)
    assert len(mapping.leagues[0].starters) == 1
    assert len(mapping.leagues[2].bench) == 1


def test_unresolved_and_missing_status_players_remain_visible_as_issues() -> None:
    mapping = map_nfl_statuses_to_fantasy_leagues(
        (
            _roster("unresolved", canonical_id=None),
            _roster("missing", canonical_id="00-missing"),
        ),
        (),
    )

    assert mapping.is_complete is False
    assert [issue.state for issue in mapping.issues] == [
        FantasyStatusIssueState.UNRESOLVED_PLAYER_IDENTITY,
        FantasyStatusIssueState.MISSING_NFL_STATUS,
    ]
    assert all(league.players[0].is_mapped is False for league in mapping.leagues)


def test_unowned_depth_blocker_status_does_not_create_a_fantasy_instance() -> None:
    mapping = map_nfl_statuses_to_fantasy_leagues(
        (_roster("owned"),),
        (_status(), _status("00-unowned-blocker")),
    )

    assert mapping.is_complete is True
    assert len(mapping.leagues[0].players) == 1
    assert mapping.leagues[0].players[0].status is not None
    assert mapping.leagues[0].players[0].status.canonical_player_id == "00-001"


def test_duplicate_canonical_statuses_fail_instead_of_overwriting() -> None:
    with pytest.raises(FantasyStatusMappingError, match="duplicate NFL status"):
        map_nfl_statuses_to_fantasy_leagues(
            (_roster("owned"),),
            (_status(), _status()),
        )
