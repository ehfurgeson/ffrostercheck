import plistlib
import subprocess
import sys
from pathlib import Path


DEPLOY = Path("deploy")


def test_macos_launch_agent_template_runs_game_day_once_daily() -> None:
    with (DEPLOY / "macos" / "com.fantasy-watchdog.game-day.plist").open("rb") as source:
        payload = plistlib.load(source)

    arguments = payload["ProgramArguments"]
    assert arguments == [
        "__EXECUTABLE__",
        "run-game-day",
        "--config",
        "__CONFIG__",
        "--env-file",
        "__ENV_FILE__",
        "--cache-dir",
        "__CACHE_DIR__",
    ]
    assert payload["StartCalendarInterval"] == {"Hour": 7, "Minute": 0}
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is False
    assert payload["StandardOutPath"] == "__STDOUT_LOG__"
    assert payload["StandardErrorPath"] == "__STDERR_LOG__"


def test_macos_renderer_safely_writes_absolute_paths(tmp_path: Path) -> None:
    output = tmp_path / "rendered.plist"
    project = tmp_path / "Fantasy & Watchdog"
    subprocess.run(
        [
            sys.executable,
            str(DEPLOY / "macos" / "render_plist.py"),
            "--template",
            str(DEPLOY / "macos" / "com.fantasy-watchdog.game-day.plist"),
            "--output",
            str(output),
            "--project-root",
            str(project),
            "--start-hour",
            "6",
        ],
        check=True,
    )
    with output.open("rb") as source:
        payload = plistlib.load(source)

    assert payload["ProgramArguments"][0] == str(
        project.resolve() / ".venv" / "bin" / "fantasy-watchdog"
    )
    assert payload["ProgramArguments"][1:] == [
        "run-game-day",
        "--config",
        str(project.resolve() / "config.yaml"),
        "--env-file",
        str(project.resolve() / ".env"),
        "--cache-dir",
        str(project.resolve() / "cache"),
    ]
    assert payload["StartCalendarInterval"] == {"Hour": 6, "Minute": 0}


def test_windows_installer_registers_safe_unattended_task() -> None:
    installer = (DEPLOY / "windows" / "install.ps1").read_text(encoding="utf-8")

    for expected in (
        "run-game-day --config",
        "--env-file",
        "--cache-dir",
        "New-ScheduledTaskTrigger -Daily -At $StartTime",
        "-StartWhenAvailable",
        "-WakeToRun",
        "-ExecutionTimeLimit (New-TimeSpan -Hours 18)",
        "-MultipleInstances IgnoreNew",
        "-LogonType Password",
        "Get-Credential",
    ):
        assert expected in installer
    assert "SMTP_APP_PASSWORD" not in installer
    assert "ESPN_S2" not in installer


def test_deployment_index_links_all_native_platforms_and_defers_docker() -> None:
    index = (DEPLOY / "README.md").read_text(encoding="utf-8")

    assert "[systemd](systemd/README.md)" in index
    assert "[Windows](windows/README.md)" in index
    assert "[macOS](macos/README.md)" in index
    assert "Docker is intentionally not included yet" in index
