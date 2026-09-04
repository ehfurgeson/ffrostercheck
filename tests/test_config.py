from pathlib import Path

import pytest

from app.config import ConfigError, load_config, load_environment, load_secrets


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_example_configuration_loads() -> None:
    config = load_config(PROJECT_ROOT / "config.example.yaml")

    assert config.season == 2026
    assert config.timezone_info.key == "America/New_York"
    assert config.alerts.prefetch_attempts_minutes_before_kickoff == (95, 75, 15)
    assert [league.nickname for league in config.sleeper.leagues] == [
        "CFL",
        "No More Fields",
        "Cry Dynasty",
    ]
    assert len(config.espn.leagues) == 1


def test_unknown_setting_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (PROJECT_ROOT / "config.example.yaml").read_text(encoding="utf-8")
        + "\nmisspelled_setting: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="misspelled_setting"):
        load_config(config_path)


def test_duplicate_league_ids_are_rejected(tmp_path: Path) -> None:
    example = (PROJECT_ROOT / "config.example.yaml").read_text(encoding="utf-8")
    duplicate = """
    - id: "1389694250752425984"
      roster_id: "99"
      nickname: Duplicate
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(example.replace("\nespn:\n", duplicate + "\nespn:\n"), encoding="utf-8")

    with pytest.raises(ConfigError, match="Duplicate sleeper.leagues ids"):
        load_config(config_path)


def test_environment_is_loaded_with_precedence_and_secrets_are_redacted(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "SLEEPER_USER=file-user\nESPN_SWID=file-swid\nESPN_S2=file-s2\nSMTP_USER=file-email\n",
        encoding="utf-8",
    )

    secrets = load_environment(
        env_file=env_path,
        environ={
            "SLEEPER_USER": "environment-user",
            "ESPN_S2": "environment-s2",
            "SMTP_APP_PASSWORD": "smtp-password",
        },
    )

    assert secrets.sleeper_user == "environment-user"
    assert secrets.espn_swid == "file-swid"
    assert secrets.espn_s2 == "environment-s2"
    assert secrets.smtp_user == "file-email"
    secrets.require_sleeper()
    secrets.require_espn()
    secrets.require_email()
    assert "environment-s2" not in repr(secrets)
    assert "smtp-password" not in repr(secrets)


def test_missing_secret_validation_is_explicit() -> None:
    secrets = load_secrets(env_file=None, environ={})

    with pytest.raises(ConfigError, match="ESPN_SWID, ESPN_S2"):
        secrets.require_espn()
