from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.analysis import (
    FantasyLeagueStatuses,
    LeaguePlayerStatus,
    ReplacementCandidate,
    StarterReplacementOptions,
    determine_fantasy_severity,
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
from app.notification import build_html_email, build_text_email


KICKOFF = datetime(2026, 9, 6, 17, 0, tzinfo=timezone.utc)
DECISION_AT = datetime(2026, 9, 6, 16, 55, tzinfo=timezone.utc)


def _league(
    league_id: str,
    nickname: str,
    platform: FantasyPlatform,
) -> FantasyLeague:
    return FantasyLeague(
        id=league_id,
        name=f"{nickname} League",
        nickname=nickname,
        platform=platform,
        roster_id="roster-1",
        roster_rules={},
        scoring_settings={},
    )


def _player(
    league: FantasyLeague,
    player_id: str,
    name: str,
    *,
    starter: bool,
    position: str = "RB",
) -> FantasyPlayer:
    return FantasyPlayer(
        platform_player_id=player_id,
        name=name,
        nfl_team="NE",
        position=position,
        league_id=league.id,
        league_name=league.name,
        platform=league.platform,
        lineup_slot=position if starter else "BN",
        eligible_slots=(position,),
        is_starter=starter,
        canonical_player_id=player_id,
    )


def _status(
    player_id: str,
    *,
    game_day: GameDayState = GameDayState.ACTIVE,
    designation: InjuryDesignation = InjuryDesignation.NONE,
    eligibility: RosterEligibility = RosterEligibility.ELIGIBLE,
    injury: str | None = None,
) -> NFLPlayerStatus:
    return NFLPlayerStatus(
        canonical_player_id=player_id,
        roster_eligibility=eligibility,
        game_day_state=game_day,
        injury_designation=designation,
        confidence=Confidence.OFFICIAL,
        decision_at=DECISION_AT,
        injury_description=injury,
    )


def _league_statuses(
    league: FantasyLeague,
    players_and_statuses: tuple[tuple[FantasyPlayer, NFLPlayerStatus | None], ...],
) -> FantasyLeagueStatuses:
    return FantasyLeagueStatuses(
        league=league,
        team_name="My Team",
        players=tuple(
            LeaguePlayerStatus(player, status, determine_fantasy_severity(player, status))
            for player, status in players_and_statuses
        ),
    )


def test_builds_one_text_email_grouped_by_urgency_then_league() -> None:
    sleeper = _league("sleeper", "Friends", FantasyPlatform.SLEEPER)
    espn = _league("espn", "Main", FantasyPlatform.ESPN)
    inactive = _player(sleeper, "inactive", "Unavailable Starter", starter=True)
    replacement = _player(sleeper, "replacement", "Healthy Backup", starter=False)
    healthy_bench = _player(sleeper, "hidden", "Healthy Bench", starter=False)
    risky = _player(espn, "risky", "Risky Starter", starter=True)
    healthy = _player(espn, "healthy", "Healthy Starter", starter=True, position="WR")
    sleeper_statuses = _league_statuses(
        sleeper,
        (
            (inactive, _status("inactive", game_day=GameDayState.INACTIVE, injury="knee")),
            (replacement, _status("replacement")),
            (healthy_bench, _status("hidden")),
        ),
    )
    espn_statuses = _league_statuses(
        espn,
        (
            (
                risky,
                _status(
                    "risky",
                    designation=InjuryDesignation.QUESTIONABLE,
                    injury="ankle",
                ),
            ),
            (healthy, _status("healthy")),
        ),
    )
    opportunity = DepthOpportunity(
        beneficiary_player_id="replacement",
        unavailable_player_ids=("blocker",),
        level=OpportunityLevel.PROMOTED,
        previous_order_in_slot=2,
        effective_order_in_slot=1,
        promoted_to_first_available=True,
        confidence=Confidence.HIGH,
        depth_chart_as_of=DECISION_AT,
        status_decision_at=DECISION_AT,
    )
    options = StarterReplacementOptions(
        starter=sleeper_statuses.players[0],
        candidates=(ReplacementCandidate(sleeper_statuses.players[1], opportunity),),
    )

    email = build_text_email(
        KICKOFF,
        (sleeper_statuses, espn_statuses),
        (options,),
    )

    assert email.subject == "Fantasy Check — 1:00 PM EDT kickoff in 5 min"
    assert email.body.startswith("1:00 PM EDT GAMES\n\nACTION NEEDED")
    assert email.body.index("ACTION NEEDED") < email.body.index("RISK")
    assert email.body.index("RISK") < email.body.index("NO ACTION")
    assert "Friends — Sleeper" in email.body
    assert "OFFICIALLY INACTIVE — knee" in email.body
    assert "1. Healthy Backup — RB — NE — Active; promoted in same depth slot" in email.body
    assert "Risky Starter — STARTING (RB)\nActive\nQuestionable — ankle" in email.body
    assert "Healthy Starter — STARTING (WR)\nActive" in email.body
    assert "Healthy Bench" not in email.body


def test_renders_abnormal_bench_status_as_information() -> None:
    league = _league("league", "Dynasty", FantasyPlatform.SLEEPER)
    bench = _player(league, "bench", "Unavailable Bench", starter=False)
    statuses = _league_statuses(
        league,
        ((bench, _status("bench", eligibility=RosterEligibility.INELIGIBLE)),),
    )

    body = build_text_email(KICKOFF, (statuses,)).body

    assert "BENCH NOTES" in body
    assert "Unavailable Bench — BENCH (RB)" in body
    assert "ROSTER INELIGIBLE" in body


def test_unknown_status_is_explicit_and_never_rendered_as_healthy() -> None:
    league = _league("league", "Main", FantasyPlatform.ESPN)
    player = _player(league, "missing", "Needs Verification", starter=True)
    statuses = _league_statuses(league, ((player, None),))

    body = build_text_email(KICKOFF, (statuses,)).body

    assert "RISK" in body
    assert "STATUS UNKNOWN — NFL status unavailable" in body
    assert "Active" not in body


def test_renders_empty_replacement_result_without_inventing_an_option() -> None:
    league = _league("league", "Friends", FantasyPlatform.SLEEPER)
    starter = _player(league, "starter", "Unavailable Starter", starter=True)
    statuses = _league_statuses(
        league,
        ((starter, _status("starter", designation=InjuryDesignation.OUT)),),
    )
    options = StarterReplacementOptions(statuses.players[0], ())

    body = build_text_email(KICKOFF, (statuses,), (options,)).body

    assert "Suggested replacements:\nNone verified and unlocked." in body


def test_uses_requested_timezone_and_validates_inputs() -> None:
    email = build_text_email(
        KICKOFF,
        (),
        display_timezone=ZoneInfo("America/Los_Angeles"),
        minutes_before_kickoff=10,
    )
    assert email.subject == "Fantasy Check — 10:00 AM PDT kickoff in 10 min"
    assert email.body == "10:00 AM PDT GAMES"

    with pytest.raises(ValueError, match="kickoff must be timezone-aware"):
        build_text_email(KICKOFF.replace(tzinfo=None), ())
    with pytest.raises(ValueError, match="minutes_before_kickoff"):
        build_text_email(KICKOFF, (), minutes_before_kickoff=-1)


def test_rejects_duplicate_replacement_options_for_one_starter() -> None:
    league = _league("league", "Friends", FantasyPlatform.SLEEPER)
    starter = _player(league, "starter", "Unavailable Starter", starter=True)
    statuses = _league_statuses(
        league,
        ((starter, _status("starter", designation=InjuryDesignation.OUT)),),
    )
    options = StarterReplacementOptions(statuses.players[0], ())

    with pytest.raises(ValueError, match="duplicate replacement options"):
        build_text_email(KICKOFF, (statuses,), (options, options))


def test_builds_html_email_with_matching_sections_and_replacements() -> None:
    sleeper = _league("sleeper", "Friends", FantasyPlatform.SLEEPER)
    espn = _league("espn", "Main", FantasyPlatform.ESPN)
    inactive = _player(sleeper, "inactive", "Unavailable Starter", starter=True)
    replacement = _player(sleeper, "replacement", "Healthy Backup", starter=False)
    risky = _player(espn, "risky", "Risky Starter", starter=True)
    healthy_bench = _player(espn, "healthy-bench", "Healthy Bench", starter=False)
    sleeper_statuses = _league_statuses(
        sleeper,
        (
            (inactive, _status("inactive", game_day=GameDayState.INACTIVE)),
            (replacement, _status("replacement")),
        ),
    )
    espn_statuses = _league_statuses(
        espn,
        (
            (risky, _status("risky", designation=InjuryDesignation.QUESTIONABLE)),
            (healthy_bench, _status("healthy-bench")),
        ),
    )
    options = StarterReplacementOptions(
        sleeper_statuses.players[0],
        (ReplacementCandidate(sleeper_statuses.players[1]),),
    )

    email = build_html_email(
        KICKOFF,
        (sleeper_statuses, espn_statuses),
        (options,),
    )

    assert email.subject == "Fantasy Check — 1:00 PM EDT kickoff in 5 min"
    assert email.body.startswith("<!doctype html>")
    assert email.body.index("ACTION NEEDED") < email.body.index("RISK")
    assert "Friends — Sleeper" in email.body
    assert "Unavailable Starter — STARTING (RB)" in email.body
    assert "<li>OFFICIALLY INACTIVE</li>" in email.body
    assert "<li>Healthy Backup — RB — NE — Active</li>" in email.body
    assert "Risky Starter — STARTING (RB)" in email.body
    assert "Healthy Bench" not in email.body


def test_html_email_escapes_dynamic_content_and_labels_unknown_status() -> None:
    league = _league("league", "Friends & <Family>", FantasyPlatform.SLEEPER)
    player = _player(league, "missing", "A & B <script>", starter=True)
    statuses = _league_statuses(league, ((player, None),))

    email = build_html_email(KICKOFF, (statuses,))

    assert "Friends &amp; &lt;Family&gt; — Sleeper" in email.body
    assert "A &amp; B &lt;script&gt; — STARTING (RB)" in email.body
    assert "<script>" not in email.body
    assert "<li>STATUS UNKNOWN — NFL status unavailable</li>" in email.body


def test_html_email_uses_shared_input_validation() -> None:
    with pytest.raises(ValueError, match="kickoff must be timezone-aware"):
        build_html_email(KICKOFF.replace(tzinfo=None), ())
    with pytest.raises(ValueError, match="minutes_before_kickoff"):
        build_html_email(KICKOFF, (), minutes_before_kickoff=-1)
