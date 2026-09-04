# Fantasy Watchdog — Multi-League NFL Game-Day Alert System

## 1. Project Goal

Build a lightweight Python service that monitors a user's fantasy-football lineups across multiple leagues and emails a consolidated game-day status report shortly before NFL kickoff.

Initial target configuration:

- **4 fantasy leagues total** (more details can be found in reminders.md)
  - **3 Sleeper leagues**
  - **1 private ESPN fantasy-football league**
- **Email notification** approximately **5 minutes before each relevant NFL kickoff window**
- **Official/near-official NFL data sources first**
- **nflreadpy / nflverse** used primarily for schedules, player identity, roster/depth-chart context, and fallback injury data
- **Sleeper API** used both for the user's Sleeper leagues and as a structured fallback status source
- **Private ESPN league** accessed using the user's `SWID` and `espn_s2` cookies
- One status lookup per relevant real NFL player, even if that player appears in several fantasy leagues; include unowned depth-chart blockers when their status affects an owned player's opportunity
- One consolidated email per kickoff window, with separate league-specific sections and recommendations

The central question the tool should answer is:

> **“Five minutes before these NFL games begin, are any players currently in my fantasy starting lineups inactive, out, risky, or otherwise in need of attention—and if so, what viable bench alternatives do I have in each affected league?”**

The first version should prioritize **reliable availability detection** over sophisticated start/sit optimization.


---

# Validated Feasibility and Required Corrections

The data-source design was exercised against live endpoints on **September 4, 2026**. The probes were read-only, used temporary storage, and left no generated files in the project.

## Feasibility verdict

The service is feasible, with the following qualifications:

- Sleeper league discovery, settings, rosters, starters, bench players, and player metadata are accessible from the username alone.
- The ESPN response shape needed by the project is available through its private fantasy API, but the user's actual league still requires an authenticated smoke test with `SWID`, `espn_s2`, league ID, season, and preferably team ID.
- nflreadpy works for schedules, current rosters, players, teams, depth charts, and fantasy-player ID crosswalks.
- NFL.com weekly injury reports are clean server-rendered tables.
- NFL.com game-day inactives are available, but generally as a live landing page and continuously updated news articles rather than a documented JSON API.
- Official team-site content is obtainable but is editorially and structurally inconsistent. It must be optional context, not a required V1 status source.
- A game-day acceptance test is still required when live 2026 injury reports and inactive lists are being published.

## Observed source behavior

| Source | Live observation | Implementation consequence |
|---|---|---|
| Sleeper user/leagues | `worldwideworm` resolves to user ID `1024779386450538496` and exactly three active 2026 NFL leagues | Automatic discovery is viable; persist the stable user ID after resolving the username |
| Sleeper rosters | All three user rosters, starters, and benches were retrievable | `players` and `starters` are the authoritative membership/lineup arrays |
| Sleeper roster caching | Responses advertised a 300-second shared-cache TTL plus stale-while-revalidate behavior | A nominal T−5 fetch may still be several minutes old; record response age and consider a carefully tested final cache-busted request |
| Sleeper player catalog | Approximately 14.7 MB and 12,226 entries; advertised a 600-second shared-cache TTL | Fetch once, cache it, and never request it per player |
| ESPN fantasy API | A public sample returned teams, owners, roster entries, lineup slots, eligible slots, player IDs, NFL team IDs, settings, and injury statuses | Use repeated `view` parameters against `lm-api-reads.fantasy.espn.com`; validate response shape, not just HTTP 200 |
| nflreadpy 0.1.5 | 2026 schedules, current rosters, players, teams, depth charts, and ID crosswalks loaded successfully | Pin a tested version and add schema-contract tests |
| nflreadpy runtime | Package metadata requires Python 3.10 or newer; installation failed under Python 3.9 and worked under Python 3.12 | Set project requirement to Python `>=3.10`; Python 3.12 is the tested local runtime |
| nflreadpy season boundary | Before the Thursday following Labor Day, some loaders still consider the prior year the current season | Always pass the configured season explicitly and handle dataset-specific `not available yet` results |
| NFL.com injury report | A completed Week 18 sample contained 32 tables and 415 player rows | Static HTML parsing is practical; associate each table with its game/team container |
| NFL.com inactives | A completed Week 18 article used semantic team headings and player lists and first appeared about 85 minutes before the early games | Discover the current article, poll until all relevant teams are present, and validate completeness before treating absence as active |
| Official team articles | Packers, Chiefs, and Patriots samples respectively used lists, plain paragraphs, and narrative/social content | Use per-team or per-template adapters only as optional context |

## Current Sleeper inventory

The September 4 snapshot contained:

| League | Teams | User roster | Actual players | Starters | Bench by `players - starters` |
|---|---:|---:|---:|---:|---:|
| CFL | 12 | 8 | 14 | 9 | 5 |
| No More Fields 2026-27 | 12 | 11 | 15 | 10 | 5 |
| Cry Dynasty | 10 | 7 | 25 | 10 | 15 |

`Cry Dynasty` returned 25 actual players even though its `roster_positions` array described only 17 nominal slots. This can occur around keeper/dynasty roster-management periods. Never derive membership or bench size solely from configured slot counts.

## Reliability rules learned from the probes

1. HTTP 200 does not mean usable data. Validate required fields, team coverage, row counts, and source timestamps.
2. An empty NFL page can mean `NOT_YET_PUBLISHED`; it must not become `ACTIVE`.
3. A player absent from an inactive article can be considered active only after both complete team lists for that game have been parsed **and** the player is confirmed eligible for the game-day roster. IR/PUP/NFI/suspended/exempt players may be absent from the inactive list because they were never eligible to dress.
4. Official NFL HTML generally supplies player name, team, and position, but not a stable player ID. Name-based resolution is unavoidable at that boundary and must use team and position as additional constraints.
5. Source retrieval time, source publication/update time, HTTP cache age, and fantasy decision time are different timestamps and should be stored separately.
6. The system must distinguish a source failure from an empty-but-valid response and distinguish both from a successfully parsed complete report.
7. Depth charts are useful for identifying likely opportunity beneficiaries, but they do not by themselves predict touches, routes, targets, or fantasy production.

## Reference endpoints and documentation

Sleeper:

```text
https://docs.sleeper.com/
GET https://api.sleeper.app/v1/user/{username_or_user_id}
GET https://api.sleeper.app/v1/user/{user_id}/leagues/nfl/{season}
GET https://api.sleeper.app/v1/league/{league_id}
GET https://api.sleeper.app/v1/league/{league_id}/rosters
GET https://api.sleeper.app/v1/league/{league_id}/users
GET https://api.sleeper.app/v1/league/{league_id}/matchups/{week}
GET https://api.sleeper.app/v1/players/nfl
```

ESPN fantasy:

```text
GET https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}
```

NFL/nflverse:

```text
https://www.nfl.com/inactives/
https://www.nfl.com/injuries/league/{season}/reg{week}
https://nflreadpy.nflverse.com/api/load_functions/
https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html
https://nflreadr.nflverse.com/articles/dictionary_schedules.html
https://nflreadr.nflverse.com/articles/dictionary_depth_charts.html
```

Validated historical parsing samples:

```text
https://www.nfl.com/news/nfl-week-18-inactives-players-ruled-out-for-sunday-s-14-games
https://www.nfl.com/injuries/league/2025/reg18
https://www.packers.com/news/packers-bears-inactives-week-18-2024
https://www.chiefs.com/news/week-18-inactive-players-chiefs-vs-raiders
https://www.patriots.com/news/inactives-analysis-all-three-patriots-quarterbacks-are-officially-active-for-sunday-s-season-finale-vs-the-bills
```


---

# 2. Core Design Principles

## 2.1 Fantasy platforms are roster sources, not the primary NFL truth source

Use:

- ESPN to determine:
  - the user's leagues
  - current roster
  - current starting lineup
  - bench
  - lineup slots
  - ESPN-side injury/status metadata

- Sleeper to determine:
  - the user's Sleeper leagues
  - current roster
  - current starters
  - league settings
  - lineup slots
  - Sleeper-side player/status metadata

NFL availability should instead be derived primarily from official NFL or team sources.

---

## 2.2 “Five minutes before kickoff” is the notification time, not the first status check

Inactive information is generally available well before kickoff.

Recommended flow:

- **T−95 to T−75:** silent pre-fetch with retries until a complete official report is available
- **T−15:** optional resilience refresh when the T−90 attempt was incomplete or failed
- **T−5:** final status attempt + lineup refresh + email

This gives redundancy if an official website or API fails at the final moment.

“Refresh” means a new request was attempted; it does not automatically mean the response is origin-fresh. Capture HTTP cache headers/age where available. Sleeper roster responses may be cached for approximately five minutes, so the T−5 job must disclose or mitigate that limitation.

