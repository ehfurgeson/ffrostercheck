# Fantasy Watchdog

Fantasy Watchdog is a Python service that will check fantasy-football lineups shortly before NFL kickoff and email one consolidated availability report across Sleeper and ESPN leagues.


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

The project currently loads Sleeper and ESPN rosters, resolves canonical NFL identities, groups players by exact kickoff, parses official NFL.com inactives and injury reports, combines those sources into one status per player, and caches T−90 official snapshots without treating them as origin-fresh at T−5.

```bash
uv run fantasy-watchdog sleeper-rosters --config config.yaml
uv run fantasy-watchdog espn-roster --config config.yaml
uv run fantasy-watchdog all-rosters --config config.yaml
uv run fantasy-watchdog nfl-inactives --html tests/fixtures/nfl_inactives/week18_excerpt.html --home JAX --away TEN
uv run fantasy-watchdog nfl-injuries --season 2025 --week 18 --html tests/fixtures/nfl_injuries/week18_excerpt.html --home TB --away CAR
uv run fantasy-watchdog player-status --season 2025 --week 18 --home JAX --away TEN --inactives-html tests/fixtures/nfl_inactives/week18_excerpt.html --injuries-html tests/fixtures/nfl_injuries/week18_excerpt.html
uv run fantasy-watchdog status-cache --stage prefetch --season 2025 --week 18 --home JAX --away TEN --game-id 2025_18_JAX_TEN --inactives-html tests/fixtures/nfl_inactives/week18_excerpt.html --injuries-html tests/fixtures/nfl_injuries/week18_excerpt.html --cache-dir cache
```

Private ESPN leagues use `ESPN_SWID` and `ESPN_S2` from `.env`; credentials are never included in errors or diagnostic output. Official NFL.com diagnostics stay fixture-driven and never infer active or healthy from missing rows.
