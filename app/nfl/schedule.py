"""Map fantasy players to their next unstarted NFL game."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from app.models import FantasyPlayer, FantasyRoster, NFLGame
from app.nfl.identity import normalize_team
from app.nfl.nflverse import DataFrameLike


EASTERN = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
FANTASY_GAME_TYPES = frozenset({"REG", "WC", "DIV", "CON", "SB", "POST"})


class NextGameState(str, Enum):
    MATCHED = "matched"
    MISSING_TEAM = "missing_team"
    BYE = "bye"
    ALREADY_STARTED = "already_started"
    INVALID_SCHEDULE = "invalid_schedule"


@dataclass(frozen=True)
class InvalidScheduleRow:
    reason: str
    game_id: str | None = None
    home_team: str | None = None
    away_team: str | None = None

    def involves(self, team: str) -> bool:
        return team in {self.home_team, self.away_team}


@dataclass(frozen=True)
class NFLSchedule:
    """Validated regular-season and postseason games plus rejected rows."""

    games: tuple[NFLGame, ...]
    invalid_rows: tuple[InvalidScheduleRow, ...] = ()

    def games_for_team(self, team: str) -> tuple[NFLGame, ...]:
        return tuple(game for game in self.games if game.involves(team))

    def invalid_rows_for_team(self, team: str) -> tuple[InvalidScheduleRow, ...]:
        return tuple(row for row in self.invalid_rows if row.involves(team))


@dataclass(frozen=True)
class PlayerNextGame:
    player: FantasyPlayer
    state: NextGameState
    game: NFLGame | None = None
    detail: str | None = None

    @property
    def matched(self) -> bool:
        return self.state is NextGameState.MATCHED


@dataclass(frozen=True)
class NextGameAssignmentResult:
    assignments: tuple[PlayerNextGame, ...]
    schedule: NFLSchedule

    @property
    def matched(self) -> tuple[PlayerNextGame, ...]:
        return tuple(item for item in self.assignments if item.matched)

    @property
    def unmatched(self) -> tuple[PlayerNextGame, ...]:
        return tuple(item for item in self.assignments if not item.matched)

    @property
    def data_errors(self) -> tuple[PlayerNextGame, ...]:
        return tuple(
            item
            for item in self.assignments
            if item.state in {NextGameState.MISSING_TEAM, NextGameState.INVALID_SCHEDULE}
        )


def parse_nfl_schedule(frame: DataFrameLike) -> NFLSchedule:
    """Parse schedule rows without inventing kickoffs for incomplete data."""

    games: list[NFLGame] = []
    invalid_rows: list[InvalidScheduleRow] = []
    seen: dict[str, NFLGame] = {}
    for row in _rows(frame):
        game_type = _optional_text(row.get("game_type"))
        if game_type and game_type.upper() not in FANTASY_GAME_TYPES:
            continue

        parsed, error = _parse_game_row(row)
        if error is not None:
            invalid_rows.append(error)
            continue
        existing = seen.get(parsed.game_id)
        if existing is not None:
            if existing != parsed:
                invalid_rows.append(
                    InvalidScheduleRow(
                        reason=f"Conflicting rows share game_id {parsed.game_id}",
                        game_id=parsed.game_id,
                        home_team=parsed.home_team,
                        away_team=parsed.away_team,
                    )
                )
            continue
        seen[parsed.game_id] = parsed
        games.append(parsed)

    games.sort(key=lambda game: (game.kickoff, game.game_id))
    return NFLSchedule(tuple(games), tuple(invalid_rows))


def assign_next_games(
    rosters: Sequence[FantasyRoster],
    schedule: NFLSchedule,
    *,
    as_of: datetime,
) -> NextGameAssignmentResult:
    """Select each player's next unstarted game by normalized NFL team."""

    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")

    assignments = tuple(
        _assign_player(player, schedule, as_of=as_of)
        for roster in rosters
        for player in roster.players
    )
    return NextGameAssignmentResult(assignments, schedule)


def render_next_games(result: NextGameAssignmentResult) -> str:
    """Render next-game matches and unmatched players without guessing games."""

    lines = [
        f"Fantasy players: {len(result.assignments)}",
        f"Players with a next NFL game: {len(result.matched)}/{len(result.assignments)}",
        f"Unmatched: {len(result.unmatched)}",
    ]
    team_groups = _matched_teams(result.matched)
    if team_groups:
        lines.append("Next games by NFL team:")
        for team, game, count in team_groups:
            opponent = game.opponent(team) or "UNK"
            lines.append(
                f"  {team} vs {opponent} — {game.game_id} — "
                f"{_format_kickoff(game.kickoff)} ({count} players)"
            )
    for item in result.unmatched:
        team = item.player.nfl_team or "none"
        lines.append(
            f"  {item.state.value}: {item.player.league_name} — {item.player.name} — "
            f"{team} — {item.detail}"
        )
    return "\n".join(lines)