---

## 2.3 Group work by real NFL player and NFL kickoff, not by fantasy league

If Josh Jacobs appears in three fantasy leagues:

- fetch his NFL status **once**
- reuse that result in all three league reports

If six of the user's players are in games kicking at 1:00 PM:

- run one 1:00 PM status pipeline
- send one consolidated 12:55 PM email
- include separate notes for each fantasy league

---

## 2.4 Never treat missing data as “healthy”

A failed scrape, timeout, parse error, or absent field must produce `UNKNOWN`, not `ACTIVE`.

Bad:

```python
if player not in injury_data:
    healthy = True
```

Correct:

```python
if source_failed:
    game_day_state = GameDayState.UNKNOWN
    injury_designation = InjuryDesignation.UNKNOWN
```

The email should explicitly state when official verification failed.

---

## 2.5 Separate game-day participation from injury risk

Do not force `Active` and `Questionable` into one mutually exclusive enum. A player may be officially active and still carry a questionable designation or workload risk.

Represent at least three axes:

```text
roster_eligibility: ELIGIBLE | INELIGIBLE | UNKNOWN
game_day_state: ACTIVE | INACTIVE | UNKNOWN
designation: OUT | DOUBTFUL | QUESTIONABLE | NONE | UNKNOWN
```

Fantasy severity is derived from both axes plus source confidence.

---

## 2.6 Treat depth-chart changes as opportunity evidence, not projections

If a player ahead of an owned player becomes unavailable, the owned player may gain opportunity. This is useful evidence for replacement ranking, but a depth chart does not establish how vacated snaps, routes, targets, or carries will be distributed.

Use three levels:

```text
PROMOTED               first healthy player remaining in the same formation slot
ROLE_BOOST             moved upward in the same slot but is not first
POSITIONAL_OPPORTUNITY a starter at the same broad position is out, but no direct slot promotion exists
```

Only the first level should receive a strong deterministic ranking boost in V1.

---

# 3. High-Level Architecture

```text
                         ┌────────────────────────────┐
                         │  Fantasy Platform Sources   │
                         └──────────────┬─────────────┘
                                        │
                    ┌───────────────────┴───────────────────┐
                    │                                       │
                    ▼                                       ▼
             ESPN Adapter                          Sleeper Adapter
          SWID + espn_s2                         Public REST API
                    │                                       │
                    └───────────────────┬───────────────────┘
                                        ▼
                              Normalized League Model
                                        │
                                        ▼
                                All Fantasy Rosters
                                        │
                                        ▼
                              Player Identity Resolver
                                        │
                    ┌───────────────────┴───────────────────┐
                    │                                       │
                    ▼                                       ▼
           nflreadpy NFL Schedule                Latest Depth-Chart Snapshot
                    │                                       │
                    └───────────────────┬───────────────────┘
                                        ▼
                         Group relevant players by kickoff
                                        │
                         ┌──────────────┴──────────────┐
                         │                             │
                      T−90-ish                        T−5
                         │                             │
                         ▼                             ▼
                  Silent status cache        Final status refresh
                                                     +
                                            Refresh fantasy lineups
                                                     │
                                                     ▼
                                      Official NFL / team sources
                                                     │
                                                     ▼
                                             Fallback sources
                                                     │
                                                     ▼
                                         Unified NFL status model
                                                     │
                                  ┌──────────────────┴──────────────────┐
                                  │                                     │
                                  ▼                                     ▼
                     Depth-opportunity resolver             Owned-player status map
                                  │                                     │
                                  └──────────────────┬──────────────────┘
                                                     ▼
                                   Map result back to every league
                                                     │
                                                     ▼
                                  Per-league starter/replacement logic
                                                     │
                                                     ▼
                                          One consolidated email
```


---

# 4. Data-Source Strategy

## 4.1 Source priority

### Primary game-day sources

1. **NFL.com official inactives**
   - Highest-value source for binary game-day active/inactive status
   - Use as the strongest T−5 authority
   - Usually exposes team/name/position rather than stable player IDs
   - May be published as a continuously updated article rather than a fixed API response
   - Must pass a per-game completeness check before absence can mean active

2. **NFL.com official injury reports**
   - Official weekly/game-status designations
   - Useful for `Out`, `Doubtful`, `Questionable`, etc.
   - Important context even when the player is active
   - Server-rendered tables are currently practical to parse
   - A valid page with zero tables means not yet published, not healthy

### Optional official context

3. **Official team website / official team game-status content**
   - Useful for late-breaking context:
     - active/inactive announcements
     - expected workload
     - activated from IR
     - elevated from practice squad
     - “will play”
     - “expected to play”
     - limited-role notes
   - Subordinate to the official inactive designation for binary active/inactive status
   - Must not be required for V1 completion because team editorial formats vary
   - Natural-language workload claims should be included as attributed context, not converted into a high-confidence binary status

### Secondary/fallback sources

4. **ESPN player status**
   - Useful because it reflects the fantasy platform's current understanding
   - Not authoritative enough to override official NFL data

5. **Sleeper player/status API**
   - Structured independent fallback
   - Useful fields may include injury/status/practice metadata
   - Good corroboration layer

6. **nflreadpy / nflverse injury data**
   - Useful structured fallback
   - Do not assume it is updated within seconds of game-time changes
   - Availability can lag the new season and differs from schedules/rosters/depth charts
   - Treat unsupported or unavailable seasons as a source-level failure, not an application error

7. **nflreadpy roster/depth-chart data**
   - Context and player/team resolution
   - Not primary live availability authority
   - Latest depth-chart snapshot can identify same-slot opportunity beneficiaries
   - It must not be interpreted as a fantasy projection


---

# 5. Source Precedence Rules

Use explicit precedence rather than loosely averaging sources.

Suggested hierarchy:

```text
OFFICIAL NFL INACTIVE — only after game report is complete
        │
        ├── listed inactive → game_day_state=INACTIVE / OFFICIAL
        └── absent from both complete team lists
                ├── roster eligible → game_day_state=ACTIVE / OFFICIAL
                └── eligibility unknown/ineligible → do not infer active

OFFICIAL NFL WEEKLY GAME STATUS
        │
        ├── Out → designation=OUT / OFFICIAL
        ├── Doubtful → designation=DOUBTFUL / OFFICIAL
        ├── Questionable → designation=QUESTIONABLE / OFFICIAL
        └── blank/absent → designation=NONE only when the report is complete

OPTIONAL OFFICIAL TEAM CONTEXT
        │
        └── attributed notes; cannot override the official inactive list

ESPN / SLEEPER / NFLVERSE FALLBACKS
        │
        └── corroboration or degraded-mode evidence with lower confidence
```

Important:

- If a player is on the official inactive list, that wins.
- Do not set `game_day_state=ACTIVE` until both team lists for that game were parsed and validated as complete and roster eligibility is confirmed.
- If official data is unavailable, fallbacks may support a conclusion, but confidence must be lower.
- A team-site note should not override an official inactive list.
- “Not on inactive list” can support an active conclusion only when the report is complete and the player is roster-eligible; the weekly injury designation may still matter for workload/risk.
- An official weekly `Out` designation and a missing/not-yet-published inactive report should remain `game_day_state=UNKNOWN`, `designation=OUT`; do not fabricate a final active/inactive state.
- Preserve contradictory evidence in `source_results` rather than silently discarding it.


---

# 6. Unified Player Status Model

Suggested enums:

```python
from enum import Enum

class GameDayState(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"

class RosterEligibility(Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"

class InjuryDesignation(Enum):
    OUT = "out"
    DOUBTFUL = "doubtful"
    QUESTIONABLE = "questionable"
    NONE = "none"
    UNKNOWN = "unknown"

class Confidence(Enum):
    OFFICIAL = 4
    HIGH = 3
    MEDIUM = 2
    LOW = 1
```

Example status object:

```python
@dataclass
class NFLPlayerStatus:
    canonical_player_id: str
    roster_eligibility: RosterEligibility
    game_day_state: GameDayState
    injury_designation: InjuryDesignation
    confidence: Confidence

    injury_description: str | None

    official_inactive: bool | None
    source_results: list["SourceResult"]

    decision_at: datetime
```

Each `SourceResult` must separately capture:

```python
@dataclass
class SourceResult:
    source: str
    source_url: str | None
    success: bool
    report_state: str  # COMPLETE, PARTIAL, NOT_YET_PUBLISHED, FAILED

    roster_eligibility: RosterEligibility | None
    game_day_state: GameDayState | None
    injury_designation: InjuryDesignation | None
    detail: str | None

    published_at: datetime | None
    source_updated_at: datetime | None
    retrieved_at: datetime
    http_cache_age_seconds: int | None
    raw_content_hash: str | None
```

