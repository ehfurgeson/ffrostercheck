import polars as pl

from app.models import FantasyPlatform
from app.nfl.identity import PlayerIdentityResolver, ResolutionMethod, normalize_name


def _resolver(
    *,
    manual_overrides: dict[tuple[str, str], str] | None = None,
) -> PlayerIdentityResolver:
    fantasy_ids = pl.DataFrame(
        [
            {
                "gsis_id": "00-001",
                "name": "D'Andre Swift Jr.",
                "team": "DET",
                "position": "RB",
                "espn_id": 101,
                "sleeper_id": 201,
            },
            {
                "gsis_id": "00-002",
                "name": "Chris Example",
                "team": "KCC",
                "position": "WR",
                "espn_id": 102,
                "sleeper_id": 202,
            },
            {
                "gsis_id": "00-003",
                "name": "Chris Example",
                "team": "LVR",
                "position": "WR",
                "espn_id": 103,
                "sleeper_id": 203,
            },
        ]
    )
    players = pl.DataFrame(
        [
            {
                "gsis_id": "00-001",
                "display_name": "D'Andre Swift",
                "position": "RB",
                "latest_team": "CHI",
                "espn_id": "101",
            },
            {
                "gsis_id": "00-004",
                "display_name": "Unique Player",
                "position": "TE",
                "latest_team": "GB",
                "espn_id": "104",
            },
        ]
    )
    rosters = pl.DataFrame(
        [
            {
                "season": 2026,
                "week": 1,
                "team": "CHI",
                "position": "RB",
                "full_name": "D'Andre Swift",
                "gsis_id": "00-001",
                "espn_id": "101",
                "sleeper_id": "201",
            },
            {
                "season": 2026,
                "week": 1,
                "team": "KC",
                "position": "WR",
                "full_name": "Chris Example",
                "gsis_id": "00-002",
                "espn_id": "102",
                "sleeper_id": "202",
            },
            {
                "season": 2026,
                "week": 1,
                "team": "LV",
                "position": "WR",
                "full_name": "Chris Example",
                "gsis_id": "00-003",
                "espn_id": "103",
                "sleeper_id": "203",
            },
        ]
    )
    return PlayerIdentityResolver.from_nflverse(
        fantasy_player_ids=fantasy_ids,
        players=players,
        rosters=rosters,
        manual_overrides=manual_overrides,
    )


def test_crosswalk_prefers_current_roster_context_and_normalizes_ids() -> None:
    resolver = _resolver()

    resolution = resolver.resolve_espn_player(
        player_id="101",
        name="Ignored Name",
        team="DET",
        position="RB",
    )

    assert resolution.method is ResolutionMethod.PLATFORM_ID
    assert resolution.identity is not None
    assert resolution.identity.gsis_id == "00-001"
    assert resolution.identity.name == "D'Andre Swift"
    assert resolution.identity.team == "CHI"


def test_sleeper_id_resolves_to_the_same_canonical_player() -> None:
    resolver = _resolver()

    resolution = resolver.resolve_sleeper_player(
        player_id="201",
        name="D'Andre Swift Jr.",
        team="CHI",
        position="RB",
    )

    assert resolution.identity is not None
    assert resolution.identity.gsis_id == "00-001"
    assert resolution.method is ResolutionMethod.PLATFORM_ID


def test_name_fallback_normalizes_punctuation_suffix_and_position_family() -> None:
    resolver = _resolver()

    resolution = resolver.resolve_name_team_position(
        name="DAndre Swift, Jr",
        team="CHI",
        position="HB",
    )

    assert normalize_name("D'Andre Swift Jr.") == "dandreswift"
    assert resolution.identity is not None
    assert resolution.identity.gsis_id == "00-001"
    assert resolution.method is ResolutionMethod.NAME_TEAM_POSITION


def test_team_and_position_constraints_disambiguate_duplicate_names() -> None:
    resolution = _resolver().resolve_name_team_position(
        name="Chris Example",
        team="KCC",
        position="WR",
    )

    assert resolution.identity is not None
    assert resolution.identity.gsis_id == "00-002"


def test_team_transition_fallback_must_be_explicit_and_unique() -> None:
    resolver = _resolver()

    disabled = resolver.resolve_name_team_position(
        name="Unique Player",
        team="NYJ",
        position="TE",
    )
    enabled = resolver.resolve_name_team_position(
        name="Unique Player",
        team="NYJ",
        position="TE",
        allow_team_transition=True,
    )

    assert not disabled.resolved
    assert enabled.identity is not None
    assert enabled.identity.gsis_id == "00-004"
    assert enabled.method is ResolutionMethod.NAME_POSITION_TEAM_TRANSITION


def test_ambiguous_name_is_never_guessed_during_team_transition() -> None:
    resolution = _resolver().resolve_name_team_position(
        name="Chris Example",
        team="NYJ",
        position="WR",
        allow_team_transition=True,
    )

    assert not resolution.resolved
    assert resolution.method is ResolutionMethod.UNRESOLVED


def test_manual_override_resolves_only_to_a_known_gsis_id() -> None:
    resolver = _resolver(manual_overrides={("sleeper", "missing-id"): "00-004"})

    resolution = resolver.resolve_sleeper_player(
        player_id="missing-id",
        name="Different Name",
        team="NYJ",
        position="TE",
    )

    assert resolution.identity is not None
    assert resolution.identity.gsis_id == "00-004"
    assert resolution.method is ResolutionMethod.MANUAL_OVERRIDE


def test_team_defenses_receive_stable_non_player_identities() -> None:
    resolution = _resolver().resolve(
        platform=FantasyPlatform.ESPN,
        platform_player_id="-16002",
        name="Buffalo Bills",
        team="BUF",
        position="D/ST",
    )

    assert resolution.identity is not None
    assert resolution.identity.canonical_player_id == "DST:BUF"
    assert resolution.identity.is_team_defense
    assert resolution.method is ResolutionMethod.TEAM_DEFENSE
