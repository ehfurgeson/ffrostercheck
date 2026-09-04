"""Typed, validated configuration for Fantasy Watchdog.

Non-secret settings live in YAML. Authenticated credentials are intentionally
loaded separately from environment variables so they cannot accidentally be
serialized with ordinary application configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv import dotenv_values


class ConfigError(ValueError):
    """Raised when application configuration is missing or invalid."""


@dataclass(frozen=True)
class LeagueConfig:
    id: str
    roster_id: str
    nickname: str


@dataclass(frozen=True)
class ESPNLeagueConfig:
    id: str
    team_id: str
    nickname: str


@dataclass(frozen=True)
class AlertsConfig:
    prefetch_attempts_minutes_before_kickoff: tuple[int, ...] = (95, 75, 15)
    email_minutes_before_kickoff: int = 5


@dataclass(frozen=True)
class SleeperConfig:
    username: str
    user_id: str | None = None
    leagues: tuple[LeagueConfig, ...] = ()


@dataclass(frozen=True)
class ESPNConfig:
    leagues: tuple[ESPNLeagueConfig, ...] = ()


@dataclass(frozen=True)
class EmailConfig:
    smtp_host: str
    smtp_port: int
    recipient: str


@dataclass(frozen=True)
class WatchConfig:
    starters: bool = True
    bench_for_replacements: bool = True
    include_bench_injury_notes: bool = True
    depth_opportunity: bool = True
    broad_position_opportunity: bool = True


@dataclass(frozen=True)
class DepthChartConfig:
    enabled: bool = True
    max_age_hours: int = 30
    strong_boost_only_for_same_slot_promotion: bool = True


@dataclass(frozen=True)
class SourcesConfig:
    official_team_context_required: bool = False
    record_http_cache_age: bool = True
    final_sleeper_cache_bust: bool = False


@dataclass(frozen=True)
class AppConfig:
    season: int
    timezone: str
    alerts: AlertsConfig
    sleeper: SleeperConfig
    espn: ESPNConfig
    email: EmailConfig
    watch: WatchConfig = field(default_factory=WatchConfig)
    depth_chart: DepthChartConfig = field(default_factory=DepthChartConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)

    @property
    def timezone_info(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@dataclass(frozen=True, repr=False)
class SecretConfig:
    espn_swid: str | None = field(default=None, repr=False)
    espn_s2: str | None = field(default=None, repr=False)
    smtp_user: str | None = field(default=None, repr=False)
    smtp_app_password: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        configured = [
            name
            for name, value in (
                ("ESPN_SWID", self.espn_swid),
                ("ESPN_S2", self.espn_s2),
                ("SMTP_USER", self.smtp_user),
                ("SMTP_APP_PASSWORD", self.smtp_app_password),
            )
            if value
        ]
        return f"SecretConfig(configured={configured!r})"

    def require_espn(self) -> None:
        _require_secrets(self, ("espn_swid", "espn_s2"), "ESPN")

    def require_email(self) -> None:
        _require_secrets(self, ("smtp_user", "smtp_app_password"), "email")


def load_config(path: str | Path) -> AppConfig:
    """Load and validate non-secret YAML configuration."""

    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Configuration file not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc

    root = _mapping(raw, "configuration")
    _reject_unknown(
        root,
        {
            "season",
            "timezone",
            "alerts",
            "sleeper",
            "espn",
            "email",
            "watch",
            "depth_chart",
            "sources",
        },
        "configuration",
    )

    season = _integer(root, "season", "configuration")
    if not 2000 <= season <= 2100:
        raise ConfigError("configuration.season must be between 2000 and 2100")

    timezone = _string(root, "timezone", "configuration")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"configuration.timezone is not recognized: {timezone}") from exc

    return AppConfig(
        season=season,
        timezone=timezone,
        alerts=_parse_alerts(_section(root, "alerts")),
        sleeper=_parse_sleeper(_section(root, "sleeper")),
        espn=_parse_espn(_section(root, "espn")),
        email=_parse_email(_section(root, "email")),
        watch=_parse_watch(_optional_section(root, "watch")),
        depth_chart=_parse_depth_chart(_optional_section(root, "depth_chart")),
        sources=_parse_sources(_optional_section(root, "sources")),
    )


def load_secrets(
    *,
    env_file: str | Path | None = ".env",
    environ: Mapping[str, str] | None = None,
) -> SecretConfig:
    """Load secrets from a dotenv file, overridden by the supplied environment."""

    values: dict[str, str | None] = {}
    if env_file is not None:
        values.update(dotenv_values(env_file))

    if environ is None:
        import os

        environ = os.environ
    values.update(environ)

    return SecretConfig(
        espn_swid=_optional_secret(values.get("ESPN_SWID")),
        espn_s2=_optional_secret(values.get("ESPN_S2")),
        smtp_user=_optional_secret(values.get("SMTP_USER")),
        smtp_app_password=_optional_secret(values.get("SMTP_APP_PASSWORD")),
    )


def _parse_alerts(data: Mapping[str, Any]) -> AlertsConfig:
    where = "alerts"
    _reject_unknown(
        data,
        {"prefetch_attempts_minutes_before_kickoff", "email_minutes_before_kickoff"},
        where,
    )
    attempts_raw = data.get("prefetch_attempts_minutes_before_kickoff", [95, 75, 15])
    if not isinstance(attempts_raw, list) or not attempts_raw:
        raise ConfigError(
            f"{where}.prefetch_attempts_minutes_before_kickoff must be a non-empty list"
        )
    attempts = tuple(
        _positive_integer(value, f"{where}.prefetch_attempts_minutes_before_kickoff")
        for value in attempts_raw
    )
    email_minutes = _integer(data, "email_minutes_before_kickoff", where, default=5)
    if email_minutes < 0:
        raise ConfigError(f"{where}.email_minutes_before_kickoff must be at least 0")
    return AlertsConfig(attempts, email_minutes)


def _parse_sleeper(data: Mapping[str, Any]) -> SleeperConfig:
    where = "sleeper"
    _reject_unknown(data, {"username", "user_id", "leagues"}, where)
    leagues = tuple(
        _parse_league(item, f"{where}.leagues[{index}]")
        for index, item in enumerate(_list(data, "leagues", where))
    )
    _ensure_unique((league.id for league in leagues), f"{where}.leagues ids")
    return SleeperConfig(
        username=_string(data, "username", where),
        user_id=_optional_string(data, "user_id", where),
        leagues=leagues,
    )


def _parse_espn(data: Mapping[str, Any]) -> ESPNConfig:
    where = "espn"
    _reject_unknown(data, {"leagues"}, where)
    leagues = tuple(
        _parse_espn_league(item, f"{where}.leagues[{index}]")
        for index, item in enumerate(_list(data, "leagues", where))
    )
    _ensure_unique((league.id for league in leagues), f"{where}.leagues ids")
    return ESPNConfig(leagues=leagues)


def _parse_email(data: Mapping[str, Any]) -> EmailConfig:
    where = "email"
    _reject_unknown(data, {"smtp_host", "smtp_port", "recipient"}, where)
    port = _integer(data, "smtp_port", where)
    if not 1 <= port <= 65535:
        raise ConfigError(f"{where}.smtp_port must be between 1 and 65535")
    return EmailConfig(
        smtp_host=_string(data, "smtp_host", where),
        smtp_port=port,
        recipient=_string(data, "recipient", where),
    )


def _parse_watch(data: Mapping[str, Any]) -> WatchConfig:
    where = "watch"
    fields = {
        "starters",
        "bench_for_replacements",
        "include_bench_injury_notes",
        "depth_opportunity",
        "broad_position_opportunity",
    }
    _reject_unknown(data, fields, where)
    return WatchConfig(**{name: _boolean(data, name, where, default=True) for name in fields})


def _parse_depth_chart(data: Mapping[str, Any]) -> DepthChartConfig:
    where = "depth_chart"
    _reject_unknown(
        data,
        {"enabled", "max_age_hours", "strong_boost_only_for_same_slot_promotion"},
        where,
    )
    max_age = _integer(data, "max_age_hours", where, default=30)
    if max_age <= 0:
        raise ConfigError(f"{where}.max_age_hours must be greater than 0")
    return DepthChartConfig(
        enabled=_boolean(data, "enabled", where, default=True),
        max_age_hours=max_age,
        strong_boost_only_for_same_slot_promotion=_boolean(
            data, "strong_boost_only_for_same_slot_promotion", where, default=True
        ),
    )


def _parse_sources(data: Mapping[str, Any]) -> SourcesConfig:
    where = "sources"
    fields = {
        "official_team_context_required",
        "record_http_cache_age",
        "final_sleeper_cache_bust",
    }
    _reject_unknown(data, fields, where)
    defaults = SourcesConfig()
    return SourcesConfig(
        official_team_context_required=_boolean(
            data,
            "official_team_context_required",
            where,
            default=defaults.official_team_context_required,
        ),
        record_http_cache_age=_boolean(
            data, "record_http_cache_age", where, default=defaults.record_http_cache_age
        ),
        final_sleeper_cache_bust=_boolean(
            data, "final_sleeper_cache_bust", where, default=defaults.final_sleeper_cache_bust
        ),
    )


def _parse_league(raw: Any, where: str) -> LeagueConfig:
    data = _mapping(raw, where)
    _reject_unknown(data, {"id", "roster_id", "nickname"}, where)
    return LeagueConfig(
        id=_string(data, "id", where),
        roster_id=_string(data, "roster_id", where),
        nickname=_string(data, "nickname", where),
    )


def _parse_espn_league(raw: Any, where: str) -> ESPNLeagueConfig:
    data = _mapping(raw, where)
    _reject_unknown(data, {"id", "team_id", "nickname"}, where)
    return ESPNLeagueConfig(
        id=_string(data, "id", where),
        team_id=_string(data, "team_id", where),
        nickname=_string(data, "nickname", where),
    )


def _section(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    if key not in data:
        raise ConfigError(f"configuration.{key} is required")
    return _mapping(data[key], key)


def _optional_section(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    return _mapping(data.get(key, {}), key)


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{where} must be a mapping")
    return value


def _list(data: Mapping[str, Any], key: str, where: str) -> list[Any]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise ConfigError(f"{where}.{key} must be a list")
    return value


def _string(data: Mapping[str, Any], key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}.{key} must be a non-empty string")
    return value.strip()


def _optional_string(data: Mapping[str, Any], key: str, where: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}.{key} must be a non-empty string when provided")
    return value.strip()


def _integer(
    data: Mapping[str, Any], key: str, where: str, *, default: int | None = None
) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where}.{key} must be an integer")
    return value


def _positive_integer(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{where} values must be positive integers")
    return value


def _boolean(data: Mapping[str, Any], key: str, where: str, *, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{where}.{key} must be true or false")
    return value


def _reject_unknown(data: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ConfigError(f"Unknown setting(s) in {where}: {', '.join(unknown)}")


def _ensure_unique(values: Any, where: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise ConfigError(f"Duplicate {where}: {', '.join(sorted(duplicates))}")


def _optional_secret(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value.strip()


def _require_secrets(config: SecretConfig, names: tuple[str, ...], label: str) -> None:
    environment_names = {
        "espn_swid": "ESPN_SWID",
        "espn_s2": "ESPN_S2",
        "smtp_user": "SMTP_USER",
        "smtp_app_password": "SMTP_APP_PASSWORD",
    }
    missing = [environment_names[name] for name in names if not getattr(config, name)]
    if missing:
        raise ConfigError(f"Missing {label} secret(s): {', '.join(missing)}")