Example:

```text
Josh Jacobs
Roster eligibility: ELIGIBLE
Game-day state: ACTIVE
Confidence: OFFICIAL
Official inactive: False
Injury designation: QUESTIONABLE
Injury: ankle
```

Fantasy interpretation:

> Active, but carries injury risk.


---

# 7. Fantasy Platform Abstraction

All downstream logic should be platform-agnostic.

Define a common interface:

```python
class FantasyPlatform:
    def get_leagues(self) -> list["FantasyLeague"]:
        ...

    def get_roster(self, league_id: str) -> "FantasyRoster":
        ...

    def refresh_lineup(self, league_id: str) -> "FantasyRoster":
        ...
```

Implement:

```python
class ESPNFantasyPlatform(FantasyPlatform):
    ...

class SleeperFantasyPlatform(FantasyPlatform):
    ...
```

Everything after normalization should work identically regardless of platform.


---

# 8. ESPN Integration

## 8.1 Authentication

The ESPN league is private.

Required credentials:

```text
ESPN_SWID
ESPN_S2
```

Required configuration:

```text
ESPN_LEAGUE_ID
ESPN_TEAM_ID
ESPN_SEASON
```

Use the private ESPN fantasy API with authenticated cookies rather than scraping rendered ESPN webpages.

Use the current read host and repeated view parameters:

```text
GET https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}
    ?view=mTeam
    &view=mRoster
    &view=mSettings
```

Do not comma-join view names. Validate that the response contains `teams[].roster.entries` and the requested settings; ESPN may return a small but syntactically valid response when a view is wrong or ignored.

Do not commit credentials to source control.


## 8.2 ESPN data to fetch

At minimum:

- league ID
- league name
- team ID / fantasy roster ID
- player name
- ESPN player ID
- NFL team
- fantasy position
- lineup slot
- starter vs bench
- injury designation/status
- league roster-slot settings
- scoring settings if available

Observed roster-entry fields include:

```text
teams[].roster.entries[].lineupSlotId
teams[].roster.entries[].playerId
teams[].roster.entries[].playerPoolEntry.player.id
teams[].roster.entries[].playerPoolEntry.player.fullName
teams[].roster.entries[].playerPoolEntry.player.proTeamId
teams[].roster.entries[].playerPoolEntry.player.defaultPositionId
teams[].roster.entries[].playerPoolEntry.player.eligibleSlots
teams[].roster.entries[].playerPoolEntry.player.injuryStatus
```

Do not rely on the generic `injured` boolean or the entry-level `injuryStatus` alone. In the sample response, players with `QUESTIONABLE` player status still had `injured=False`, and entry status remained `NORMAL`.

Map the configured team using `teams[].owners`/`primaryOwner`, `members[].id`, and/or explicit `ESPN_TEAM_ID`. Explicit team ID is the safest operational configuration.


## 8.3 Refresh at T−5

Always re-fetch the ESPN lineup just before generating the final email.

The user may have changed the lineup earlier in the day.

Validate and classify failures explicitly:

- 401/403: credentials missing, invalid, expired, or unauthorized
- 404: wrong league ID, wrong season, league not activated for that season, or unavailable historical path
- redirect/login HTML: invalid authenticated response
- 200 with missing roster/settings shape: invalid or ignored views

The user's private league must pass a credentialed end-to-end smoke test before the service is considered ready.


---

# 9. Sleeper Integration

Sleeper is substantially easier because its API is public.

Recommended flow:

```text
Sleeper username
      ↓
Sleeper user ID
      ↓
NFL leagues for target season
      ↓
identify user's roster_id in each league
      ↓
fetch roster
      ↓
read players + starters
      ↓
fetch league settings / roster positions
```

Support both:

- automatic league discovery
- explicit allowlist / inclusion config

Example configuration:

```yaml
sleeper:
  username: "worldwideworm"

  include_leagues:
    - "CFL"
    - "No More Fields 2026-27"
    - "Cry Dynasty"
```

This avoids accidentally monitoring mock, best-ball, test, or abandoned leagues.

Persist the resolved user ID `1024779386450538496` as a cache/diagnostic value, but continue to support resolving the username because usernames can change.

Roster rules:

- Select the user's roster where `owner_id` equals the resolved user ID or the ID appears in `co_owners`.
- Treat `roster.players` as actual membership.
- Treat `roster.starters` as the current starting player IDs.
- Compute bench membership as `players - starters`, then annotate `reserve` and `taxi` separately.
- Map `starters` positionally to non-bench entries in `league.roster_positions`, while validating that counts match.
- Do not assume actual player count equals the nominal number of configured roster positions.

Freshness:

- League roster responses currently advertise a shared-cache TTL of about 300 seconds.
- The full NFL player catalog currently advertises a shared-cache TTL of about 600 seconds and is approximately 14.7 MB.
- Cache the player catalog once and use its ETag when practical.
- Record the HTTP `Date`, `Age`, `ETag`, and cache status headers when supplied.
- A final query parameter produced a cache miss during feasibility testing, but cache busting is undocumented. Use it only for the final low-volume lineup request after confirming the behavior in season.

Player-status semantics:

- Sleeper's `active` boolean means the player is active in the broader player universe; it is not a game-day active declaration.
- `status` may describe roster/employment state and can disagree with `active`.
- `injury_status` currently includes values such as `Questionable`, `IR`, `PUP`, and suspension/not-active variants. Normalize them explicitly rather than passing arbitrary strings into decision logic.
- `practice_participation` and related practice fields were nearly empty in the observed catalog, so Sleeper cannot be the primary practice-report source.
- Team defenses use team-like IDs rather than normal player identities. Resolve them through the team mapping and exclude them from individual depth-chart opportunity logic.


---

# 10. League Configuration

Give every league a short nickname.

Example:

```yaml
leagues:
  sleeper:
    - id: "1389694250752425984"
      roster_id: "8"
      nickname: "CFL"

    - id: "1389362487303892992"
      roster_id: "11"
      nickname: "No More Fields"

    - id: "1359974214391582720"
      roster_id: "7"
      nickname: "Cry Dynasty"

  espn:
    - id: "987"
      nickname: "Main"
```

Store:

```python
@dataclass
class FantasyLeague:
    id: str
    nickname: str
    name: str
    platform: str
    roster_id: str

    roster_rules: object | None = None
    scoring_settings: object | None = None
```

Do not assume all leagues have identical roster rules.


---

# 11. Normalized Fantasy Player Model

```python
@dataclass
class FantasyPlayer:
    canonical_player_id: str
    platform_player_id: str

    name: str
    nfl_team: str
    position: str

    league_id: str
    league_name: str
    platform: str

    lineup_slot: str
    is_starter: bool
    is_reserve: bool = False
    is_taxi: bool = False
```

One NFL player may correspond to several `FantasyPlayer` instances.

Example:

```text
Josh Jacobs
├── Friends — Sleeper — STARTER
├── Family — Sleeper — STARTER
└── Main — ESPN — BENCH
```

But only one universal NFL status record should be created.

Depth-chart relationships are universal NFL context and should also be created once per canonical player, then reused across fantasy instances.


---

# 12. Player Identity Resolution

This deserves a dedicated subsystem.

Do not rely on names alone.

However, official NFL injury and inactive HTML generally does not expose provider IDs. The resolver therefore needs a controlled name fallback rather than a blanket prohibition on names.

Potential problems:

- `Jr.`
- `Sr.`
- `II`
- `III`
- apostrophes
- hyphens
- duplicate names
- ESPN vs Sleeper naming differences
- midseason team changes

Maintain a mapping such as:

```text
canonical_player_id
GSIS ID
ESPN ID
Sleeper ID
nflverse ID
canonical name
team
position
```

Use `nflreadpy` / nflverse cross-provider player-ID data where possible.

Create:

```python
class PlayerIdentityResolver:
    def resolve_espn_player(...):
        ...

    def resolve_sleeper_player(...):
        ...

    def resolve_name_team_position(...):
        ...
```

Preferred canonical identifier: NFL/GSIS-compatible stable ID when available.

Resolution order:

1. Existing exact platform-to-GSIS mapping.
2. nflverse fantasy-player ID crosswalk (`load_ff_playerids`).
3. Current nflverse roster mapping by provider ID.
4. Exact normalized name + current team + compatible position.
5. Name + position with an explicit team-transition allowance when exactly one candidate exists.
6. Manual override table.
7. Unresolved; never silently create a guessed identity.

Use the platform/current roster source for a player's current NFL team. The `team` field in a slow-changing ID crosswalk may be stale after a transaction.

Observed 2026 coverage:

