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

`config.yaml` contains ordinary application settings and may be customized locally. Authenticated ESPN and SMTP credentials belong only in `.env`, which is ignored by Git; SMTP delivery uses authenticated STARTTLS and sends the plain-text and HTML renderings as one multipart message.

## Current milestone

The project currently loads Sleeper and ESPN rosters, resolves canonical NFL identities, groups players by exact kickoff, parses official NFL.com inactives and injury reports, combines those sources into one status per player, caches T−90 official snapshots, and uses Sleeper catalog fields plus nflverse injuries as fallbacks. It selects one timestamped depth snapshot, joins deduplicated owned QB/RB/WR/TE players by exact GSIS ID and then known ESPN ID, builds normalized same-slot blocker chains, and detects direct promotions, role boosts, and weaker same-position opportunity. Unknown and fallback-only blockers remain ahead, so only explicit official inactive or Out evidence changes the effective depth order. League-specific analysis assigns deterministic severity and ranks only eligible, verified bench replacements whose games have not locked; a missing kickoff is never assumed actionable. Plain-text and HTML emails are rendered per exact kickoff window, grouped by urgency and league with healthy bench noise omitted and missing status labeled explicitly, then delivered together through authenticated SMTP with STARTTLS. Delivery requires both SMTP secrets, treats recipient refusal as failure, and sanitizes transport errors so credentials and server response details are not exposed. The game-day planner turns relevant local-date kickoff windows into deterministic prefetch and final jobs; the silent prefetch executor retries incomplete official reports, and the final executor refreshes and re-resolves fantasy lineups, attempts official status refreshes, consults fallbacks for roster eligibility and degraded official evidence, performs league-specific analysis, and sends one consolidated T−5 alert. The `run-game-day` command now composes those live stages into one supervised process, and the checked-in systemd service/timer starts it each morning without hard-coding NFL weekdays. Final emails disclose fantasy-provider retrieval times and HTTP cache ages, while failed official refreshes may reuse the T−90 snapshot only with downgraded confidence and an explicit limitation. Optional official team-site articles can attach attributed notes, but they never override official inactives or become binary game-day status. Confidence is scored from the strongest informing source rather than averaged: official evidence is `official`, cached official evidence after a failed refresh is `high`, Sleeper is `medium`, and nflverse is `low`.

```bash
uv run fantasy-watchdog sleeper-rosters --config config.yaml
uv run fantasy-watchdog espn-roster --config config.yaml
uv run fantasy-watchdog all-rosters --config config.yaml
uv run fantasy-watchdog nfl-inactives --html tests/fixtures/nfl_inactives/week18_excerpt.html --home JAX --away TEN
uv run fantasy-watchdog nfl-injuries --season 2025 --week 18 --html tests/fixtures/nfl_injuries/week18_excerpt.html --home TB --away CAR
uv run fantasy-watchdog player-status --season 2025 --week 18 --home JAX --away TEN --inactives-html tests/fixtures/nfl_inactives/week18_excerpt.html --injuries-html tests/fixtures/nfl_injuries/week18_excerpt.html --sleeper-players tests/fixtures/sleeper/status_players.json --nflverse-injuries tests/fixtures/nflverse/injuries.json
uv run fantasy-watchdog status-cache --stage prefetch --season 2025 --week 18 --home JAX --away TEN --game-id 2025_18_JAX_TEN --inactives-html tests/fixtures/nfl_inactives/week18_excerpt.html --injuries-html tests/fixtures/nfl_injuries/week18_excerpt.html --cache-dir cache
uv run fantasy-watchdog sleeper-status --home JAX --away TEN --players tests/fixtures/sleeper/status_players.json
uv run fantasy-watchdog nflverse-status --season 2025 --week 18 --home JAX --away TEN --injuries tests/fixtures/nflverse/injuries.json
uv run fantasy-watchdog team-status --home CHI --away GB --article GB=tests/fixtures/team_sites/packers_lists.html
uv run fantasy-watchdog depth-charts --season 2026 --charts tests/fixtures/depth_charts/snapshots.json --as-of 2026-09-04T18:00:00Z
uv run fantasy-watchdog owned-depth --config config.yaml
uv run fantasy-watchdog plan-game-day --config config.yaml
uv run fantasy-watchdog run-game-day --config config.yaml
```

Native deployment guides are available for [Linux, Windows, and macOS](deploy/README.md). Run `plan-game-day` first for a read-only production smoke test; `run-game-day` waits for live job times and can send configured email alerts.

Private ESPN leagues use `ESPN_SWID` and `ESPN_S2` from `.env`; credentials are never included in errors or diagnostic output. Official NFL.com diagnostics stay fixture-driven and never infer active or healthy from missing rows. Official team-site context is optional, non-blocking, and attributed only: Packers list adapters may name players, while Chiefs/Patriots/generic narrative is preserved as excerpt text and never converted into active/inactive/out. Sleeper catalog `active` is recorded as employment metadata only and is not a game-day active declaration. nflverse injuries are corroboration only; a missing or unsupported season is a source failure, not a healthy league. Confidence follows the strongest informing source and is reduced when cached official evidence is reused after a failed refresh.
