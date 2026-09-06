"""Parse platform roster rules into safe league-specific slot eligibility."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from app.models import FantasyLeague, FantasyPlatform, FantasyPlayer


class EligibilityParseError(ValueError):
    """Raised when a league's roster rules cannot be interpreted safely."""


class RosterSlotKind(str, Enum):
    STARTER = "starter"
    BENCH = "bench"
    RESERVE = "reserve"
    TAXI = "taxi"


class EligibilityIssueState(str, Enum):
    UNKNOWN_STARTING_SLOT = "unknown_starting_slot"


@dataclass(frozen=True)
class EligibilityIssue:
    state: EligibilityIssueState
    source_slot_id: str
    detail: str


@dataclass(frozen=True)
class RosterSlotRule:
    """One slot type configured by a fantasy league."""

    source_slot_id: str
    name: str
    count: int
    kind: RosterSlotKind
    eligible_positions: tuple[str, ...] = ()

    @property
    def is_starting(self) -> bool:
        return self.kind is RosterSlotKind.STARTER


@dataclass(frozen=True)
class LeagueRosterEligibility:
    """Normalized slot rules for one league, including unsupported-slot diagnostics."""

    league_id: str
    platform: FantasyPlatform
    slots: tuple[RosterSlotRule, ...]
    issues: tuple[EligibilityIssue, ...] = ()

    @property
    def starting_slots(self) -> tuple[RosterSlotRule, ...]:
        return tuple(slot for slot in self.slots if slot.is_starting)

    @property
    def is_complete(self) -> bool:
        return not self.issues

    def slot(self, name: str) -> RosterSlotRule | None:
        normalized_name = _normalize_label(name)
        return next(
            (slot for slot in self.slots if _normalize_label(slot.name) == normalized_name),
            None,
        )

    def is_player_eligible(self, player: FantasyPlayer, lineup_slot: str) -> bool:
        """Return eligibility without guessing across leagues or unknown slot types."""

        if player.league_id != self.league_id or player.platform is not self.platform:
            return False
        slot = self.slot(lineup_slot)
        if slot is None or not slot.is_starting:
            return False
        player_slots = {_normalize_label(value) for value in player.eligible_slots}
        if self.platform is FantasyPlatform.ESPN:
            return slot.source_slot_id in player_slots
        return bool(player_slots.intersection(slot.eligible_positions))


_SLEEPER_SLOT_POSITIONS: Mapping[str, tuple[str, ...]] = {
    "QB": ("QB",),
    "RB": ("RB",),
    "WR": ("WR",),
    "TE": ("TE",),
    "K": ("K",),
    "DEF": ("DEF",),
    "DL": ("DL",),
    "LB": ("LB",),
    "DB": ("DB",),
    "FLEX": ("RB", "WR", "TE"),
    "REC_FLEX": ("WR", "TE"),
    "WRRB_FLEX": ("RB", "WR"),
    "SUPER_FLEX": ("QB", "RB", "WR", "TE"),
    "IDP_FLEX": ("DL", "LB", "DB"),
}

_ESPN_SLOT_NAMES: Mapping[int, str] = {
    0: "QB",
    1: "TQB",
    2: "RB",
    3: "RB/WR",
    4: "WR",
    5: "WR/TE",
    6: "TE",
    7: "OP",
    8: "DT",
    9: "DE",
    10: "LB",
    11: "DL",
    12: "CB",
    13: "S",
    14: "DB",
    15: "DP",
    16: "D/ST",
    17: "K",
    18: "P",
    19: "HC",
    20: "BE",
    21: "IR",
    23: "FLEX",
}


def parse_league_roster_eligibility(league: FantasyLeague) -> LeagueRosterEligibility:
    """Parse the provider rules retained on a normalized fantasy league."""

    if league.platform is FantasyPlatform.SLEEPER:
        return _parse_sleeper_rules(league)
    if league.platform is FantasyPlatform.ESPN:
        return _parse_espn_rules(league)
    raise EligibilityParseError(f"unsupported fantasy platform: {league.platform!r}")