- 770 of 857 active, team-assigned Sleeper fantasy-skill IDs matched the nflverse fantasy crosswalk.
- Among 535 QB/RB/FB/WR/TE/K players marked active on current NFL rosters, all 535 had GSIS IDs, 531 had ESPN IDs, and 524 had Sleeper IDs.
- Direct `gsis_id` coverage in the Sleeper player catalog was much lower, so it must not be the only Sleeper identity path.


---

# 13. nflreadpy Responsibilities

Use `nflreadpy` primarily for:

## Primary

- schedule
- NFL game IDs
- kickoff timestamps
- player identity helpers
- team mappings

## Secondary/context

- rosters
- weekly rosters
- depth charts
- injuries
- fantasy player ID crosswalks

Conceptual functions:

```python
nfl.load_schedules(2026)
nfl.load_players()
nfl.load_rosters(2026)
nfl.load_rosters_weekly(2026)
nfl.load_injuries(2026)
nfl.load_depth_charts(2026)
nfl.load_ff_playerids()
```

Pin and test the precise package version. Feasibility testing used nflreadpy `0.1.5` under Python 3.12; package metadata requires Python 3.10 or newer.

Observed live dataset shapes on September 4, 2026:

| Loader | Result |
|---|---|
| `load_schedules(2026)` | 272 rows × 46 columns |
| `load_players()` | 24,832 rows × 39 columns |
| `load_rosters(2026)` | 2,946 rows × 36 columns, current Week 1 snapshot |
| `load_depth_charts(2026)` | 496,713 rows × 12 columns, 168 appended snapshots since March |
| `load_ff_playerids()` | 12,492 rows × 35 columns |
| `load_rosters_weekly(2026)` | Rejected as not yet supported by that loader on the test date |
| `load_injuries(2026)` | Rejected as not yet supported by that loader on the test date |

Implementation rules:

- Always pass the configured season explicitly.
- Catch unsupported-season errors per dataset.
- Schedules update frequently enough for game-day planning; current rosters and depth charts update daily rather than at T−5 frequency.
- From 2025 onward, depth charts are timestamped snapshots (`dt`) rather than week-numbered records. Filter to the latest snapshot at or before the decision time.
- nflverse documents that its depth-chart source changed to ESPN after the 2024 season. Treat depth data and ESPN fantasy metadata as related evidence, not two independent confirmations.
- Do not load or scan the full historical depth-chart frame for every player lookup. Build one latest-snapshot index per process/run.
- nflverse injuries are a fallback only. The live probe returned 6,068 rows of 2025 history, but the nflverse availability page still warns that the injury source changed/died after 2024, and the Python loader rejected 2026 on the test date. Their season availability and update pipeline must not be inferred from other nflverse datasets.

## Depth-chart interpretation

The current schema contains:

```text
dt
team
player_name
espn_id
gsis_id
pos_grp_id
pos_grp
pos_id
pos_name
pos_abb
pos_slot
pos_rank
```

Treat `pos_slot` as an opaque formation-slot identifier scoped to team/formation/position. For a potential beneficiary, sort players within the same `team + pos_abb + pos_slot` chain by `pos_rank`. Numeric gaps are allowed and do not imply missing records.

The September 4 latest snapshot covered all 32 teams and contained 582 unique QB/RB/FB/WR/TE skill players. All 582 had ESPN IDs and 581 had GSIS IDs. Every QB, RB, and TE slot had a backup; 94 of 96 WR slots had at least one backup.


---

# 14. Schedule and Kickoff Logic

The scheduler should work from actual NFL games, not fantasy leagues.

Example resolved roster:

```text
Ja'Marr Chase → CIN
Josh Jacobs → GB
Courtland Sutton → DEN
Jake Ferguson → DAL
```

Map those teams to actual schedule rows.

Create:

```python
@dataclass
class RelevantGame:
    game_id: str
    home_team: str
    away_team: str
    kickoff: datetime

    fantasy_players: list[FantasyPlayer]
```

Then group by exact kickoff.

Example:

```python
games_by_kickoff = {
    datetime(..., 13, 0): [...],
    datetime(..., 16, 5): [...],
    datetime(..., 16, 25): [...],
    datetime(..., 20, 20): [...],
}
```

Do not merge 4:05 PM and 4:25 PM games.

Send one email per actual kickoff window.

The schedule's `gameday` and `gametime` fields are separate, and `gametime` is expressed in US Eastern time. Combine them in `America/New_York`, then convert to UTC for storage. Do not interpret `gametime` as UTC.


---

# 15. Game-Day Scheduling Strategy

## Stage A — Game-day planning

Run early on each NFL game day.

Steps:

1. Load all fantasy leagues.
2. Fetch all rosters.
3. Normalize starters and bench.
4. Resolve canonical NFL identities.
5. Load NFL schedule.
6. Load the latest depth-chart snapshot and index it by provider ID and team/position/slot.
7. For every owned player, identify players ahead of them in the same depth-chart slot.
8. Match roster players to games.
9. Create jobs for relevant kickoff windows.

Only schedule checks for games involving players on the user's fantasy rosters.

Within those relevant games, retain official status rows for all players on the involved NFL teams. This is necessary to notice when an unowned NFL starter's absence creates opportunity for a player the user does own.

---

## Stage B — T−90-ish silent pre-fetch

Approximately 80–100 minutes before kickoff:

- fetch official NFL inactives if available
- fetch official NFL injury report
- optionally fetch relevant team information
- retry if the official report is not yet published or is incomplete
- cache results
- do **not** send email

Purpose:

- capture official inactive information once published
- create resilience against last-minute source outages

---

## Stage C — T−5 final run

Approximately five minutes before kickoff:

1. Refresh all relevant fantasy lineups.
2. Re-resolve any lineup/player changes if necessary.
3. Re-fetch official NFL inactives.
4. Re-fetch official injury report.
5. Optionally check relevant official team sources without blocking the report.
6. Consult Sleeper/nflverse fallbacks if needed.
7. Compare against T−90 cache.
8. Validate that official reports are complete for each relevant game.
9. Produce unified statuses for owned players and depth-chart blockers on relevant teams.
10. Resolve depth-chart opportunity changes.
11. Map statuses and opportunity changes back to each league.
12. Find and rank league-specific replacement options.
13. Render one consolidated email.
14. Send email.

Every output should include decision time plus source-as-of and retrieval timestamps.


---

# 16. Deduplication Strategy

Maintain two distinct layers.

```python
player_statuses: dict[str, NFLPlayerStatus]

fantasy_instances: list[FantasyPlayer]

depth_relations: dict[str, DepthRelation]

depth_opportunities: dict[str, DepthOpportunity]
```

Example:

```text
Josh Jacobs status fetched ONCE
            │
            ├── Friends League interpretation
            ├── Family League interpretation
            └── ESPN Main interpretation
```

This reduces:

- network traffic
- scrape volume
- parsing work
- inconsistent results
- rate-limit risk

The status map may contain unowned players when they are depth-chart blockers for an owned player. Deduplicate them exactly like owned NFL players, but do not create fantasy instances for them.


---

# 17. Fantasy Severity Logic

Suggested policy:

| Situation | Starter | Bench |
|---|---|---|
| Officially inactive | CRITICAL | INFO |
| Out | CRITICAL | INFO |
| Questionable but active | WARNING | LOW |
| Doubtful | WARNING/CRITICAL | LOW |
| Healthy | NORMAL | usually omit |
| Status unknown | WARNING | INFO |

Suggested enum:

```python
class FantasyAlertSeverity(Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"
    NORMAL = "normal"
```


---

# 18. Starter Decision Logic — V1

Keep V1 deterministic.

```python
if starter.status.roster_eligibility == RosterEligibility.INELIGIBLE:
    severity = CRITICAL

elif starter.status.game_day_state == GameDayState.INACTIVE:
    severity = CRITICAL

elif starter.status.injury_designation == InjuryDesignation.OUT:
    severity = CRITICAL

elif starter.status.injury_designation in {
    InjuryDesignation.DOUBTFUL,
    InjuryDesignation.QUESTIONABLE,
}:
    severity = WARNING

elif starter.status.game_day_state == GameDayState.UNKNOWN:
    severity = WARNING

else:
    severity = NORMAL
```

Do not begin V1 with AI-based start/sit analysis.


---

# 19. Bench Replacement Logic

When a starter is unavailable or highly risky, evaluate league-specific bench alternatives.

Required checks:

1. Player is on that league's bench.
2. Player is eligible for the affected lineup slot.
3. Player's NFL game has **not started**.
4. Player is confirmed not inactive/out.
5. Prefer healthier/higher-confidence alternatives.
6. Unknown-status candidates are excluded by default or placed in a clearly labeled unverified tier when no verified candidate exists.

Example:

