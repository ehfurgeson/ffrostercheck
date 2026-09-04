# Fantasy Watchdog

Fantasy Watchdog is a Python service that will check fantasy-football lineups shortly before NFL kickoff and email one consolidated availability report across Sleeper and ESPN leagues.

The implementation is being built in small, independently working milestones; see [`fantasy_watchdog_build_spec.md`](fantasy_watchdog_build_spec.md) for the full design.

## Requirements

- Python 3.10 or newer (Python 3.12 is the tested development version)
- [`uv`](https://docs.astral.sh/uv/) or another Python package manager

## Development setup

```bash
uv venv --python 3.12
uv sync --extra test
cp config.example.yaml config.yaml
cp .env.example .env
uv run pytest
```

`config.yaml` contains ordinary application settings and may be customized locally. Authenticated ESPN and SMTP credentials belong only in `.env`, which is ignored by Git.

## Current milestone

The project currently provides typed YAML configuration, strict validation, and secret-safe environment loading. Fantasy platform ingestion is the next milestone.