def _parse_sleeper_rules(league: FantasyLeague) -> LeagueRosterEligibility:
    raw_positions = league.roster_rules.get("roster_positions")
    if not isinstance(raw_positions, Sequence) or isinstance(raw_positions, (str, bytes)):
        raise EligibilityParseError(
            f"Sleeper league {league.id!r} is missing roster_positions"
        )
    positions = [_normalize_label(position) for position in raw_positions]
    if not positions or any(not position for position in positions):
        raise EligibilityParseError(
            f"Sleeper league {league.id!r} has invalid roster_positions"
        )

    counts = Counter(positions)
    ordered_positions = tuple(dict.fromkeys(positions))
    slots: list[RosterSlotRule] = []
    issues: list[EligibilityIssue] = []
    for position in ordered_positions:
        kind = _sleeper_slot_kind(position)
        eligible_positions = _SLEEPER_SLOT_POSITIONS.get(position, ())
        slots.append(
            RosterSlotRule(
                source_slot_id=position,
                name=position,
                count=counts[position],
                kind=kind,
                eligible_positions=eligible_positions,
            )
        )
        if kind is RosterSlotKind.STARTER and not eligible_positions:
            issues.append(
                EligibilityIssue(
                    state=EligibilityIssueState.UNKNOWN_STARTING_SLOT,
                    source_slot_id=position,
                    detail=(
                        f"Sleeper starting slot {position!r} has no known position mapping; "
                        "players will not be treated as eligible for it"
                    ),
                )
            )
    return LeagueRosterEligibility(
        league_id=league.id,
        platform=league.platform,
        slots=tuple(slots),
        issues=tuple(issues),
    )


def _parse_espn_rules(league: FantasyLeague) -> LeagueRosterEligibility:
    raw_counts = league.roster_rules.get("lineupSlotCounts")
    if not isinstance(raw_counts, Mapping) or not raw_counts:
        raise EligibilityParseError(
            f"ESPN league {league.id!r} is missing lineupSlotCounts"
        )

    slots: list[RosterSlotRule] = []
    issues: list[EligibilityIssue] = []
    for raw_slot_id, raw_count in raw_counts.items():
        slot_id = _positive_or_zero_int(raw_slot_id, "ESPN lineup slot ID", league.id)
        count = _positive_or_zero_int(raw_count, "ESPN lineup slot count", league.id)
        if count == 0:
            continue
        name = _ESPN_SLOT_NAMES.get(slot_id, f"SLOT_{slot_id}")
        kind = _espn_slot_kind(slot_id)
        slots.append(
            RosterSlotRule(
                source_slot_id=str(slot_id),
                name=name,
                count=count,
                kind=kind,
            )
        )
        if kind is RosterSlotKind.STARTER and slot_id not in _ESPN_SLOT_NAMES:
            issues.append(
                EligibilityIssue(
                    state=EligibilityIssueState.UNKNOWN_STARTING_SLOT,
                    source_slot_id=str(slot_id),
                    detail=(
                        f"ESPN starting slot ID {slot_id} has no known label; "
                        "player eligibility remains available by exact slot ID"
                    ),
                )
            )
    if not slots:
        raise EligibilityParseError(
            f"ESPN league {league.id!r} has no configured roster slots"
        )
    return LeagueRosterEligibility(
        league_id=league.id,
        platform=league.platform,
        slots=tuple(slots),
        issues=tuple(issues),
    )


def _sleeper_slot_kind(slot: str) -> RosterSlotKind:
    if slot == "BN":
        return RosterSlotKind.BENCH
    if slot == "IR":
        return RosterSlotKind.RESERVE
    if slot == "TAXI":
        return RosterSlotKind.TAXI
    return RosterSlotKind.STARTER


def _espn_slot_kind(slot_id: int) -> RosterSlotKind:
    if slot_id == 20:
        return RosterSlotKind.BENCH
    if slot_id == 21:
        return RosterSlotKind.RESERVE
    return RosterSlotKind.STARTER


def _positive_or_zero_int(value: Any, field: str, league_id: str) -> int:
    if isinstance(value, bool):
        raise EligibilityParseError(f"{field} is invalid for league {league_id!r}: {value!r}")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise EligibilityParseError(
            f"{field} is invalid for league {league_id!r}: {value!r}"
        ) from exc
    if parsed < 0:
        raise EligibilityParseError(f"{field} is invalid for league {league_id!r}: {value!r}")
    return parsed


def _normalize_label(value: Any) -> str:
    return str(value).strip().upper()