```python
replacement_candidates = [
    p for p in league_roster.bench
    if eligible_for_slot(p, affected_slot, league.roster_rules)
    and not game_has_started(p)
    and player_status[p.canonical_player_id].roster_eligibility
        == RosterEligibility.ELIGIBLE
    and player_status[p.canonical_player_id].game_day_state
        == GameDayState.ACTIVE
    and player_status[p.canonical_player_id].injury_designation
        not in {InjuryDesignation.OUT, InjuryDesignation.DOUBTFUL}
]
```


---

# 20. League-Specific Eligibility

Do not globally hardcode only one lineup structure.

A conceptual baseline:

```python
ELIGIBILITY = {
    "QB": {"QB"},
    "RB": {"RB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "FLEX": {"RB", "WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
}
```

But actual rules should come from each league's configuration.

Reason:

- one league may have one FLEX
- another may have two FLEX
- another may have SUPERFLEX
- slot labels differ between platforms


---

# 21. Replacement Ranking — V1 vs V2

## V1

Rank by simple deterministic criteria:

1. valid eligibility
2. game not started
3. known active/healthy
4. fewer injury concerns
5. same-slot depth promotion, when supported by a fresh depth snapshot and official unavailability evidence
6. optionally ESPN/Sleeper projected points if trivially available

## V2

Potential scoring:

```python
replacement_score = (
    projected_points
    * availability_probability
    * workload_factor
)
```

Possible future features:

- fantasy projection
- PPR/half-PPR/standard scoring
- opponent
- Vegas spread
- team total
- expected workload
- historical depth-chart movement and stability
- target/carry share
- injury limitations

Do not block V1 on these features.

## 21.1 Depth-aware opportunity detection

Depth data can identify owned players who may benefit when a teammate ahead of them becomes unavailable.

Suggested models:

```python
class OpportunityLevel(Enum):
    PROMOTED = "promoted"
    ROLE_BOOST = "role_boost"
    POSITIONAL_OPPORTUNITY = "positional_opportunity"


@dataclass
class DepthRelation:
    canonical_player_id: str
    team: str
    position: str
    formation: str
    position_slot: int
    source_rank: int
    players_ahead: list[str]
    snapshot_at: datetime


@dataclass
class DepthOpportunity:
    beneficiary_player_id: str
    unavailable_player_ids: list[str]
    level: OpportunityLevel

    previous_order_in_slot: int
    effective_order_in_slot: int
    promoted_to_first_available: bool

    confidence: Confidence
    depth_chart_as_of: datetime
    status_decision_at: datetime
```

Algorithm:

1. Select the latest depth snapshot at or before decision time.
2. Resolve every owned QB/RB/WR/TE to a depth row by GSIS ID, then ESPN ID, then constrained name/team/position.
3. Build chains keyed by `team + formation + pos_abb + pos_slot`.
4. Sort each chain by `pos_rank`; do not require ranks to be contiguous.
5. Resolve availability for every player ahead of an owned player in that chain.
6. Remove only officially inactive/out players from the effective chain. Treat unknown blockers as still ahead, with reduced confidence.
7. Compare the owned player's original and effective order.
8. Emit `PROMOTED` only when the owned player becomes the first available player in the same slot.
9. Emit `ROLE_BOOST` when the player moves upward but remains behind another available player.
10. Optionally emit `POSITIONAL_OPPORTUNITY` when a starter in another slot at the same broad position is unavailable. This is contextual and should carry lower confidence.
11. Apply the signal separately in each fantasy league: an existing starter gets an “outlook improved” note; a bench player receives a replacement-ranking boost when eligible and unlocked.

Do not assume all production transfers to the next depth player. Wide receivers can change alignment, running backs can split early-down, passing-down, and goal-line work, and teams may change personnel groupings. Later versions should combine depth promotion with recent snap share, routes, targets/carries, projections, and scoring format.

### Validated Bears example

The September 4, 2026 Bears snapshot used a `3WR 1TE` formation and contained three distinct WR slots:

| Slot | First player | Next player |
|---:|---|---|
| 1 | Rome Odunze — rank 1 | Zavion Thomas — rank 4 |
| 2 | Luther Burden III — rank 2 | Jahdae Walker — rank 5 |
| 8 | Kalif Raymond — rank 3 | Ray-Ray McCloud III — rank 6 |

Therefore:

- If Rome Odunze is inactive, Zavion Thomas is the direct same-slot promotion.
- Jahdae Walker may have broader positional opportunity, but the depth chart alone does not identify him as Rome's direct replacement.
- If Luther Burden III is inactive, Jahdae Walker is the direct same-slot promotion.
- Jahdae moved from slot 1/rank 4 to slot 2/rank 5 between recent daily snapshots, demonstrating why the latest timestamp must be used.

Jahdae Walker resolved to GSIS `00-0040277`, ESPN `5160110`, and Sleeper `13079`. He was not rostered in any of the three target Sleeper leagues at the time of validation.

### Validated opportunity relationships on current target rosters

The proof-of-concept join found examples such as:

- TreVeyon Henderson behind Rhamondre Stevenson.
- Alvin Kamara behind Travis Etienne Jr.
- Jordan Mason behind Aaron Jones Sr.
- Kaelon Black behind Christian McCaffrey.
- Nicholas Singleton behind Tyjae Spears.

These relationships are point-in-time source observations, not permanent football judgments. Recompute them from the latest game-day snapshot rather than hardcoding them.


---

# 22. League Scoring Settings

Store league scoring information from the beginning even if V1 does not use it.

Example:

```python
FantasyLeague(
    ...
    scoring_settings={...}
)
```

This makes later league-aware recommendations possible without redesigning the data model.


---

# 23. Consolidated Email Design

Send **one email per kickoff window**, not one email per league.

Recommended subject:

```text
🚨 Fantasy Check — 4:25 PM ET kickoff in 5 min
```

Organize by urgency first, then league.

Example:

```text
4:25 PM ET GAMES
Final check: 4:20:31 PM ET

🚨 ACTION NEEDED

Friends — Sleeper

Jayden Reed — STARTING
❌ OFFICIALLY INACTIVE — knee

Suggested replacements:
1. Khalil Shakir — WR — 8:20 PM — healthy
2. Romeo Doubs — WR — 4:25 PM — active


⚠️ RISK

Family — Sleeper

Josh Jacobs — STARTING
✅ Active
⚠️ Questionable — ankle
Not listed among official inactives.


ℹ️ BENCH NOTES

Dynasty — Sleeper

Jayden Reed — BENCH
❌ Inactive
No lineup action required.


✅ NO ACTION

Main — ESPN
Ja'Marr Chase — Active
Courtland Sutton — Active


LEAGUE SUMMARY

Friends
🚨 1 lineup issue
⚠️ 0 warnings

Family
🚨 0 lineup issues
⚠️ 1 warning

Dynasty
✅ No starter issues

Main
✅ Clear


Sources checked:
✓ NFL official inactives
✓ NFL injury report
— official team context not required / not checked
✓ Sleeper
✓ ESPN

Decision time: 4:20:31 PM ET
NFL inactives source updated: 4:17:08 PM ET
Sleeper lineup response age: 42 seconds
Depth chart snapshot: 7:57:41 AM ET
Kickoff: 4:25 PM ET
```

Healthy bench players should normally be omitted unless they are relevant replacement options.

Depth opportunity notes should be concise and explain the relationship:

```text
📈 OPPORTUNITY CHANGE

CFL — Sleeper
TreVeyon Henderson — BENCH
Rhamondre Stevenson is officially inactive.
Henderson is now the first available player in the same NE RB depth slot.
Consider for FLEX; depth signal is not a workload guarantee.
```


---

# 24. Email Transport

For a personal tool, email is simpler than SMS, Discord, or push notifications.

Suggested V1 options:

- Gmail SMTP with app password
- or a transactional email provider

Suggested interface:

```python
class EmailNotifier:
    def send_game_alert(
        self,
        kickoff: datetime,
        report: "FantasyGameReport",
    ) -> None:
        ...
```

Environment variables:

```text
SMTP_USER
SMTP_APP_PASSWORD
NOTIFICATION_EMAIL
```

Do not hardcode credentials.


---

# 25. Status Source Interface

Every source should implement a common contract.

```python
class PlayerStatusSource:
    name: str
    priority: int

    def fetch_game(
        self,
        game: RelevantGame,
    ) -> "GameSourceReport":
        ...
```

```python
@dataclass
class GameSourceReport:
    source: str
    game_id: str
    report_state: str  # COMPLETE, PARTIAL, NOT_YET_PUBLISHED, FAILED
    expected_teams: set[str]
    parsed_teams: set[str]
    player_results: list[SourceResult]
    retrieved_at: datetime
    source_updated_at: datetime | None
    errors: list[str]
```

