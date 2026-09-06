import pytest

from app.analysis import (
    EligibilityIssueState,
    EligibilityParseError,
    RosterSlotKind,
    parse_league_roster_eligibility,
)
from app.models import FantasyLeague, FantasyPlatform, FantasyPlayer


def _league(
    platform: FantasyPlatform,
    roster_rules: dict[str, object],
    *,
    league_id: str = "league-1",
) -> FantasyLeague:
    return FantasyLeague(
        id=league_id,
        name="Test League",
        nickname="Test",
        platform=platform,
        roster_id="roster-1",
        roster_rules=roster_rules,
        scoring_settings={},
    )


def _player(
    platform: FantasyPlatform,
    eligible_slots: tuple[str, ...],
    *,
    league_id: str = "league-1",
) -> FantasyPlayer:
    return FantasyPlayer(
        platform_player_id="player-1",
        name="Bench Player",
        nfl_team="BUF",
        position="RB",
        league_id=league_id,
        league_name="Test League",
        platform=platform,
        lineup_slot="BN" if platform is FantasyPlatform.SLEEPER else "BE",
        eligible_slots=eligible_slots,
        is_starter=False,
    )


def test_sleeper_rules_preserve_counts_and_slot_kinds() -> None:
    league = _league(
        FantasyPlatform.SLEEPER,
        {"roster_positions": ("QB", "RB", "RB", "FLEX", "BN", "BN", "IR", "TAXI")},
    )

    parsed = parse_league_roster_eligibility(league)

    assert [(slot.name, slot.count) for slot in parsed.slots] == [
        ("QB", 1),
        ("RB", 2),
        ("FLEX", 1),
        ("BN", 2),
        ("IR", 1),
        ("TAXI", 1),
    ]
    assert [slot.name for slot in parsed.starting_slots] == ["QB", "RB", "FLEX"]
    assert parsed.slot("BN").kind is RosterSlotKind.BENCH  # type: ignore[union-attr]
    assert parsed.slot("IR").kind is RosterSlotKind.RESERVE  # type: ignore[union-attr]
    assert parsed.slot("TAXI").kind is RosterSlotKind.TAXI  # type: ignore[union-attr]


def test_sleeper_flex_uses_preserved_multi_position_eligibility() -> None:
    rules = parse_league_roster_eligibility(
        _league(FantasyPlatform.SLEEPER, {"roster_positions": ("FLEX", "BN")})
    )
    dual_role_player = _player(FantasyPlatform.SLEEPER, ("RB", "WR"))
    quarterback = _player(FantasyPlatform.SLEEPER, ("QB",))

    assert rules.is_player_eligible(dual_role_player, "FLEX") is True
    assert rules.is_player_eligible(quarterback, "FLEX") is False


def test_sleeper_superflex_accepts_quarterbacks() -> None:
    rules = parse_league_roster_eligibility(
        _league(FantasyPlatform.SLEEPER, {"roster_positions": ("SUPER_FLEX", "BN")})
    )

    assert rules.is_player_eligible(_player(FantasyPlatform.SLEEPER, ("QB",)), "SUPER_FLEX")


def test_unknown_sleeper_starting_slot_is_visible_and_never_guessed() -> None:
    rules = parse_league_roster_eligibility(
        _league(FantasyPlatform.SLEEPER, {"roster_positions": ("MYSTERY", "BN")})
    )

    assert rules.is_complete is False
    assert rules.issues[0].state is EligibilityIssueState.UNKNOWN_STARTING_SLOT
    assert rules.is_player_eligible(_player(FantasyPlatform.SLEEPER, ("RB",)), "MYSTERY") is False


def test_espn_rules_use_exact_player_eligible_slot_ids() -> None:
    rules = parse_league_roster_eligibility(
        _league(
            FantasyPlatform.ESPN,
            {"lineupSlotCounts": {"0": 1, "2": 2, "7": 1, "20": 6, "21": 2}},
        )
    )
    running_back = _player(FantasyPlatform.ESPN, ("2", "7", "20"))

    assert [(slot.name, slot.count) for slot in rules.starting_slots] == [
        ("QB", 1),
        ("RB", 2),
        ("OP", 1),
    ]
    assert rules.is_player_eligible(running_back, "RB") is True
    assert rules.is_player_eligible(running_back, "OP") is True
    assert rules.is_player_eligible(running_back, "QB") is False
    assert rules.is_player_eligible(running_back, "BE") is False


def test_unknown_espn_slot_keeps_exact_id_eligibility_with_diagnostic() -> None:
    rules = parse_league_roster_eligibility(
        _league(FantasyPlatform.ESPN, {"lineupSlotCounts": {"99": 1, "20": 5}})
    )

    assert rules.issues[0].state is EligibilityIssueState.UNKNOWN_STARTING_SLOT
    assert rules.slot("SLOT_99") is not None
    assert rules.is_player_eligible(
        _player(FantasyPlatform.ESPN, ("99", "20")), "SLOT_99"
    )


def test_player_from_another_league_is_not_eligible() -> None:
    rules = parse_league_roster_eligibility(
        _league(FantasyPlatform.SLEEPER, {"roster_positions": ("RB", "BN")})
    )

    assert rules.is_player_eligible(
        _player(FantasyPlatform.SLEEPER, ("RB",), league_id="other-league"), "RB"
    ) is False


@pytest.mark.parametrize(
    ("platform", "roster_rules"),
    (
        (FantasyPlatform.SLEEPER, {}),
        (FantasyPlatform.SLEEPER, {"roster_positions": "QB"}),
        (FantasyPlatform.ESPN, {}),
        (FantasyPlatform.ESPN, {"lineupSlotCounts": {"QB": 1}}),
        (FantasyPlatform.ESPN, {"lineupSlotCounts": {"0": -1}}),
        (FantasyPlatform.ESPN, {"lineupSlotCounts": {"0": 0}}),
    ),
)
def test_invalid_roster_rules_fail_explicitly(
    platform: FantasyPlatform,
    roster_rules: dict[str, object],
) -> None:
    with pytest.raises(EligibilityParseError):
        parse_league_roster_eligibility(_league(platform, roster_rules))
