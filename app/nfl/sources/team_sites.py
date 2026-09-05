"""Optional official team-site context. Narrative never becomes binary status."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Mapping
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag

from app.models import GameSourceReport, RelevantGame, ReportState, SourceResult
from app.nfl.identity import normalize_team
from app.nfl.sources.nfl_inactives import parse_inactive_line, resolve_team_heading


SOURCE_NAME = "official_team"
ATTRIBUTED_DISCLAIMER = "attributed official team context; not a binary game-day status"
TEAM_SITE_LABELS = {
    "GB": "packers.com",
    "KC": "chiefs.com",
    "NE": "patriots.com",
}
ADAPTERS = {
    "GB": "packers_lists",
    "KC": "chiefs_paragraphs",
    "NE": "patriots_narrative",
}


@dataclass(frozen=True)
class OfficialTeamArticle:
    """One supplied official-team article. HTML fixtures are preferred in tests."""

    team: str
    html: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class ParsedTeamArticle:
    team: str
    adapter: str
    source_url: str | None
    title: str | None
    excerpt: str | None
    notes: tuple[SourceResult, ...]
    retrieved_at: datetime
    published_at: datetime | None = None
    source_updated_at: datetime | None = None
    http_cache_age_seconds: int | None = None
    raw_content_hash: str | None = None
    errors: tuple[str, ...] = ()


class OfficialTeamSource:
    """Attach attributed team-site notes without overriding official NFL status."""

    name = SOURCE_NAME
    priority = 80

    def __init__(
        self,
        *,
        articles: Mapping[str, OfficialTeamArticle] | None = None,
        http_client: httpx.Client | None = None,
        required: bool = False,
        timeout_seconds: float = 15.0,
        retrieved_at: datetime | None = None,
    ) -> None:
        articles_by_team: dict[str, OfficialTeamArticle] = {}
        for team, article in (articles or {}).items():
            key = normalize_team(team)
            if key is not None:
                articles_by_team[key] = article
        self._articles = articles_by_team
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "fantasy-watchdog/0.1"},
        )
        self._required = required
        self._retrieved_at = retrieved_at

    def __enter__(self) -> OfficialTeamSource:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch_game(self, game: RelevantGame) -> GameSourceReport:
        expected = frozenset(
            team
            for team in (normalize_team(game.home_team), normalize_team(game.away_team))
            if team
        )
        retrieved_at = self._retrieved_at or datetime.now(timezone.utc)
        parsed_articles: list[ParsedTeamArticle] = []
        errors: list[str] = []
        parsed_teams: set[str] = set()

        for team in sorted(expected):
            supplied = self._articles.get(team)
            if supplied is None:
                if self._required:
                    errors.append(f"Official team article missing for {team}")
                continue
            article = self._load_article(supplied, team=team, retrieved_at=retrieved_at)
            if article.errors and not article.notes and not article.excerpt:
                errors.extend(article.errors)
                continue
            parsed_teams.add(team)
            parsed_articles.append(article)
            errors.extend(article.errors)

        player_results = tuple(
            result
            for article in parsed_articles
            for result in _article_results(article)
        )
        report_state = _report_state(
            expected=expected,
            parsed_teams=frozenset(parsed_teams),
            errors=tuple(errors),
            required=self._required,
            had_fetch_failure=any("request failed" in error for error in errors),
        )
        source_url = parsed_articles[0].source_url if len(parsed_articles) == 1 else None
        return GameSourceReport(
            source=self.name,
            game_id=game.game_id,
            report_state=report_state,
            expected_teams=expected,
            parsed_teams=frozenset(parsed_teams),
            player_results=player_results,
            retrieved_at=retrieved_at,
            source_updated_at=_latest_timestamp(
                article.source_updated_at or article.published_at
                for article in parsed_articles
            ),
            errors=tuple(errors),
            source_url=source_url,
            published_at=_latest_timestamp(article.published_at for article in parsed_articles),
            http_cache_age_seconds=_shared_cache_age(parsed_articles),
            raw_content_hash=_combined_hash(parsed_articles),
        )

    def _load_article(
        self,
        supplied: OfficialTeamArticle,
        *,
        team: str,
        retrieved_at: datetime,
    ) -> ParsedTeamArticle:
        html = supplied.html
        url = supplied.url
        cache_age: int | None = None
        if html is None:
            if not url:
                return ParsedTeamArticle(
                    team=team,
                    adapter="none",
                    source_url=None,
                    title=None,
                    excerpt=None,
                    notes=(),
                    retrieved_at=retrieved_at,
                    errors=(f"Official team article for {team} has no HTML or URL",),
                )
            try:
                response = self._client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                return ParsedTeamArticle(
                    team=team,
                    adapter="none",
                    source_url=url,
                    title=None,
                    excerpt=None,
                    notes=(),
                    retrieved_at=retrieved_at,
                    errors=(f"{team} official team request failed: {exc}",),
                )
            html = response.text
            url = str(response.url)
            cache_age = _cache_age(response)
        return parse_team_article(
            html,
            team=team,
            source_url=url,
            retrieved_at=retrieved_at,
            http_cache_age_seconds=cache_age,
        )


def parse_team_article(
    html: str,
    *,
    team: str,
    source_url: str | None,
    retrieved_at: datetime,
    http_cache_age_seconds: int | None = None,
) -> ParsedTeamArticle:
    """Parse attributed team context. Only registered adapters may name players."""

    normalized_team = normalize_team(team)
    if normalized_team is None:
        return ParsedTeamArticle(
            team=team,
            adapter="none",
            source_url=source_url,
            title=None,
            excerpt=None,
            notes=(),
            retrieved_at=retrieved_at,
            errors=(f"Unrecognized official team: {team}",),
        )
    adapter = _adapter_for(normalized_team, source_url)
    soup = BeautifulSoup(html, "html.parser")
    metadata = _news_article_metadata(soup)
    title = metadata.headline or _page_title(soup)
    excerpt = _excerpt_from(soup, adapter) or metadata.description
    notes: tuple[SourceResult, ...] = ()
    errors: tuple[str, ...] = ()
    if adapter == "packers_lists":
        notes, errors = _packers_list_notes(
            soup,
            source_team=normalized_team,
            source_url=source_url,
            retrieved_at=retrieved_at,
            published_at=metadata.published_at,
            source_updated_at=metadata.updated_at,
            http_cache_age_seconds=http_cache_age_seconds,
            raw_content_hash=_content_hash(html),
        )
    return ParsedTeamArticle(
        team=normalized_team,
        adapter=adapter,
        source_url=source_url,
        title=title,
        excerpt=excerpt,
        notes=notes,
        retrieved_at=retrieved_at,
        published_at=metadata.published_at,
        source_updated_at=metadata.updated_at,
        http_cache_age_seconds=http_cache_age_seconds,
        raw_content_hash=_content_hash(html),
        errors=errors,
    )


def render_team_status_report(report: GameSourceReport) -> str:
    lines = [
        f"Official team context: {report.report_state.value}",
        f"Game: {report.game_id}",
        f"Expected teams: {', '.join(sorted(report.expected_teams)) or 'none'}",
        f"Parsed teams: {', '.join(sorted(report.parsed_teams)) or 'none'}",
        "Team narrative is not binary game-day status",
    ]
    if report.source_url:
        lines.append(f"Source: {report.source_url}")
    if report.published_at:
        lines.append(f"Published: {report.published_at.isoformat()}")
    if report.source_updated_at:
        lines.append(f"Source updated: {report.source_updated_at.isoformat()}")
    for result in report.player_results:
        identity = result.player_name or "article"
        position = f"{result.position} " if result.position else ""
        team = f" ({result.nfl_team})" if result.nfl_team else ""
        game_day = result.game_day_state.value if result.game_day_state else "unset"
        designation = (
            result.injury_designation.value if result.injury_designation else "unset"
        )
        lines.append(
            f"  {position}{identity}{team} game_day={game_day} designation={designation}"
        )
        if result.detail:
            lines.append(f"    {result.detail}")
    if not report.player_results:
        lines.append("  No attributed official-team notes")
    for error in report.errors:
        lines.append(f"  error: {error}")
    return "\n".join(lines)


def _adapter_for(team: str, source_url: str | None) -> str:
    host = urlparse(source_url or "").netloc.casefold()
    if host.endswith("packers.com"):
        return "packers_lists"
    if host.endswith("chiefs.com"):
        return "chiefs_paragraphs"
    if host.endswith("patriots.com"):
        return "patriots_narrative"
    return ADAPTERS.get(team, "generic_excerpt")


def _packers_list_notes(
    soup: BeautifulSoup,
    *,
    source_team: str,
    source_url: str | None,
    retrieved_at: datetime,
    published_at: datetime | None,
    source_updated_at: datetime | None,
    http_cache_age_seconds: int | None,
    raw_content_hash: str | None,
) -> tuple[tuple[SourceResult, ...], tuple[str, ...]]:
    notes: list[SourceResult] = []
    errors: list[str] = []
    for heading in soup.find_all(["h2", "h3", "h4"]):
        listed_team = _resolve_team_site_heading(heading.get_text(" ", strip=True))
        if listed_team is None:
            continue
        player_list = _following_list(heading)
        if player_list is None:
            errors.append(f"{listed_team} heading has no player list")
            continue
        for item in player_list.find_all("li", recursive=False):
            parsed = parse_inactive_line(item.get_text(" ", strip=True), listed_team)
            if parsed is None:
                errors.append(f"{listed_team} has an unparseable team-site line")
                continue
            notes.append(
                _note_result(
                    team=listed_team,
                    player_name=parsed.name,
                    position=parsed.position,
                    detail=(
                        f"{_site_label(source_team)} listed {parsed.raw_text} "
                        f"({ATTRIBUTED_DISCLAIMER})"
                    ),
                    source_url=source_url,
                    retrieved_at=retrieved_at,
                    published_at=published_at,
                    source_updated_at=source_updated_at,
                    http_cache_age_seconds=http_cache_age_seconds,
                    raw_content_hash=raw_content_hash,
                )
            )
    return tuple(notes), tuple(errors)


def _article_results(article: ParsedTeamArticle) -> tuple[SourceResult, ...]:
    article_note = _note_result(
        team=article.team,
        player_name=None,
        position=None,
        detail=_article_detail(article),
        source_url=article.source_url,
        retrieved_at=article.retrieved_at,
        published_at=article.published_at,
        source_updated_at=article.source_updated_at,
        http_cache_age_seconds=article.http_cache_age_seconds,
        raw_content_hash=article.raw_content_hash,
    )
    return (article_note, *article.notes)


def _article_detail(article: ParsedTeamArticle) -> str:
    site = _site_label(article.team)
    title = article.title or "untitled official team article"
    excerpt = article.excerpt or "no excerpt available"
    return (
        f"{site} [{article.adapter}] {title}: {excerpt} "
        f"({ATTRIBUTED_DISCLAIMER})"
    )


def _note_result(
    *,
    team: str,
    player_name: str | None,
    position: str | None,
    detail: str,
    source_url: str | None,
    retrieved_at: datetime,
    published_at: datetime | None,
    source_updated_at: datetime | None,
    http_cache_age_seconds: int | None,
    raw_content_hash: str | None,
) -> SourceResult:
    return SourceResult(
        source=SOURCE_NAME,
        source_url=source_url,
        success=True,
        report_state=ReportState.COMPLETE,
        retrieved_at=retrieved_at,
        detail=detail,
        player_name=player_name,
        nfl_team=team,
        position=position,
        published_at=published_at,
        source_updated_at=source_updated_at,
        http_cache_age_seconds=http_cache_age_seconds,
        raw_content_hash=raw_content_hash,
    )


def _report_state(
    *,
    expected: frozenset[str],
    parsed_teams: frozenset[str],
    errors: tuple[str, ...],
    required: bool,
    had_fetch_failure: bool,
) -> ReportState:
    if parsed_teams == expected:
        return ReportState.COMPLETE
    if parsed_teams:
        return ReportState.FAILED if required else ReportState.PARTIAL
    if had_fetch_failure or (required and errors):
        return ReportState.FAILED
    return ReportState.NOT_YET_PUBLISHED


def _resolve_team_site_heading(text: str) -> str | None:
    resolved = resolve_team_heading(text)
    if resolved:
        return resolved
    heading = " ".join(text.split()).upper()
    for suffix in (" INACTIVES", " INACTIVE PLAYERS", " INACTIVE"):
        if heading.endswith(suffix):
            return resolve_team_heading(heading[: -len(suffix)].strip())
    tokens = heading.split()
    return resolve_team_heading(tokens[0]) if tokens else None


def _following_list(heading: Tag) -> Tag | None:
    for sibling in heading.next_siblings:
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


@dataclass(frozen=True)
class _ArticleMetadata:
    headline: str | None = None
    description: str | None = None
    published_at: datetime | None = None
    updated_at: datetime | None = None


def _news_article_metadata(soup: BeautifulSoup) -> _ArticleMetadata:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(script.get_text() or "")
        except json.JSONDecodeError:
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            headline = _optional_text(item.get("headline"))
            description = _optional_text(item.get("description"))
            published = _parse_datetime(item.get("datePublished"))
            updated = _parse_datetime(item.get("dateModified"))
            if headline or description or published or updated:
                return _ArticleMetadata(headline, description, published, updated)
    return _ArticleMetadata()


def _excerpt_from(soup: BeautifulSoup, adapter: str) -> str | None:
    tags = ("p", "blockquote") if adapter == "patriots_narrative" else ("p",)
    parts: list[str] = []
    for tag in soup.find_all(tags):
        text = " ".join(tag.get_text(" ", strip=True).split())
        if len(text) >= 40:
            parts.append(text)
        if len(parts) == 3:
            break
    if not parts:
        return None
    excerpt = " ".join(parts)
    return excerpt if len(excerpt) <= 500 else excerpt[:497].rstrip() + "..."


def _page_title(soup: BeautifulSoup) -> str | None:
    if soup.title is None:
        return None
    return _optional_text(soup.title.get_text(" ", strip=True))


def _site_label(team: str) -> str:
    return TEAM_SITE_LABELS.get(team, f"{team} official site")


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return " ".join(value.split())


def _content_hash(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def _combined_hash(articles: list[ParsedTeamArticle]) -> str | None:
    hashes = [article.raw_content_hash for article in articles if article.raw_content_hash]
    if not hashes:
        return None
    if len(hashes) == 1:
        return hashes[0]
    return hashlib.sha256("".join(hashes).encode("utf-8")).hexdigest()


def _shared_cache_age(articles: list[ParsedTeamArticle]) -> int | None:
    ages = [
        article.http_cache_age_seconds
        for article in articles
        if article.http_cache_age_seconds is not None
    ]
    if not articles or len(ages) != len(articles):
        return None
    return max(ages)


def _latest_timestamp(values: Any) -> datetime | None:
    timestamps = [value for value in values if value is not None]
    return max(timestamps) if timestamps else None


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