This game-level wrapper is required because “player absent” has meaning only when the containing report is complete.

Suggested implementations:

```text
NFLInactivesSource
NFLInjuryReportSource
OfficialTeamSource
ESPNStatusSource
SleeperStatusSource
NFLVerseStatusSource
```

This makes it easy to replace a scraper if a website changes.


---

# 26. NFL.com / Team Scraping Strategy

Prefer, in order:

1. structured endpoint / embedded JSON if present
2. static HTML parsing
3. browser/network extraction only if necessary

Avoid making Selenium/Playwright the default if ordinary HTTP requests work.

Reasons:

- fewer dependencies
- faster execution
- easier deployment
- lower memory use
- lower failure rate

Scrapers must have:

- timeouts
- retries with backoff
- parse validation
- explicit failure states
- fixture-based parser tests

NFL inactives implementation details:

- Check the league inactives landing page first.
- Discover/link the current kickoff-window article when the landing page points to one.
- Parse semantic window/team headings and their following player lists.
- Strip position prefixes and annotations such as “Emergency QB” only after preserving the raw text.
- Validate expected home and away teams against the schedule.
- Mark a game complete only when both teams are present and each section passes structural validation.
- Store article publication/update metadata when available.
- Retry `NOT_YET_PUBLISHED` and `PARTIAL` reports through the final check.

NFL weekly injury implementation details:

- Use the configured season/week path.
- Parse each game container and associate its two team tables with schedule teams.
- Parse `Player`, `Position`, `Injuries`, `Practice Status`, and `Game Status` columns.
- Do not trust the HTML page title for the week; validate the URL path, visible week heading, game date, and teams.
- A 200 response with zero tables is `NOT_YET_PUBLISHED`.

Official team implementation details:

- Prefer embedded structured article data when available.
- Otherwise preserve attributed text/link for display.
- Require a team/template-specific parser before deriving structured status.
- Never use generic keyword extraction to override league-wide official status.


---

# 27. Caching

Cache:

- T−90 source results
- player ID mappings
- schedule
- team mappings
- slow-changing league metadata

Do not cache:

- final T−5 fantasy lineup without refreshing
- final T−5 official status without attempting a refresh

Upstream/CDN caching still applies. A new request is not proof of fresh origin data. Persist response `Date`, `Age`, `ETag`, `Last-Modified`, cache-control, and source timestamps when available, and show meaningful staleness in the report.

Example:

```text
cache/
  schedule_2026.parquet
  player_ids.parquet
  sleeper_players_ETAG.json
  depth_chart_latest_TIMESTAMP.parquet
  status/
    GAME_ID_TIMESTAMP.json
```


---

# 28. Storage

SQLite is more than sufficient for V1.

Suggested tables:

## `leagues`

```text
league_id
platform
league_name
nickname
user_roster_id
```

## `fantasy_players`

```text
league_id
canonical_player_id
platform_player_id
slot
is_starter
is_reserve
is_taxi
last_seen
```

## `nfl_players`

```text
canonical_player_id
gsis_id
name
team
position
espn_id
sleeper_id
```

## `status_snapshots`

```text
canonical_player_id
game_id
timestamp
roster_eligibility
game_day_state
injury_designation
injury_description
source
confidence
raw_reference
report_state
published_at
source_updated_at
retrieved_at
http_cache_age_seconds
raw_content_hash
```

## `depth_relations`

```text
canonical_player_id
team
formation
position
position_slot
source_rank
players_ahead_json
snapshot_at
```

## `depth_opportunities`

```text
game_id
beneficiary_player_id
unavailable_player_ids_json
opportunity_level
previous_order
effective_order
promoted_to_first_available
confidence
depth_chart_as_of
status_decision_at
```

## `scheduled_games`

```text
game_id
kickoff
home_team
away_team
```


---

# 29. Scheduling / Deployment

Avoid relying on GitHub Actions for precise T−5 execution.

Runtime baseline:

```text
Python >=3.10
Tested locally: Python 3.12 + nflreadpy 0.1.5
```

Pin dependencies and run source-contract tests before upgrading nflreadpy, Polars, or HTML parser libraries.

Preferred options:

## Option A — Always-on Python service

- VPS
- Raspberry Pi
- home server
- small cloud VM
- APScheduler or equivalent
- systemd for process supervision

Simplest operational model.

## Option B — AWS EventBridge Scheduler + Lambda

Flow:

```text
game-day planner
      ↓
create T−90 and T−5 scheduled invocations
      ↓
Lambda runs check
      ↓
email
```

Good fit for low-frequency event-driven workloads.

## Option C — Traditional cron

Works well on an always-on host, but dynamic per-game scheduling may be easier inside the Python application.


---

# 30. Time Zones

All stored timestamps should be timezone-aware.

Recommended:

- store normalized timestamps in UTC
- render emails in `America/New_York` / ET unless user config says otherwise

Never use naïve datetimes for kickoff scheduling.


---

# 31. Security

## Secrets

Never commit:

```text
ESPN_SWID
ESPN_S2
SMTP_APP_PASSWORD
```

Use:

```text
.env locally
cloud secret store in production
```

`.gitignore`:

```gitignore
.env
*.log
cache/
*.sqlite
```

Treat `espn_s2` as a sensitive authenticated credential.

Never print cookies to logs.

---

# 32. Authentication Failure Handling

If ESPN authentication fails:

- detect 401/403/login redirect/invalid response
- mark ESPN league refresh failed
- send a clear operational error notification
- do not silently continue as though the lineup is current

Example:

```text
Fantasy Watchdog ERROR

ESPN authentication failed.
Refresh ESPN_SWID / ESPN_S2.
```

Sleeper failures should similarly be explicit.


---

# 33. Failure and Degradation Policy

The system should still produce a useful email when some sources fail.

Example:

```text
⚠️ Jayden Reed — STARTING

Official NFL status could not be refreshed.
Cached NFL inactive data from 3:02 PM: not inactive.
Sleeper: Questionable.
nflverse: Questionable.

Confidence: MEDIUM
```

Rules:

- source failure ≠ healthy
- `NOT_YET_PUBLISHED` ≠ source failure, but it also ≠ healthy
- `PARTIAL` report ≠ permission to infer active from absence
- stale data must display its timestamp
- official cached data may be used with reduced confidence
- cached “not inactive” evidence is valid only if the cached report was complete for both teams
- an unavailable depth chart removes the opportunity boost but must not prevent ordinary replacement recommendations
- an unresolved depth blocker lowers or suppresses the opportunity signal
- final report should list failed sources


---

# 34. Logging

Use structured logs.

Include:

- timestamp
- league ID
- game ID
- canonical player ID
- source
- report state
- game-day state
- injury designation
- expected/parsed team coverage
- published/source-updated/retrieved timestamps
- HTTP cache age when present
- depth-chart snapshot timestamp
- opportunity level and blocker IDs
- latency
- success/failure
- exception type

Never log:

- `espn_s2`
- `SWID`
- SMTP password
- full authenticated request headers


---

# 35. Project Structure

```text
fantasy-watchdog/
│
├── app/
│   ├── config.py
│   │
│   ├── models/
│   │   ├── league.py
│   │   ├── player.py
│   │   ├── game.py
│   │   ├── status.py
│   │   └── opportunity.py
│   │
│   ├── fantasy/
│   │   ├── base.py
│   │   ├── espn.py
│   │   ├── sleeper.py
│   │   └── manager.py
│   │
│   ├── nfl/
│   │   ├── schedule.py
│   │   ├── identity.py
│   │   ├── depth_chart.py
│   │   └── sources/
│   │       ├── base.py
│   │       ├── nfl_inactives.py
│   │       ├── nfl_injuries.py
│   │       ├── team_sites.py
│   │       ├── sleeper_status.py
│   │       ├── espn_status.py
│   │       └── nflverse.py
│   │
│   ├── analysis/
│   │   ├── availability.py
│   │   ├── league_context.py
│   │   ├── replacements.py
│   │   ├── opportunity.py
│   │   └── confidence.py
│   │
│   ├── notification/
│   │   ├── email.py
│   │   └── templates.py
│   │
│   ├── scheduler/
│   │   ├── planner.py
│   │   └── jobs.py
│   │
│   └── storage/
│       ├── database.py
│       └── cache.py
│
├── tests/
│   ├── fixtures/
│   │   ├── nfl_inactives/
│   │   ├── nfl_injuries/
│   │   ├── team_sites/
│   │   ├── depth_charts/
│   │   ├── espn/
│   │   └── sleeper/
│   │
│   ├── test_espn.py
│   ├── test_sleeper.py
│   ├── test_identity.py
│   ├── test_schedule.py
│   ├── test_multi_league.py
│   ├── test_inactives.py
│   ├── test_injury_reports.py
│   ├── test_availability.py
│   ├── test_source_completeness.py
│   ├── test_depth_charts.py
│   ├── test_opportunity.py
│   ├── test_replacements.py
│   └── test_email.py
│
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```