def _assign_player(
    player: FantasyPlayer,
    schedule: NFLSchedule,
    *,
    as_of: datetime,
) -> PlayerNextGame:
    team = normalize_team(player.nfl_team)
    if not team:
        return PlayerNextGame(
            player=player,
            state=NextGameState.MISSING_TEAM,
            detail="Player has no current NFL team",
        )

    team_games = schedule.games_for_team(team)
    invalid_rows = schedule.invalid_rows_for_team(team)
    if not team_games:
        if invalid_rows:
            return PlayerNextGame(
                player=player,
                state=NextGameState.INVALID_SCHEDULE,
                detail=invalid_rows[0].reason,
            )
        return PlayerNextGame(
            player=player,
            state=NextGameState.BYE,
            detail=f"{team} has no remaining regular-season or postseason games",
        )

    upcoming = [game for game in team_games if game.kickoff > as_of]
    if upcoming:
        return PlayerNextGame(
            player=player,
            state=NextGameState.MATCHED,
            game=upcoming[0],
        )

    latest = team_games[-1]
    return PlayerNextGame(
        player=player,
        state=NextGameState.ALREADY_STARTED,
        game=latest,
        detail=f"{team}'s latest game {latest.game_id} already started",
    )


def _parse_game_row(row: Mapping[str, Any]) -> tuple[NFLGame | None, InvalidScheduleRow | None]:
    game_id = _optional_text(row.get("game_id"))
    game_type = _optional_text(row.get("game_type"))
    home_team = normalize_team(_optional_text(row.get("home_team")))
    away_team = normalize_team(_optional_text(row.get("away_team")))
    season = _optional_int(row.get("season"))
    week = _optional_int(row.get("week"))

    if not game_id:
        return None, InvalidScheduleRow(
            reason="Schedule row is missing game_id",
            home_team=home_team,
            away_team=away_team,
        )
    if not game_type:
        return None, InvalidScheduleRow(
            reason=f"{game_id} is missing game_type",
            game_id=game_id,
            home_team=home_team,
            away_team=away_team,
        )
    if season is None or week is None:
        return None, InvalidScheduleRow(
            reason=f"{game_id} is missing season or week",
            game_id=game_id,
            home_team=home_team,
            away_team=away_team,
        )
    if not home_team or not away_team:
        return None, InvalidScheduleRow(
            reason=f"{game_id} is missing home or away team",
            game_id=game_id,
            home_team=home_team,
            away_team=away_team,
        )
    if home_team == away_team:
        return None, InvalidScheduleRow(
            reason=f"{game_id} has the same home and away team",
            game_id=game_id,
            home_team=home_team,
            away_team=away_team,
        )

    kickoff, kickoff_error = _parse_kickoff(row.get("gameday"), row.get("gametime"))
    if kickoff is None:
        return None, InvalidScheduleRow(
            reason=f"{game_id} {kickoff_error}",
            game_id=game_id,
            home_team=home_team,
            away_team=away_team,
        )
    return (
        NFLGame(
            game_id=game_id,
            season=season,
            week=week,
            game_type=game_type.upper(),
            home_team=home_team,
            away_team=away_team,
            kickoff=kickoff,
        ),
        None,
    )


def _parse_kickoff(gameday: Any, gametime: Any) -> tuple[datetime | None, str]:
    parsed_date = _parse_gameday(gameday)
    if parsed_date is None:
        return None, "has an invalid or missing Eastern gameday"
    parsed_time = _parse_gametime(gametime)
    if parsed_time is None:
        return None, "has an invalid or missing Eastern gametime"
    kickoff = datetime.combine(parsed_date, parsed_time, tzinfo=EASTERN).astimezone(UTC)
    return kickoff, ""


def _parse_gameday(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _optional_text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _parse_gametime(value: Any) -> time | None:
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    if isinstance(value, datetime):
        return value.time()
    text = _optional_text(value)
    if not text:
        return None
    parts = text.split(":")
    if len(parts) not in {2, 3}:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    return time(hour, minute, second)


def _matched_teams(
    assignments: Sequence[PlayerNextGame],
) -> list[tuple[str, NFLGame, int]]:
    grouped: dict[str, tuple[NFLGame, int]] = {}
    for item in assignments:
        team = item.player.nfl_team
        game = item.game
        if not team or game is None:
            continue
        existing = grouped.get(team)
        if existing is None:
            grouped[team] = (game, 1)
        else:
            grouped[team] = (existing[0], existing[1] + 1)
    return sorted(
        ((team, game, count) for team, (game, count) in grouped.items()),
        key=lambda item: (item[1].kickoff, item[0]),
    )


def _format_kickoff(kickoff: datetime) -> str:
    return kickoff.astimezone(EASTERN).strftime("%Y-%m-%d %H:%M ET")


def _rows(frame: DataFrameLike) -> list[Mapping[str, Any]]:
    iter_rows = getattr(frame, "iter_rows", None)
    if not callable(iter_rows):
        raise TypeError("Schedule parsing requires a Polars-like frame with iter_rows")
    return list(iter_rows(named=True))


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
