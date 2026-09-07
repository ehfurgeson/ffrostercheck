# Fantasy Football Roster Check

This is a fantasy watchdog Python service that watches fantasy-football lineups across Sleeper and ESPN leagues and emails one consolidated availability report about five minutes before each relevant NFL kickoff.

It answers a narrow operational question:

> Before these games start, are any of my starters inactive, out, or risky—and if so, what usable bench options do I still have in each league?

## Why it exists

On NFL Sundays it is easy to miss an inactive list while juggling multiple fantasy apps. This service gathers official status once per real NFL player, maps the result back to every fantasy league that owns that player, and sends a single email per kickoff window.

## How it works

```text
Fantasy platforms (Sleeper + ESPN)
        │
        ▼
Normalize rosters → resolve NFL identities → group by exact kickoff
        │
        ├─ T−90-ish: silently prefetch official inactives / injuries
        │
        └─ T−5: refresh lineups, re-check status, analyze, email
```

1. **Ingest fantasy state** from configured Sleeper leagues and an authenticated ESPN league.
2. **Resolve players once** to canonical NFL identities (preferring stable provider IDs over names).
3. **Plan the day** from the NFL schedule so 1:00, 4:05, and 4:25 windows stay separate.
4. **Prefetch official status** well before kickoff and cache complete reports.
5. **At T−5**, refresh lineups, refresh official sources, fall back carefully if needed, then send one plain-text + HTML email with league-specific alerts and bench replacements.

Missing or incomplete official data is never treated as “healthy.” Report completeness, confidence, and source freshness are explicit in the output.

## Design highlights

| Area | Approach |
|---|---|
| Source of truth | Official NFL.com inactives and weekly injury reports first; Sleeper / nflverse / team sites are corroboration or fallback |
| Deduplication | One NFL status lookup per real player, reused across every fantasy league that owns them |
| Confidence | Strongest informing source wins; cached official data after a failed refresh is downgraded |
| Replacements | Deterministic: eligible slot, verified availability, unlocked kickoff, optional depth-chart promotion boost |
| Failure mode | Partial source failure still produces an email that discloses what could not be verified |
| Secrets | ESPN cookies and SMTP credentials live only in `.env`; logs redact credential-like fields |

## Stack

- **Python 3.10+** (developed and tested on 3.12)
- **httpx** for fantasy and NFL HTTP clients
- **BeautifulSoup** for official NFL.com HTML parsing
- **nflreadpy / nflverse** for schedules, identity crosswalks, rosters, and depth charts
- **SMTP + STARTTLS** for multipart email delivery
- **uv** for environment and dependency management
- Native host schedulers: **systemd**, **Windows Task Scheduler**, or **macOS LaunchAgent**

## Quick start

```bash
uv venv --python 3.12
uv sync --extra test
cp config.example.yaml config.yaml
cp .env.example .env
uv run pytest
```

Edit `config.yaml` for season, leagues, timezone, and alert offsets. Put authenticated values only in `.env`:

```env
SLEEPER_USER=...
ESPN_LEAGUE_ID=...
ESPN_SWID=...
ESPN_S2=...
SMTP_USER=...
SMTP_APP_PASSWORD=...
```

## Common commands

```bash
# Read-only readiness checks
uv run fantasy-watchdog health --config config.yaml
uv run fantasy-watchdog plan-game-day --config config.yaml

# Supervised game-day run (waits for planned jobs; can send email)
uv run fantasy-watchdog run-game-day --config config.yaml

# Useful diagnostics
uv run fantasy-watchdog all-rosters --config config.yaml
uv run fantasy-watchdog kickoff-windows --config config.yaml
```

`run-game-day` writes structured JSON events to stderr (job lifecycle, source reports, delivery) with sensitive keys redacted.

## Deployment

The same `run-game-day` entry point is used on every platform. A host scheduler starts it once each morning; the process plans that day’s relevant windows, waits, executes prefetch/final jobs, and exits.

| Platform | Guide |
|---|---|
| Linux | [deploy/systemd](deploy/systemd/README.md) |
| Windows | [deploy/windows](deploy/windows/README.md) |
| macOS | [deploy/macos](deploy/macos/README.md) |

See [deploy/README.md](deploy/README.md) for choosing a host. The machine must stay powered on (or wakeable) through the day’s kickoff windows.

## Testing

Parser and decision logic are covered by offline fixture tests so scrapers and precedence rules can be validated without live NFL pages:

```bash
uv run --extra test pytest
```

## Project layout

```text
app/
  fantasy/          # Sleeper + ESPN adapters, shared manager
  nfl/              # schedule, identity, depth charts, status sources
  analysis/         # severity, eligibility, replacements, opportunity
  notification/     # email rendering + SMTP transport
  scheduling/       # planner, prefetch, final, production runner
  storage/          # T−90 status cache
  health.py         # operational probes
  structured_logging.py
tests/              # fixture-backed unit and integration coverage
deploy/             # Linux / Windows / macOS installers
```

## Scope

Fantasy Watchdog is a pre-kickoff safety system. It does not auto-set lineups, claim waivers, scrape sports-news sites broadly, or claim that a depth-chart promotion guarantees fantasy production.