---

# 36. Fantasy Manager

`fantasy/manager.py` should be the only interface most downstream code needs.

Conceptually:

```python
class FantasyManager:
    def __init__(
        self,
        espn: ESPNFantasyPlatform,
        sleeper: SleeperFantasyPlatform,
    ):
        self.espn = espn
        self.sleeper = sleeper

    def get_all_leagues(self):
        return (
            self.espn.get_leagues()
            + self.sleeper.get_leagues()
        )

    def get_all_rosters(self):
        ...
```

Downstream components should not contain ESPN- or Sleeper-specific branching unless necessary.


---

# 37. Suggested Core Data Classes

```python
@dataclass
class FantasyLeague:
    id: str
    name: str
    nickname: str
    platform: str
    roster_id: str
    roster_rules: dict
    scoring_settings: dict


@dataclass
class FantasyPlayer:
    canonical_player_id: str
    platform_player_id: str
    name: str
    nfl_team: str
    position: str

    league_id: str
    league_name: str
    platform: str

    lineup_slot: str
    is_starter: bool
    is_reserve: bool = False
    is_taxi: bool = False


@dataclass
class RelevantGame:
    game_id: str
    home_team: str
    away_team: str
    kickoff: datetime
    fantasy_players: list[FantasyPlayer]


@dataclass
class SourceResult:
    source: str
    source_url: str | None
    success: bool
    report_state: str
    roster_eligibility: RosterEligibility | None
    game_day_state: GameDayState | None
    injury_designation: InjuryDesignation | None
    detail: str | None
    published_at: datetime | None
    source_updated_at: datetime | None
    retrieved_at: datetime
    http_cache_age_seconds: int | None
    raw_content_hash: str | None


@dataclass
class NFLPlayerStatus:
    canonical_player_id: str
    roster_eligibility: RosterEligibility
    game_day_state: GameDayState
    injury_designation: InjuryDesignation
    confidence: Confidence
    injury_description: str | None
    official_inactive: bool | None
    source_results: list[SourceResult]
    decision_at: datetime


@dataclass
class DepthRelation:
    canonical_player_id: str
    team: str
    position: str
    formation: str
    position_slot: int
    source_rank: int
    players_ahead: list[str]
    snapshot_at: datetime


@dataclass
class DepthOpportunity:
    beneficiary_player_id: str
    unavailable_player_ids: list[str]
    level: OpportunityLevel
    previous_order_in_slot: int
    effective_order_in_slot: int
    promoted_to_first_available: bool
    confidence: Confidence
    depth_chart_as_of: datetime
    status_decision_at: datetime


@dataclass
class LeaguePlayerAlert:
    league_id: str
    league_name: str
    player: FantasyPlayer
    status: NFLPlayerStatus
    severity: FantasyAlertSeverity
    replacement_candidates: list[FantasyPlayer]
    opportunity: DepthOpportunity | None = None
```


---

# 38. End-to-End Game-Day Workflow

```text
START
  │
  ▼
Load configuration
  │
  ▼
Fetch 3 Sleeper leagues
  +
Fetch 1 private ESPN league
  │
  ▼
Normalize rosters
  │
  ▼
Resolve canonical NFL player IDs
  │
  ▼
Deduplicate players
  │
  ▼
Load nflreadpy schedule
  │
  ├─ load latest depth-chart snapshot
  ├─ index same-slot player chains
  └─ add depth blockers for owned players to status scope
  │
  ▼
Map roster players → NFL games
  │
  ▼
Group by exact kickoff
  │
  ├────────────────── T−90-ish
  │                        │
  │                        ▼
  │              fetch official inactives
  │              fetch official injury data
  │              optionally fetch team context
  │              validate report completeness
  │              retry not-yet-published/partial reports
  │              cache snapshot
  │
  ▼
 T−5
  │
  ├─ refresh Sleeper lineups
  ├─ refresh ESPN lineup
  ├─ refresh official inactives
  ├─ refresh NFL injury report
  ├─ optionally refresh relevant team sources
  ├─ consult fallback sources when needed
  ├─ record upstream cache age/freshness
  │
  ▼
Combine source evidence
  │
  ▼
One NFL status per relevant owned player or depth blocker
  │
  ▼
Resolve same-slot depth promotions
  │
  ▼
Map status to all fantasy instances
  │
  ▼
Evaluate starter urgency per league
  │
  ▼
Find league-specific bench replacements
  │
  ▼
Build one consolidated kickoff email
  │
  ▼
Send
  │
  ▼
Persist status snapshot + send result
```


---

# 39. V1 Scope

A strong V1 should implement exactly these capabilities:

1. Load all **3 Sleeper leagues**.
2. Authenticate to and load the **1 private ESPN league**.
3. Normalize all four fantasy rosters.
4. Correctly distinguish starters from bench.
5. Resolve cross-provider player identities.
6. Use nflreadpy for schedule and team/game mapping.
7. Group all relevant players by exact NFL kickoff time.
8. Cache official status information around T−90.
9. Refresh lineups and official statuses at T−5.
10. Use official NFL inactives as the primary game-day authority.
11. Use official NFL injury reports as the second primary source.
12. Optionally use relevant team sources for attributed late context without making them a V1 dependency.
13. Use ESPN/Sleeper/nflverse as fallback/corroborating sources.
14. Deduplicate NFL player checks across leagues.
15. Detect unavailable/risky starters.
16. Find league-specific valid bench replacements.
17. Load the latest depth-chart snapshot and identify same-slot players ahead of every owned skill player.
18. Detect when an owned player is promoted by an official out/inactive decision.
19. Use confirmed same-slot promotion as a bounded replacement-ranking boost.
20. Ensure replacement games have not already started.
21. Send one consolidated email per kickoff window.
22. Include league-specific sections.
23. Include source publication, retrieval, cache-age, and decision timestamps.
24. Validate per-game report completeness before inferring active from absence.
25. Fail safely when sources are unavailable, partial, stale, or not yet published.
26. Keep all credentials out of source control.


---

# 40. Explicit Non-Goals for V1

Do **not** require the first version to:

- automatically change fantasy lineups
- make waiver claims
- optimize full weekly lineups
- use an LLM for injury interpretation
- scrape sports-news sites broadly
- continuously poll every player all day
- implement advanced projection modeling
- claim that a depth promotion guarantees workload or fantasy production
- generate strong opportunity boosts from a same-position/different-slot injury alone
- build a mobile app
- send SMS or Discord messages
- support dozens of fantasy platforms
- run a relational cloud database

These can come later.


---

# 41. V2 / Future Enhancements

Potential later additions:

## Smarter replacements

- ESPN/Sleeper projected points
- nflverse usage data
- opponent strength
- expected targets/carries
- historical production changes following same-slot depth promotions
- recent snap share, route participation, target share, carry share, and red-zone role
- implied team total
- game spread
- PPR-specific scoring
- workload adjustment for injury

## More alert types

- surprise active from IR
- practice-squad elevation
- depth-chart changes between daily snapshots
- snap-count restriction
- weather warnings
- delayed/postponed games
- late fantasy lineup changes
- overlapping lineup-lock issues

## Additional fantasy actions

- waiver candidate suggestions
- free-agent replacements
- weekly lineup audit
- trade impact analysis
- automatic post-game report

## Platform expansion

The platform abstraction should make it possible to later add:

```text
Yahoo
NFL Fantasy
Fleaflicker
CBS
```

without changing the NFL status pipeline.


---

# 42. Testing Requirements

Testing is especially important because web scrapers are brittle.

## Unit tests

Test:

- ESPN authentication parsing
- Sleeper league discovery
- starter detection
- roster normalization
- player-ID resolution
- schedule matching
- timezone conversions
- slot eligibility
- game-lock detection
- status precedence
- report completeness validation
- `NOT_YET_PUBLISHED` vs `PARTIAL` vs `FAILED`
- upstream cache-age handling
- separate game-day state and injury designation
- roster eligibility for IR/PUP/NFI/suspended/exempt players
- latest depth-snapshot selection
- provider-ID depth-chart joins
- same-slot ordering with non-contiguous ranks
- direct promotion vs broad positional opportunity
- multiple unavailable blockers
- unknown blocker behavior
- replacement ranking
- email rendering

## Fixture-based scraper tests

Save representative HTML/JSON fixtures for:

- NFL inactives
- NFL injury reports
- team pages
- timestamped depth-chart snapshots
- ESPN responses
- Sleeper responses

Parser tests should run without internet access.

## Integration tests

Test scenarios such as:

### Scenario A

Player appears in three leagues.

