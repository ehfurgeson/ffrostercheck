"""Official NFL.com weekly injury-report discovery and HTML parsing."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag

from app.models import (
    GameSourceReport,
    InjuryDesignation,
    RelevantGame,
    ReportState,
    SourceResult,
)
from app.nfl.identity import normalize_team
from app.nfl.teams import resolve_team_label


SOURCE_NAME = "nfl_injuries"
INJURY_REPORT_URL = "https://www.nfl.com/injuries/league/{season}/reg{week}"
EASTERN = ZoneInfo("America/New_York")
URL_PATH_RE = re.compile(r"^/injuries/league/(?P<season>\d{4})/reg(?P<week>\d{1,2})/?$")
WEEK_HEADING_RE = re.compile(r"Injuries\s*[-–]\s*WEEK\s+(\d+)", re.IGNORECASE)
ORDINAL_RE = re.compile(r"(\d+)(ST|ND|RD|TH)\b", re.IGNORECASE)
REQUIRED_COLUMNS = ("player", "position", "injuries", "practice status", "game status")
DESIGNATION_ALIASES = {
    "out": InjuryDesignation.OUT,
    "doubtful": InjuryDesignation.DOUBTFUL,
    "questionable": InjuryDesignation.QUESTIONABLE,
}


@dataclass(frozen=True)
class ParsedInjuryPlayer:
    name: str
    team: str
    position: str | None
    injuries: str | None
    practice_status: str | None
    game_status: str | None
    injury_designation: InjuryDesignation


@dataclass(frozen=True)
class ParsedInjuryGame:
    teams: frozenset[str]
    players: dict[str, tuple[ParsedInjuryPlayer, ...]]
    date_heading: str | None = None
    kickoff_label: str | None = None
    errors: tuple[str, ...] = ()

    @property
    def parsed_teams(self) -> frozenset[str]:
        return frozenset(self.players)


@dataclass(frozen=True)
class InjuryReportDocument:
    report_state: ReportState
    source_url: str | None
    season: int
    week: int
    games: tuple[ParsedInjuryGame, ...]
    retrieved_at: datetime
    visible_week: int | None = None
    published_at: datetime | None = None
    source_updated_at: datetime | None = None
    http_cache_age_seconds: int | None = None
    raw_content_hash: str | None = None
    errors: tuple[str, ...] = ()

    @property
    def parsed_teams(self) -> frozenset[str]:
        return frozenset(team for game in self.games for team in game.parsed_teams)


class NFLInjuryReportSource:
    """Fetch and parse official NFL.com weekly injury tables."""

    name = SOURCE_NAME
    priority = 90

    def __init__(
        self,
        *,
        season: int,
        week: int,
        http_client: httpx.Client | None = None,
        source_url: str | None = None,
        html: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.season = season
        self.week = week
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "fantasy-watchdog/0.1"},
        )
        self._source_url = source_url or injury_report_url(season, week)
        self._html = html
        self._document: InjuryReportDocument | None = None

    def __enter__(self) -> NFLInjuryReportSource:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch_game(
        self, game: RelevantGame, *, validate_date: bool = True
    ) -> GameSourceReport:
        document = self.load_document()
        expected = frozenset(
            team
            for team in (
                normalize_team(game.home_team),
                normalize_team(game.away_team),
            )
            if team
        )
        parsed_game = _matching_game(document, expected)
        parsed_for_game = parsed_game.parsed_teams if parsed_game else frozenset()
        report_state = _game_report_state(document, expected, parsed_game)
        errors = list(document.errors)
        if parsed_game:
            errors.extend(parsed_game.errors)
        if report_state is ReportState.PARTIAL and expected - parsed_for_game:
            missing = ", ".join(sorted(expected - parsed_for_game))
            errors.append(f"Injury report missing team table(s): {missing}")
        if (
            validate_date
            and parsed_game
            and not _date_heading_matches(
                parsed_game.date_heading, game.kickoff, season=self.season
            )
        ):
            errors.append(
                "Injury-report game date "
                f"{parsed_game.date_heading or 'missing'} does not match kickoff "
                f"{game.kickoff.astimezone(EASTERN).isoformat()}"
            )
            if report_state is ReportState.COMPLETE:
                report_state = ReportState.PARTIAL
        player_results = ()
        if report_state in {ReportState.COMPLETE, ReportState.PARTIAL} and parsed_game:
            player_results = tuple(
                _injury_result(player, document, report_state)
                for team in sorted(parsed_for_game)
                for player in parsed_game.players[team]
            )
        return GameSourceReport(
            source=self.name,
            game_id=game.game_id,
            report_state=report_state,
            expected_teams=expected,
            parsed_teams=parsed_for_game,
            player_results=player_results,
            retrieved_at=document.retrieved_at,
            source_updated_at=document.source_updated_at,
            errors=tuple(errors),
            source_url=document.source_url,
            published_at=document.published_at,
            http_cache_age_seconds=document.http_cache_age_seconds,
            raw_content_hash=document.raw_content_hash,
        )

    def load_document(self) -> InjuryReportDocument:
        if self._document is None:
            self._document = self._fetch_document()
        return self._document

    def _fetch_document(self) -> InjuryReportDocument:
        retrieved_at = datetime.now(timezone.utc)
        if self._html is not None:
            return parse_injury_report(
                self._html,
                season=self.season,
                week=self.week,
                source_url=self._source_url,
                retrieved_at=retrieved_at,
            )
        try:
            response = self._client.get(self._source_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return InjuryReportDocument(
                report_state=ReportState.FAILED,
                source_url=self._source_url,
                season=self.season,
                week=self.week,
                games=(),
                retrieved_at=retrieved_at,
                errors=(f"NFL injury report request failed: {exc}",),
            )
        return parse_injury_report(
            response.text,
            season=self.season,
            week=self.week,
            source_url=str(response.url),
            retrieved_at=retrieved_at,
            http_cache_age_seconds=_cache_age(response),
            source_updated_at=_http_datetime(response.headers.get("last-modified")),
        )


def injury_report_url(season: int, week: int) -> str:
    return INJURY_REPORT_URL.format(season=season, week=week)


def parse_injury_report(
    html: str,
    *,
    season: int,
    week: int,
    source_url: str | None,
    retrieved_at: datetime,
    http_cache_age_seconds: int | None = None,
    source_updated_at: datetime | None = None,
) -> InjuryReportDocument:
    """Parse game containers and team tables without trusting the page title."""

    soup = BeautifulSoup(html, "html.parser")
    errors: list[str] = []
    url_error = _url_path_error(source_url, season=season, week=week)
    if url_error:
        errors.append(url_error)
    visible_week = _visible_week(soup)
    if visible_week is None:
        errors.append("Visible injury-report week heading was not found")
    elif visible_week != week:
        return InjuryReportDocument(
            report_state=ReportState.FAILED,
            source_url=source_url,
            season=season,
            week=week,
            games=(),
            retrieved_at=retrieved_at,
            visible_week=visible_week,
            source_updated_at=source_updated_at,
            http_cache_age_seconds=http_cache_age_seconds,
            raw_content_hash=_content_hash(html),
            errors=tuple(
                errors
                + [
                    f"Visible week heading is WEEK {visible_week}, expected WEEK {week}"
                ]
            ),
        )
    if url_error:
        return InjuryReportDocument(
            report_state=ReportState.FAILED,
            source_url=source_url,
            season=season,
            week=week,
            games=(),
            retrieved_at=retrieved_at,
            visible_week=visible_week,
            source_updated_at=source_updated_at,
            http_cache_age_seconds=http_cache_age_seconds,
            raw_content_hash=_content_hash(html),
            errors=tuple(errors),
        )

    tables = soup.find_all("table")
    if not tables:
        return InjuryReportDocument(
            report_state=ReportState.NOT_YET_PUBLISHED,
            source_url=source_url,
            season=season,
            week=week,
            games=(),
            retrieved_at=retrieved_at,
            visible_week=visible_week,
            source_updated_at=source_updated_at,
            http_cache_age_seconds=http_cache_age_seconds,
            raw_content_hash=_content_hash(html),
            errors=tuple(errors),
        )

    games: list[ParsedInjuryGame] = []
    for unit in soup.select("section.nfl-o-injury-report__unit"):
        parsed = _parse_game_unit(unit)
        if parsed is not None:
            games.append(parsed)
    if not games:
        errors.append("Injury tables were present but no game containers were parsed")

    return InjuryReportDocument(
        report_state=ReportState.PARTIAL,
        source_url=source_url,
        season=season,
        week=week,
        games=tuple(games),
        retrieved_at=retrieved_at,
        visible_week=visible_week,
        source_updated_at=source_updated_at,
        http_cache_age_seconds=http_cache_age_seconds,
        raw_content_hash=_content_hash(html),
        errors=tuple(errors),
    )


def parse_game_status(value: str | None) -> InjuryDesignation:
    """Map the Game Status column without inventing a final active/inactive state."""

    if not value:
        return InjuryDesignation.NONE
    return DESIGNATION_ALIASES.get(value.casefold(), InjuryDesignation.UNKNOWN)


def render_injury_document(document: InjuryReportDocument) -> str:
    lines = [
        f"NFL injury report: {document.report_state.value}",
        f"Requested: {document.season} WEEK {document.week}",
        f"Games parsed: {len(document.games)}",
        f"Teams with tables: {len(document.parsed_teams)}",
    ]
    if document.visible_week is not None:
        lines.append(f"Visible week: {document.visible_week}")
    if document.source_url:
        lines.append(f"Source: {document.source_url}")
    for game in document.games:
        matchup = " vs ".join(sorted(game.teams)) or "unknown matchup"
        date = f" — {game.date_heading}" if game.date_heading else ""
        lines.append(f"  {matchup}{date}")
        for team in sorted(game.players):
            lines.append(f"    {team}: {len(game.players[team])} listed")
            for player in game.players[team]:
                position = f"{player.position} " if player.position else ""
                designation = player.injury_designation.value
                lines.append(f"      {position}{player.name} — {designation}")
    if not document.games:
        lines.append("  No injury tables were parsed")
    for error in document.errors:
        lines.append(f"  error: {error}")
    return "\n".join(lines)


def render_injury_report(report: GameSourceReport) -> str:
    lines = [
        f"NFL injury report: {report.report_state.value}",
        f"Game: {report.game_id}",
        f"Expected teams: {', '.join(sorted(report.expected_teams)) or 'none'}",
        f"Parsed teams: {', '.join(sorted(report.parsed_teams)) or 'none'}",
    ]
    if report.source_url:
        lines.append(f"Source: {report.source_url}")
    for result in report.player_results:
        identity = result.player_name or "Unknown player"
        position = f"{result.position} " if result.position else ""
        team = f" ({result.nfl_team})" if result.nfl_team else ""
        designation = (
            result.injury_designation.value if result.injury_designation else "unknown"
        )
        lines.append(f"  {designation.upper()}: {position}{identity}{team}")
    if not report.player_results:
        lines.append("  No injury designations inferred as healthy or active")
    for error in report.errors:
        lines.append(f"  error: {error}")
    return "\n".join(lines)


def _parse_game_unit(unit: Tag) -> ParsedInjuryGame | None:
    matchup = unit.select_one(".nfl-c-matchup-strip")
    if matchup is None:
        return None
    teams = [
        resolve_team_label(node.get_text(" ", strip=True))
        for node in matchup.select(".nfl-c-matchup-strip__team-abbreviation")
    ]
    resolved = [team for team in teams if team]
    if len(resolved) != 2:
        return None
    date_heading = _previous_date_heading(unit)
    kickoff_label = _text_or_none(matchup.select_one(".nfl-c-matchup-strip__date-info"))
    players: dict[str, tuple[ParsedInjuryPlayer, ...]] = {}
    errors: list[str] = []
    for title, table in _team_tables(unit):
        team = resolve_team_label(title)
        if team is None or team not in resolved:
            errors.append(f"Injury table heading {title!r} does not match the matchup")
            continue
        parsed_rows, table_errors = _parse_team_table(table, team)
        errors.extend(table_errors)
        if parsed_rows is not None:
            players[team] = parsed_rows
    return ParsedInjuryGame(
        teams=frozenset(resolved),
        players=players,
        date_heading=date_heading,
        kickoff_label=kickoff_label,
        errors=tuple(errors),
    )


def _team_tables(unit: Tag) -> list[tuple[str, Tag]]:
    pairs: list[tuple[str, Tag]] = []
    pending_title: str | None = None
    for child in unit.children:
        if not isinstance(child, Tag):
            continue
        title = child.select_one(".d3-o-section-sub-title")
        if title is not None:
            pending_title = title.get_text(" ", strip=True)
            continue
        table = child if child.name == "table" else child.find("table")
        if table is not None and pending_title:
            pairs.append((pending_title, table))
            pending_title = None
    return pairs


def _parse_team_table(
    table: Tag, team: str
) -> tuple[tuple[ParsedInjuryPlayer, ...] | None, list[str]]:
    headers = [
        " ".join(cell.get_text(" ", strip=True).split()).casefold()
        for cell in table.find_all("th")
    ]
    missing = [column for column in REQUIRED_COLUMNS if column not in headers]
    if missing:
        return None, [f"{team} table is missing column(s): {', '.join(missing)}"]
    indexes = {name: headers.index(name) for name in REQUIRED_COLUMNS}
    players: list[ParsedInjuryPlayer] = []
    errors: list[str] = []
    body = table.find("tbody") or table
    for row in body.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < len(headers):
            continue
        name = cells[indexes["player"]].get_text(" ", strip=True)
        if not name:
            errors.append(f"{team} table has a row without a player name")
            continue
        game_status = _text_or_none(cells[indexes["game status"]])
        players.append(
            ParsedInjuryPlayer(
                name=name,
                team=team,
                position=_text_or_none(cells[indexes["position"]]),
                injuries=_text_or_none(cells[indexes["injuries"]]),
                practice_status=_text_or_none(cells[indexes["practice status"]]),
                game_status=game_status,
                injury_designation=parse_game_status(game_status),
            )
        )
    return tuple(players), errors


def _matching_game(
    document: InjuryReportDocument, expected: frozenset[str]
) -> ParsedInjuryGame | None:
    for game in document.games:
        if game.teams == expected:
            return game
    return None


def _game_report_state(
    document: InjuryReportDocument,
    expected: frozenset[str],
    parsed_game: ParsedInjuryGame | None,
) -> ReportState:
    if document.report_state is ReportState.FAILED:
        return ReportState.FAILED
    if document.report_state is ReportState.NOT_YET_PUBLISHED or not document.games:
        return ReportState.NOT_YET_PUBLISHED
    if parsed_game is None:
        return ReportState.PARTIAL
    if expected and parsed_game.parsed_teams == expected:
        return ReportState.COMPLETE
    return ReportState.PARTIAL


def _injury_result(
    player: ParsedInjuryPlayer,
    document: InjuryReportDocument,
    report_state: ReportState,
) -> SourceResult:
    details = [
        part
        for part in (player.game_status, player.injuries, player.practice_status)
        if part
    ]
    return SourceResult(
        source=SOURCE_NAME,
        source_url=document.source_url,
        success=True,
        report_state=report_state,
        retrieved_at=document.retrieved_at,
        injury_designation=player.injury_designation,
        detail=" — ".join(details) or None,
        player_name=player.name,
        nfl_team=player.team,
        position=player.position,
        published_at=document.published_at,
        source_updated_at=document.source_updated_at,
        http_cache_age_seconds=document.http_cache_age_seconds,
        raw_content_hash=document.raw_content_hash,
    )


def _url_path_error(source_url: str | None, *, season: int, week: int) -> str | None:
    if not source_url:
        return "Injury report URL is missing"
    parsed = urlparse(source_url)
    if parsed.scheme not in {"http", "https"}:
        return None
    match = URL_PATH_RE.match(parsed.path)
    if match is None:
        return f"Injury report URL path is not a weekly report: {parsed.path}"
    path_season = int(match.group("season"))
    path_week = int(match.group("week"))
    if path_season != season or path_week != week:
        return (
            f"Injury report URL path is {path_season} WEEK {path_week}, "
            f"expected {season} WEEK {week}"
        )
    return None


def _visible_week(soup: BeautifulSoup) -> int | None:
    for heading in soup.select("h2.nfl-c-content-header__roofline"):
        match = WEEK_HEADING_RE.search(heading.get_text(" ", strip=True))
        if match:
            return int(match.group(1))
    for heading in soup.find_all("h2"):
        match = WEEK_HEADING_RE.search(heading.get_text(" ", strip=True))
        if match:
            return int(match.group(1))
    return None


def _previous_date_heading(unit: Tag) -> str | None:
    for sibling in unit.previous_siblings:
        if not isinstance(sibling, Tag):
            continue
        if sibling.name == "h2":
            text = sibling.get_text(" ", strip=True)
            if text and WEEK_HEADING_RE.search(text) is None:
                return text
            return None
        if sibling.name in {"h1", "section"}:
            break
    return None


def _date_heading_matches(heading: str | None, kickoff: datetime, *, season: int) -> bool:
    if not heading:
        return True
    local = kickoff.astimezone(EASTERN)
    cleaned = ORDINAL_RE.sub(r"\1", " ".join(heading.split()))
    for year in (local.year, season, season + 1):
        for fmt in ("%A, %B %d", "%B %d"):
            try:
                parsed = datetime.strptime(cleaned, fmt).replace(year=year)
            except ValueError:
                continue
            if parsed.date() == local.date():
                return True
    return False


def _text_or_none(value: Tag | str | None) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else value.get_text(" ", strip=True)
    text = " ".join(text.split())
    return text or None


def _content_hash(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def _cache_age(response: httpx.Response) -> int | None:
    value = response.headers.get("age")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _http_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
