"""Official NFL.com inactives discovery and HTML parsing."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag

from app.models import (
    GameDayState,
    GameSourceReport,
    RelevantGame,
    ReportState,
    SourceResult,
)
from app.nfl.identity import normalize_team


INACTIVES_LANDING_URL = "https://www.nfl.com/inactives/"
SOURCE_NAME = "nfl_inactives"
WINDOW_MARKERS = ("WINDOW", "NIGHT", "FOOTBALL")
PLACEHOLDER_MARKERS = (
    "check back soon",
    "inactive reports for this season",
)
POSITIONS = frozenset(
    {
        "QB",
        "RB",
        "FB",
        "HB",
        "WR",
        "TE",
        "OT",
        "OG",
        "G",
        "C",
        "OL",
        "LT",
        "RT",
        "DE",
        "DT",
        "DL",
        "NT",
        "LB",
        "OLB",
        "ILB",
        "MLB",
        "CB",
        "S",
        "DB",
        "FS",
        "SS",
        "K",
        "P",
        "LS",
    }
)
TEAM_NICKNAMES = {
    "CARDINALS": "ARI",
    "ARIZONA": "ARI",
    "ARIZONA CARDINALS": "ARI",
    "FALCONS": "ATL",
    "ATLANTA": "ATL",
    "ATLANTA FALCONS": "ATL",
    "RAVENS": "BAL",
    "BALTIMORE": "BAL",
    "BALTIMORE RAVENS": "BAL",
    "BILLS": "BUF",
    "BUFFALO": "BUF",
    "BUFFALO BILLS": "BUF",
    "PANTHERS": "CAR",
    "CAROLINA": "CAR",
    "CAROLINA PANTHERS": "CAR",
    "BEARS": "CHI",
    "CHICAGO": "CHI",
    "CHICAGO BEARS": "CHI",
    "BENGALS": "CIN",
    "CINCINNATI": "CIN",
    "CINCINNATI BENGALS": "CIN",
    "BROWNS": "CLE",
    "CLEVELAND": "CLE",
    "CLEVELAND BROWNS": "CLE",
    "COWBOYS": "DAL",
    "DALLAS": "DAL",
    "DALLAS COWBOYS": "DAL",
    "BRONCOS": "DEN",
    "DENVER": "DEN",
    "DENVER BRONCOS": "DEN",
    "LIONS": "DET",
    "DETROIT": "DET",
    "DETROIT LIONS": "DET",
    "PACKERS": "GB",
    "GREEN BAY": "GB",
    "GREEN BAY PACKERS": "GB",
    "TEXANS": "HOU",
    "HOUSTON": "HOU",
    "HOUSTON TEXANS": "HOU",
    "COLTS": "IND",
    "INDIANAPOLIS": "IND",
    "INDIANAPOLIS COLTS": "IND",
    "JAGUARS": "JAX",
    "JAGS": "JAX",
    "JACKSONVILLE": "JAX",
    "JACKSONVILLE JAGUARS": "JAX",
    "CHIEFS": "KC",
    "KANSAS CITY": "KC",
    "KANSAS CITY CHIEFS": "KC",
    "RAIDERS": "LV",
    "LAS VEGAS": "LV",
    "LAS VEGAS RAIDERS": "LV",
    "CHARGERS": "LAC",
    "LOS ANGELES CHARGERS": "LAC",
    "RAMS": "LAR",
    "LOS ANGELES RAMS": "LAR",
    "DOLPHINS": "MIA",
    "MIAMI": "MIA",
    "MIAMI DOLPHINS": "MIA",
    "VIKINGS": "MIN",
    "MINNESOTA": "MIN",
    "MINNESOTA VIKINGS": "MIN",
    "PATRIOTS": "NE",
    "NEW ENGLAND": "NE",
    "NEW ENGLAND PATRIOTS": "NE",
    "SAINTS": "NO",
    "NEW ORLEANS": "NO",
    "NEW ORLEANS SAINTS": "NO",
    "GIANTS": "NYG",
    "NEW YORK GIANTS": "NYG",
    "JETS": "NYJ",
    "NEW YORK JETS": "NYJ",
    "EAGLES": "PHI",
    "PHILADELPHIA": "PHI",
    "PHILADELPHIA EAGLES": "PHI",
    "STEELERS": "PIT",
    "PITTSBURGH": "PIT",
    "PITTSBURGH STEELERS": "PIT",
    "49ERS": "SF",
    "NINERS": "SF",
    "FORTY NINERS": "SF",
    "SAN FRANCISCO": "SF",
    "SAN FRANCISCO 49ERS": "SF",
    "SEAHAWKS": "SEA",
    "SEATTLE": "SEA",
    "SEATTLE SEAHAWKS": "SEA",
    "BUCCANEERS": "TB",
    "BUCS": "TB",
    "TAMPA BAY": "TB",
    "TAMPA BAY BUCCANEERS": "TB",
    "TITANS": "TEN",
    "TENNESSEE": "TEN",
    "TENNESSEE TITANS": "TEN",
    "COMMANDERS": "WAS",
    "WASHINGTON": "WAS",
    "WASHINGTON COMMANDERS": "WAS",
}
ANNOTATION_RE = re.compile(r"\(([^)]*)\)")


@dataclass(frozen=True)
class ParsedInactivePlayer:
    raw_text: str
    name: str
    team: str
    position: str | None
    annotations: tuple[str, ...] = ()


@dataclass(frozen=True)
class InactivesDocument:
    report_state: ReportState
    source_url: str | None
    teams: dict[str, tuple[ParsedInactivePlayer, ...]]
    retrieved_at: datetime
    published_at: datetime | None = None
    source_updated_at: datetime | None = None
    http_cache_age_seconds: int | None = None
    raw_content_hash: str | None = None
    errors: tuple[str, ...] = ()

    @property
    def parsed_teams(self) -> frozenset[str]:
        return frozenset(self.teams)


class NFLInactivesSource:
    """Discover and parse official NFL.com inactive lists."""

    name = SOURCE_NAME
    priority = 100

    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        landing_url: str = INACTIVES_LANDING_URL,
        article_url: str | None = None,
        landing_html: str | None = None,
        article_html: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "fantasy-watchdog/0.1"},
        )
        self._landing_url = landing_url
        self._article_url = article_url
        self._landing_html = landing_html
        self._article_html = article_html
        self._document: InactivesDocument | None = None

    def __enter__(self) -> NFLInactivesSource:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch_game(self, game: RelevantGame) -> GameSourceReport:
        document = self.load_document()
        expected = frozenset(
            team
            for team in (
                normalize_team(game.home_team),
                normalize_team(game.away_team),
            )
            if team
        )
        parsed_for_game = frozenset(team for team in expected if team in document.teams)
        report_state = _game_report_state(document, expected, parsed_for_game)
        player_results = ()
        if report_state in {ReportState.COMPLETE, ReportState.PARTIAL}:
            player_results = tuple(
                _inactive_result(player, document, report_state)
                for team in sorted(parsed_for_game)
                for player in document.teams[team]
            )
        errors = list(document.errors)
        if report_state is ReportState.PARTIAL and expected - parsed_for_game:
            missing = ", ".join(sorted(expected - parsed_for_game))
            errors.append(f"Inactive list missing team(s): {missing}")
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

    def load_document(self) -> InactivesDocument:
        if self._document is None:
            self._document = self._fetch_document()
        return self._document

    def _fetch_document(self) -> InactivesDocument:
        retrieved_at = datetime.now(timezone.utc)
        if self._article_html is not None:
            return parse_inactives_article(
                self._article_html,
                source_url=self._article_url or self._landing_url,
                retrieved_at=retrieved_at,
            )

        if self._article_url is not None:
            return self._fetch_article(self._article_url, retrieved_at)

        landing = self._fetch_landing(retrieved_at)
        if isinstance(landing, InactivesDocument):
            return landing
        landing_html, landing_url, cache_age = landing
        if _looks_unpublished(landing_html) and discover_inactives_article(
            landing_html, base_url=landing_url
        ) is None:
            return InactivesDocument(
                report_state=ReportState.NOT_YET_PUBLISHED,
                source_url=landing_url,
                teams={},
                retrieved_at=retrieved_at,
                http_cache_age_seconds=cache_age,
                raw_content_hash=_content_hash(landing_html),
            )

        article_url = discover_inactives_article(landing_html, base_url=landing_url)
        if article_url:
            return self._fetch_article(article_url, retrieved_at)
        return parse_inactives_article(
            landing_html,
            source_url=landing_url,
            retrieved_at=retrieved_at,
            http_cache_age_seconds=cache_age,
        )

    def _fetch_landing(
        self, retrieved_at: datetime
    ) -> InactivesDocument | tuple[str, str, int | None]:
        if self._landing_html is not None:
            return self._landing_html, self._landing_url, None
        try:
            response = self._client.get(self._landing_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return InactivesDocument(
                report_state=ReportState.FAILED,
                source_url=self._landing_url,
                teams={},
                retrieved_at=retrieved_at,
                errors=(f"NFL inactives landing request failed: {exc}",),
            )
        return response.text, str(response.url), _cache_age(response)

    def _fetch_article(self, url: str, retrieved_at: datetime) -> InactivesDocument:
        try:
            response = self._client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return InactivesDocument(
                report_state=ReportState.FAILED,
                source_url=url,
                teams={},
                retrieved_at=retrieved_at,
                errors=(f"NFL inactives article request failed: {exc}",),
            )
        return parse_inactives_article(
            response.text,
            source_url=str(response.url),
            retrieved_at=retrieved_at,
            http_cache_age_seconds=_cache_age(response),
        )


def discover_inactives_article(html: str, *, base_url: str) -> str | None:
    """Return the current inactives article URL when the landing page links to one."""

    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"])
        label = anchor.get_text(" ", strip=True)
        haystack = f"{href} {label}".casefold()
        if "inactive" in haystack and "/news/" in href.casefold():
            return urljoin(base_url, href)
    return None


def parse_inactives_article(
    html: str,
    *,
    source_url: str | None,
    retrieved_at: datetime,
    http_cache_age_seconds: int | None = None,
) -> InactivesDocument:
    """Parse semantic team headings and the player lists that follow them."""

    soup = BeautifulSoup(html, "html.parser")
    published_at, source_updated_at = _article_timestamps(soup)
    teams: dict[str, tuple[ParsedInactivePlayer, ...]] = {}
    errors: list[str] = []
    for heading in soup.find_all(["h2", "h3", "h4"]):
        team = resolve_team_heading(heading.get_text(" ", strip=True))
        if team is None:
            continue
        player_list = _following_player_list(heading)
        if player_list is None:
            errors.append(f"{team} heading has no player list")
            continue
        parsed_players: list[ParsedInactivePlayer] = []
        for item in player_list.find_all("li", recursive=False):
            player = parse_inactive_line(item.get_text(" ", strip=True), team)
            if player is None:
                errors.append(f"{team} has an unparseable inactive line")
                continue
            parsed_players.append(player)
        teams[team] = tuple(parsed_players)

    return InactivesDocument(
        report_state=ReportState.NOT_YET_PUBLISHED if not teams else ReportState.PARTIAL,
        source_url=source_url,
        teams=teams,
        retrieved_at=retrieved_at,
        published_at=published_at,
        source_updated_at=source_updated_at,
        http_cache_age_seconds=http_cache_age_seconds,
        raw_content_hash=_content_hash(html),
        errors=tuple(errors),
    )


def parse_inactive_line(raw_text: str, team: str) -> ParsedInactivePlayer | None:
    """Keep raw text, then strip position prefixes and parenthetical notes."""

    raw = " ".join(raw_text.split())
    if not raw:
        return None
    annotations = tuple(part.strip() for part in ANNOTATION_RE.findall(raw) if part.strip())
    core = ANNOTATION_RE.sub("", raw).strip()
    tokens = core.split()
    if not tokens:
        return None
    position = None
    name_tokens = tokens
    if tokens[0].upper() in POSITIONS:
        position = tokens[0].upper()
        name_tokens = tokens[1:]
    name = " ".join(name_tokens).strip()
    if not name:
        return None
    return ParsedInactivePlayer(
        raw_text=raw,
        name=name,
        team=team,
        position=position,
        annotations=annotations,
    )


def resolve_team_heading(text: str) -> str | None:
    heading = " ".join(text.split()).upper()
    if not heading or any(marker in heading for marker in WINDOW_MARKERS):
        return None
    if heading in TEAM_NICKNAMES:
        return TEAM_NICKNAMES[heading]
    normalized = normalize_team(heading)
    if normalized in set(TEAM_NICKNAMES.values()):
        return normalized
    return None


def render_inactives_document(document: InactivesDocument) -> str:
    lines = [
        f"NFL inactives document: {document.report_state.value}",
        f"Teams parsed: {len(document.teams)}",
    ]
    if document.source_url:
        lines.append(f"Source: {document.source_url}")
    if document.source_updated_at:
        lines.append(f"Source updated: {document.source_updated_at.isoformat()}")
    for team, players in sorted(document.teams.items()):
        lines.append(f"  {team}: {len(players)} inactive")
        for player in players:
            position = f"{player.position} " if player.position else ""
            lines.append(f"    {position}{player.name}")
    if not document.teams:
        lines.append("  No team inactive lists were parsed")
    for error in document.errors:
        lines.append(f"  error: {error}")
    return "\n".join(lines)


def render_inactives_report(report: GameSourceReport) -> str:
    lines = [
        f"NFL inactives: {report.report_state.value}",
        f"Game: {report.game_id}",
        f"Expected teams: {', '.join(sorted(report.expected_teams)) or 'none'}",
        f"Parsed teams: {', '.join(sorted(report.parsed_teams)) or 'none'}",
    ]
    if report.source_url:
        lines.append(f"Source: {report.source_url}")
    if report.source_updated_at:
        lines.append(f"Source updated: {report.source_updated_at.isoformat()}")
    for result in report.player_results:
        identity = result.player_name or "Unknown player"
        position = f"{result.position} " if result.position else ""
        team = f" ({result.nfl_team})" if result.nfl_team else ""
        lines.append(f"  INACTIVE: {position}{identity}{team}")
    if not report.player_results:
        lines.append("  No inactive players inferred as active")
    for error in report.errors:
        lines.append(f"  error: {error}")
    return "\n".join(lines)


def _game_report_state(
    document: InactivesDocument,
    expected: frozenset[str],
    parsed_for_game: frozenset[str],
) -> ReportState:
    if document.report_state is ReportState.FAILED:
        return ReportState.FAILED
    if document.report_state is ReportState.NOT_YET_PUBLISHED or not document.teams:
        return ReportState.NOT_YET_PUBLISHED
    if expected and parsed_for_game == expected:
        return ReportState.COMPLETE
    return ReportState.PARTIAL


def _inactive_result(
    player: ParsedInactivePlayer,
    document: InactivesDocument,
    report_state: ReportState,
) -> SourceResult:
    return SourceResult(
        source=SOURCE_NAME,
        source_url=document.source_url,
        success=True,
        report_state=report_state,
        retrieved_at=document.retrieved_at,
        game_day_state=GameDayState.INACTIVE,
        detail=player.raw_text,
        player_name=player.name,
        nfl_team=player.team,
        position=player.position,
        published_at=document.published_at,
        source_updated_at=document.source_updated_at,
        http_cache_age_seconds=document.http_cache_age_seconds,
        raw_content_hash=document.raw_content_hash,
    )


def _following_player_list(heading: Tag) -> Tag | None:
    for sibling in heading.next_siblings:
        if not isinstance(sibling, Tag):
            continue
        if sibling.name == "ul":
            return sibling
        nested = sibling.find("ul") if sibling.name == "p" else None
        if nested is not None:
            return nested
        break
    parent = heading.parent
    if parent is not None:
        for sibling in parent.next_siblings:
            if not isinstance(sibling, Tag):
                continue
            if sibling.name == "ul":
                return sibling
            nested = sibling.find("ul")
            if nested is not None:
                return nested
            if sibling.find(["h2", "h3", "h4"]) is not None:
                break
    return None


def _article_timestamps(soup: BeautifulSoup) -> tuple[datetime | None, datetime | None]:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(script.get_text() or "")
        except json.JSONDecodeError:
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            published = _parse_datetime(item.get("datePublished"))
            updated = _parse_datetime(item.get("dateModified"))
            if published or updated:
                return published, updated
    return None, None


def _looks_unpublished(html: str) -> bool:
    lowered = " ".join(html.split()).casefold()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


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


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = _http_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