Expected:

- one NFL lookup
- three league interpretations

### Scenario B

Starter becomes officially inactive.

Expected:

- critical alert in leagues where starting
- info-only note where benched

### Scenario C

Official source fails.

Expected:

- cached/fallback data used
- confidence downgraded
- failure shown in email

### Scenario D

Bench alternative's game already started.

Expected:

- candidate excluded

### Scenario E

User changes lineup two minutes before T−5 job.

Expected:

- system attempts a final refresh
- response cache age is captured
- a confirmed origin-fresh response uses the new lineup
- a response that may predate the change is explicitly marked with its freshness limitation

### Scenario F

4:05 and 4:25 games.

Expected:

- separate notification windows

### Scenario G

Inactive article exists but contains only one team for a game.

Expected:

- game report is `PARTIAL`
- an absent player is not marked active
- retry continues through the final check

### Scenario H

Official injury page returns HTTP 200 with no tables.

Expected:

- source is `NOT_YET_PUBLISHED`
- no player is inferred healthy

### Scenario I

Owned bench player becomes first available in the same depth-chart slot after an official inactive decision.

Expected:

- `PROMOTED` opportunity is emitted
- candidate receives a bounded ranking boost when eligible, healthy, and unlocked

### Scenario J

A different receiver slot loses its starter.

Expected:

- candidate in another slot is at most `POSITIONAL_OPPORTUNITY`
- no strong direct-promotion claim is made

### Scenario K

Latest depth chart is missing, stale, or fails identity resolution.

Expected:

- ordinary status and replacement analysis continues
- opportunity boost is suppressed or downgraded
- limitation appears in diagnostics

### Scenario L

Player is on IR/PUP/NFI/suspension and therefore absent from a complete inactive list.

Expected:

- roster eligibility is `INELIGIBLE`
- player is unavailable for fantasy purposes
- absence from the inactive list does not mark the player active


---

# 43. Observability / Health Checks

Add simple operational checks:

- last successful Sleeper fetch
- last successful ESPN fetch
- last successful NFL official scrape
- latest complete official report per relevant game
- latest depth-chart snapshot and age
- unresolved owned-player depth joins
- current source cache ages
- last sent email
- next scheduled kickoff job

Optional health command:

```bash
python -m app.health
```

Example output:

```text
ESPN: OK
Sleeper: OK
NFL inactives scraper: OK
NFL injury scraper: OK
Depth chart: OK — 32 teams — snapshot 7:57 AM ET
Unresolved owned depth joins: 0
SMTP: OK
Next job: Sun 12:55 PM ET
```


---

# 44. Configuration Example

```yaml
season: 2026

timezone: "America/New_York"

alerts:
  prefetch_attempts_minutes_before_kickoff: [95, 75, 15]
  email_minutes_before_kickoff: 5

sleeper:
  username: "worldwideworm"
  user_id: "1024779386450538496"

  leagues:
    - id: "1389694250752425984"
      roster_id: "8"
      nickname: "CFL"

    - id: "1389362487303892992"
      roster_id: "11"
      nickname: "No More Fields"

    - id: "1359974214391582720"
      roster_id: "7"
      nickname: "Cry Dynasty"

espn:
  leagues:
    - id: "987"
      team_id: "1"
      nickname: "Main"

email:
  smtp_host: "smtp.gmail.com"
  smtp_port: 587
  recipient: "YOUR_EMAIL"

watch:
  starters: true
  bench_for_replacements: true
  include_bench_injury_notes: true
  depth_opportunity: true
  broad_position_opportunity: true

depth_chart:
  enabled: true
  max_age_hours: 30
  strong_boost_only_for_same_slot_promotion: true

sources:
  official_team_context_required: false
  record_http_cache_age: true
  final_sleeper_cache_bust: false  # enable only after in-season validation
```


Environment:

```env
ESPN_SWID=...
ESPN_S2=...

SMTP_USER=...
SMTP_APP_PASSWORD=...
```


---

# 45. Recommended Build Order

## Phase 1 — Fantasy ingestion

1. Implement config.
2. Implement Sleeper adapter.
3. Implement ESPN adapter.
4. Normalize leagues/rosters.
5. Verify starters/bench in all four leagues.

Deliverable:

```text
print all leagues
print all starters
print all bench players
```

---

## Phase 2 — Player identity and schedule

1. Add nflreadpy.
2. Build canonical player crosswalk.
3. Map every roster player to NFL team.
4. Map every player to next game.
5. Group by kickoff.

Deliverable:

```text
12:55 alert candidates
4:00 alert candidates
4:20 alert candidates
...
```

---

## Phase 3 — Official status layer

1. Implement NFL inactives source.
2. Implement NFL injury-report source.
3. Implement source precedence.
4. Add cache.
5. Add explicit unknown/failure states.

Deliverable:

```text
canonical status per relevant NFL player
```

---

## Phase 4 — Team/fallback sources

1. Add Sleeper status fallback.
2. Add nflverse injury fallback with unsupported-season handling.
3. Add confidence scoring.
4. Optionally add official team source abstraction and selected adapters.

---

## Phase 5 — Depth opportunity

1. Load and select the latest timestamped depth snapshot.
2. Join owned skill players by GSIS/ESPN ID.
3. Build same-team/same-position/same-slot chains.
4. Add ahead-of-owned-player blockers to status scope.
5. Detect direct promotions and broader positional opportunity.
6. Add stale/missing/unresolved degradation behavior.

Deliverable:

```text
owned player → depth blockers → effective role after official statuses
```

---

## Phase 6 — Fantasy analysis

1. Map NFL status back to leagues.
2. Determine critical/warning/info severity.
3. Parse league roster eligibility.
4. Find valid bench substitutes.
5. Exclude locked games.

---

## Phase 7 — Email

1. Build text email.
2. Optionally add HTML formatting.
3. Add league summaries.
4. Add timestamps.
5. Add source/failure section.
6. Send via SMTP.

---

## Phase 8 — Scheduler and deployment

1. Add game-day planning job.
2. Add T−90 prefetch.
3. Add T−5 final jobs.
4. Add systemd / Lambda deployment.
5. Add health checks and structured logging.

---

# 46. Definition of Done for V1

The project is ready for real use when all of the following are true:

- All four leagues load correctly.
- The private ESPN league has passed an authenticated smoke test using the production endpoint and required views.
- All starters and bench players are represented accurately.
- Actual Sleeper membership comes from `players`, not nominal roster-slot count.
- A player owned in multiple leagues is resolved to one canonical NFL identity.
- Every rostered player's next NFL kickoff is identified correctly.
- The system correctly separates 1:00, 4:05, 4:25, Sunday night, Monday, Thursday, etc.
- T−90 snapshots are cached.
- T−5 checks attempt fantasy-lineup refreshes and retain upstream cache-age/freshness evidence.
- Official NFL inactive status is parsed correctly.
- Official injury designation is parsed correctly.
- Official report completeness is verified per game before absence becomes active.
- Roster-ineligible players are not marked active merely because they are absent from the game inactive list.
- Empty, partial, not-yet-published, failed, and complete reports remain distinguishable.
- Missing/failed official data never becomes “healthy” automatically.
- Starter issues are identified per league.
- Bench replacements obey league eligibility.
- Locked players are excluded from replacements.
- Latest depth snapshot is selected by `dt` and joined to owned players by stable IDs where available.
- Same-slot direct promotions are distinguished from broad same-position opportunity.
- Depth-chart failure never blocks ordinary availability alerts.
- Depth boosts are bounded and never represented as workload guarantees.
- One consolidated email is sent per kickoff window.
- Email contains separate league-specific notes.
- Email clearly distinguishes critical action from warnings and benign information.
- Every status has publication/update, retrieval, decision, and relevant cache-age timestamps plus a confidence/source trail.
- Secrets never appear in logs or source control.
- Core parser and decision logic has fixture-based tests.
- At least one live 2026 game-day rehearsal has verified article discovery, complete inactive parsing, lineup freshness behavior, and email timing before unattended operation.


---

# 47. Guiding Product Philosophy

This tool should behave less like a general fantasy-news feed and more like a **pre-kickoff safety system**.

The ideal user experience is:

1. The user sets lineups normally on ESPN and Sleeper.
2. The system silently watches upcoming NFL kickoff windows.
3. About five minutes before a relevant kickoff, one concise email arrives.
4. If everything is fine, the email confirms that quickly.
5. If a starter is unexpectedly inactive or risky, that fact is unmistakable.
6. The email immediately shows league-specific replacement options that are still usable.
7. The user never needs to manually check four separate fantasy leagues, NFL inactives, team pages, and injury reports at once.

Reliability, freshness, explicit uncertainty, and actionable league-specific context matter more than sophistication.
