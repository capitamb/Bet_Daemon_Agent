# Apuestas Daemon v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the daemon with Sofascore xG, consensus fair_prob for cups/other sports, all-sports auto-discovery, Telegram combinada, and a results feedback loop.

**Architecture:** Football domestic leagues use Sofascore xG → Poisson fair_prob. Cup competitions (CL/EL) and all non-football sports use vig-free consensus across all bookmakers as fair_prob. Results checker reads past edge .md files, queries football-data.org, writes PnL back to frontmatter, and sends a weekly Telegram summary.

**Tech Stack:** Python 3.11+, httpx, APScheduler, FastAPI, PyYAML, scipy (existing). No new dependencies required.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `config.py` | Modify | Add CUP_COMPETITIONS, KELLY_FRACTION, CONSENSUS_MIN_BOOKMAKERS, SOFASCORE_TOURNAMENTS |
| `models.py` | Modify | Add `sport: str = "football"` field to Match |
| `scrapers/xg.py` | Rewrite | Sofascore xG — replaces broken Understat library |
| `scrapers/sports.py` | Create | Auto-discover active non-soccer sports from Odds API |
| `scrapers/odds.py` | Modify | All-bookmaker fetch, `consensus_fair_prob()`, `fetch_sport_events()` |
| `results_checker.py` | Create | Read pending .md edges, query results, write PnL, weekly stats |
| `notifier.py` | Modify | `format_sport_alert()`, `format_combinada_alert()`, `format_results_summary()`, `send_alerts_for_cycle()` |
| `main.py` | Modify | Unified multi-sport pipeline, results_checker jobs, `/stats` endpoint |
| `tests/test_consensus.py` | Create | Unit tests for vig removal and consensus averaging |
| `tests/test_results_checker.py` | Create | Unit tests for frontmatter parsing, market win detection, stats computation |
| `tests/test_combinada.py` | Create | Unit tests for Kelly calc and combinada message format |

---

## Task 1: Config additions

**Files:**
- Modify: `config.py`

- [ ] **Step 1: Add constants to config.py**

Open `config.py` and add after the existing `UNDERSTAT_LEAGUES` block:

```python
# Cup competitions — use consensus fair_prob instead of xG/Poisson
CUP_COMPETITIONS = {"Champions League", "Europa League"}

# Kelly criterion fraction (0.25 = quarter Kelly)
KELLY_FRACTION = 0.25

# Minimum bookmakers required for consensus to be valid
CONSENSUS_MIN_BOOKMAKERS = 3

# Sofascore unique-tournament IDs for domestic leagues
SOFASCORE_TOURNAMENTS = {
    "Premier League": 17,
    "La Liga":        8,
    "Serie A":        23,
    "Bundesliga":     35,
    "Ligue 1":        34,
}
```

- [ ] **Step 2: Verify import works**

```bash
cd ~/apuestas-daemon && ~/.local/bin/uv run python -c "from config import CUP_COMPETITIONS, KELLY_FRACTION, CONSENSUS_MIN_BOOKMAKERS, SOFASCORE_TOURNAMENTS; print('ok', CUP_COMPETITIONS)"
```

Expected: `ok {'Champions League', 'Europa League'}`

- [ ] **Step 3: Commit**

```bash
cd ~/apuestas-daemon && git add config.py && git commit -m "config: add v2 constants (CUP_COMPETITIONS, KELLY_FRACTION, Sofascore IDs)"
```

---

## Task 2: Add sport field to Match model

**Files:**
- Modify: `models.py`

- [ ] **Step 1: Add `sport` field to Match dataclass**

In `models.py`, add `sport: str = "football"` after the `date` field:

```python
@dataclass
class Match:
    home_team: str
    away_team: str
    competition: str
    date: datetime
    sport: str = "football"          # ← add this line
    # Odds (decimal)
    odds_home: Optional[float] = None
    odds_draw: Optional[float] = None
    odds_away: Optional[float] = None
    # xG averages
    home_xg: Optional[TeamXG] = None
    away_xg: Optional[TeamXG] = None
    # Computed fair probabilities
    fair_prob_home: Optional[float] = None
    fair_prob_draw: Optional[float] = None
    fair_prob_away: Optional[float] = None
    # Context
    injuries: str = ""
    line_movement: str = ""
```

- [ ] **Step 2: Verify existing tests still pass**

```bash
cd ~/apuestas-daemon && ~/.local/bin/uv run pytest tests/ -v --tb=short
```

Expected: all existing tests pass (sport field has default, no breaking change).

- [ ] **Step 3: Commit**

```bash
cd ~/apuestas-daemon && git add models.py && git commit -m "models: add sport field to Match (default='football')"
```

---

## Task 3: Rewrite scrapers/xg.py with Sofascore

**Files:**
- Rewrite: `scrapers/xg.py`
- Create: `tests/test_xg.py`

- [ ] **Step 1: Probe Sofascore API to verify field names**

Run this one-off script to check the actual response structure before writing the parser:

```bash
cd ~/apuestas-daemon && ~/.local/bin/uv run python -c "
import asyncio, httpx

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

async def probe():
    async with httpx.AsyncClient(timeout=15, headers=HEADERS) as c:
        # Get current season for Premier League (tid=17)
        r = await c.get('https://api.sofascore.com/api/v1/unique-tournament/17/seasons')
        seasons = r.json().get('seasons', [])
        sid = seasons[0]['id'] if seasons else None
        print('Latest season id:', sid, 'name:', seasons[0].get('year') if seasons else None)
        if sid:
            r2 = await c.get(f'https://api.sofascore.com/api/v1/unique-tournament/17/season/{sid}/standings/total')
            rows = r2.json().get('standings', [{}])[0].get('rows', [])
            if rows:
                print('Sample row keys:', list(rows[0].keys()))
                print('Sample team:', rows[0].get('team', {}).get('name'))
                print('Sample stats keys:', list(rows[0].keys()))

asyncio.run(probe())
"
```

Note the exact field names from the output — you'll need them in step 2.

- [ ] **Step 2: Write failing test**

Create `tests/test_xg.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from models import TeamXG

# We'll mock httpx.AsyncClient responses
MOCK_SEASONS = {"seasons": [{"id": 52186, "year": "24/25"}]}

MOCK_STANDINGS = {
    "standings": [{
        "rows": [
            {
                "team": {"name": "Arsenal"},
                "matches": 38,
                "expectedGoals": 71.4,
                "expectedGoalsAgainst": 40.2,
            },
            {
                "team": {"name": "Manchester City"},
                "matches": 38,
                "expectedGoals": 80.1,
                "expectedGoalsAgainst": 35.0,
            },
        ]
    }]
}

@pytest.mark.asyncio
async def test_get_league_xg_returns_team_xg():
    """get_league_xg returns TeamXG objects with per-game averages."""
    with patch("scrapers.xg._fetch_seasons", return_value=MOCK_SEASONS), \
         patch("scrapers.xg._fetch_standings", return_value=MOCK_STANDINGS):
        from scrapers.xg import get_league_xg
        result = await get_league_xg("Premier League")
        assert "Arsenal" in result
        arsenal = result["Arsenal"]
        assert isinstance(arsenal, TeamXG)
        assert arsenal.xg_scored_avg == round(71.4 / 38, 3)
        assert arsenal.xg_conceded_avg == round(40.2 / 38, 3)
        assert arsenal.games_sampled == 38

@pytest.mark.asyncio
async def test_find_team_xg_fuzzy_match():
    """find_team_xg matches 'Arsenal FC' to 'Arsenal'."""
    with patch("scrapers.xg._fetch_seasons", return_value=MOCK_SEASONS), \
         patch("scrapers.xg._fetch_standings", return_value=MOCK_STANDINGS):
        from scrapers.xg import find_team_xg
        result = await find_team_xg("Arsenal FC", "Premier League")
        assert result is not None
        assert result.team == "Arsenal"

@pytest.mark.asyncio
async def test_find_team_xg_returns_none_for_unknown():
    """find_team_xg returns None when team not found in any league."""
    with patch("scrapers.xg._fetch_seasons", return_value=MOCK_SEASONS), \
         patch("scrapers.xg._fetch_standings", return_value=MOCK_STANDINGS):
        from scrapers.xg import find_team_xg
        result = await find_team_xg("Unknown Team XYZ", "Premier League")
        assert result is None
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd ~/apuestas-daemon && ~/.local/bin/uv run pytest tests/test_xg.py -v --tb=short
```

Expected: FAIL — `ImportError` or `AttributeError` since xg.py doesn't have `_fetch_seasons`/`_fetch_standings` yet.

- [ ] **Step 4: Rewrite scrapers/xg.py**

Replace the entire file content. Note: if step 1 revealed different field names than `expectedGoals`/`expectedGoalsAgainst`/`matches`, use those actual field names here:

```python
"""Fetch team xG stats from Sofascore for top 5 European leagues."""
import httpx
from models import TeamXG
from config import SOFASCORE_TOURNAMENTS

SOFASCORE_BASE = "https://api.sofascore.com/api/v1"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# In-memory caches
_season_cache: dict[int, int] = {}           # tid → season_id
_xg_cache: dict[str, dict[str, TeamXG]] = {} # league_name → {team_name: TeamXG}

def _normalize_team(name: str) -> str:
    return name.lower().replace(" ", "").replace("-", "").replace("fc", "").replace(".", "")

# Thin HTTP helpers — exist only so tests can patch them
async def _fetch_seasons(tid: int) -> dict:
    async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as c:
        r = await c.get(f"{SOFASCORE_BASE}/unique-tournament/{tid}/seasons")
        r.raise_for_status()
        return r.json()

async def _fetch_standings(tid: int, sid: int) -> dict:
    async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as c:
        r = await c.get(f"{SOFASCORE_BASE}/unique-tournament/{tid}/season/{sid}/standings/total")
        r.raise_for_status()
        return r.json()

async def _get_season_id(tid: int) -> int | None:
    if tid in _season_cache:
        return _season_cache[tid]
    try:
        data = await _fetch_seasons(tid)
        seasons = data.get("seasons", [])
        if not seasons:
            return None
        sid = seasons[0]["id"]
        _season_cache[tid] = sid
        return sid
    except Exception as e:
        print(f"[xg] Sofascore seasons fetch failed (tid={tid}): {e}")
        return None

async def get_league_xg(league_name: str) -> dict[str, TeamXG]:
    """Return {team_name: TeamXG} for all teams in a league via Sofascore."""
    if league_name in _xg_cache:
        return _xg_cache[league_name]

    tid = SOFASCORE_TOURNAMENTS.get(league_name)
    if not tid:
        return {}

    sid = await _get_season_id(tid)
    if not sid:
        return {}

    result: dict[str, TeamXG] = {}
    try:
        data = await _fetch_standings(tid, sid)
        rows = data.get("standings", [{}])[0].get("rows", [])
        for row in rows:
            team_name = row.get("team", {}).get("name", "")
            matches = int(row.get("matches", 0) or 0)
            xg_for = float(row.get("expectedGoals", 0) or 0)
            xg_against = float(row.get("expectedGoalsAgainst", 0) or 0)
            if team_name and matches > 0:
                result[team_name] = TeamXG(
                    team=team_name,
                    xg_scored_avg=round(xg_for / matches, 3),
                    xg_conceded_avg=round(xg_against / matches, 3),
                    games_sampled=matches,
                )
    except Exception as e:
        print(f"[xg] Sofascore fetch failed for {league_name}: {e}")

    _xg_cache[league_name] = result
    return result

_TEAM_LEAGUE_FALLBACK: dict[str, str] = {
    "arsenal": "Premier League", "chelsea": "Premier League",
    "liverpool": "Premier League", "manchestercity": "Premier League",
    "manchesterunited": "Premier League", "tottenham": "Premier League",
    "newcastle": "Premier League", "astonvilla": "Premier League",
    "realmadrid": "La Liga", "barcelona": "La Liga",
    "atleticomadrid": "La Liga", "sevilla": "La Liga", "villarreal": "La Liga",
    "juventus": "Serie A", "inter": "Serie A", "acmilan": "Serie A",
    "napoli": "Serie A", "roma": "Serie A", "atalanta": "Serie A",
    "bayernmunich": "Bundesliga", "borussiadortmund": "Bundesliga",
    "bayer04": "Bundesliga", "leipzig": "Bundesliga",
    "parissaintgermain": "Ligue 1", "marseille": "Ligue 1",
    "monaco": "Ligue 1", "lyon": "Ligue 1", "lille": "Ligue 1",
}

async def find_team_xg(team_name: str, competition: str) -> TeamXG | None:
    """Fuzzy-match team across competition league, then fall back to domestic league for cups."""
    norm = _normalize_team(team_name)

    league_xg = await get_league_xg(competition)
    for name, xg in league_xg.items():
        n = _normalize_team(name)
        if norm in n or n in norm:
            return xg

    # Cup fallback — look up domestic league
    domestic = None
    for fragment, league in _TEAM_LEAGUE_FALLBACK.items():
        if fragment in norm or norm in fragment:
            domestic = league
            break

    if domestic and domestic != competition:
        league_xg = await get_league_xg(domestic)
        for name, xg in league_xg.items():
            n = _normalize_team(name)
            if norm in n or n in norm:
                return xg

    return None
```

- [ ] **Step 5: Run tests — expect pass**

```bash
cd ~/apuestas-daemon && ~/.local/bin/uv run pytest tests/test_xg.py -v --tb=short
```

Expected: 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
cd ~/apuestas-daemon && git add scrapers/xg.py tests/test_xg.py && git commit -m "feat: replace Understat with Sofascore for xG (Understat site is broken)"
```

---

## Task 4: Create scrapers/sports.py

**Files:**
- Create: `scrapers/sports.py`
- Create: `tests/test_sports.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_sports.py`:

```python
import pytest
from unittest.mock import patch, AsyncMock

MOCK_SPORTS_RESPONSE = [
    {"key": "soccer_epl", "active": True},
    {"key": "basketball_nba", "active": True},
    {"key": "americanfootball_nfl", "active": True},
    {"key": "soccer_spain_la_liga", "active": True},
    {"key": "tennis_atp", "active": True},
]

@pytest.mark.asyncio
async def test_get_active_sports_excludes_soccer():
    """get_active_sports filters out all soccer_ keys."""
    with patch("scrapers.sports._fetch_sports", return_value=MOCK_SPORTS_RESPONSE):
        from scrapers import sports as sports_mod
        sports_mod._sports_cache.clear()
        result = await sports_mod.get_active_sports()
        assert "soccer_epl" not in result
        assert "soccer_spain_la_liga" not in result
        assert "basketball_nba" in result
        assert "americanfootball_nfl" in result
        assert "tennis_atp" in result

@pytest.mark.asyncio
async def test_get_active_sports_uses_cache():
    """get_active_sports returns cached result on second call."""
    with patch("scrapers.sports._fetch_sports", return_value=MOCK_SPORTS_RESPONSE) as mock_fetch:
        from scrapers import sports as sports_mod
        sports_mod._sports_cache.clear()
        sports_mod._cache_time = None
        await sports_mod.get_active_sports()
        await sports_mod.get_active_sports()
        assert mock_fetch.call_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ~/apuestas-daemon && ~/.local/bin/uv run pytest tests/test_sports.py -v --tb=short
```

Expected: FAIL — `ModuleNotFoundError: No module named 'scrapers.sports'`

- [ ] **Step 3: Create scrapers/sports.py**

```python
"""Auto-discover active non-soccer sports from The Odds API."""
import os
import httpx
from datetime import datetime, timedelta

ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
_CACHE_TTL = timedelta(hours=24)

_sports_cache: list[str] = []
_cache_time: datetime | None = None

async def _fetch_sports() -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{ODDS_API_BASE}/sports", params={"apiKey": ODDS_API_KEY, "all": "false"})
        r.raise_for_status()
        return r.json()

async def get_active_sports(exclude_prefixes: tuple[str, ...] = ("soccer",)) -> list[str]:
    """Return sport keys with active events, excluding soccer (handled by football pipeline)."""
    global _sports_cache, _cache_time

    now = datetime.utcnow()
    if _sports_cache and _cache_time and (now - _cache_time) < _CACHE_TTL:
        return _sports_cache

    if not ODDS_API_KEY:
        return []

    try:
        sports = await _fetch_sports()
        _sports_cache = [
            s["key"] for s in sports
            if not any(s["key"].startswith(p) for p in exclude_prefixes)
        ]
        _cache_time = now
        print(f"[sports] {len(_sports_cache)} active non-soccer sports discovered")
    except Exception as e:
        print(f"[sports] Discovery failed: {e}")

    return _sports_cache
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd ~/apuestas-daemon && ~/.local/bin/uv run pytest tests/test_sports.py -v --tb=short
```

Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/apuestas-daemon && git add scrapers/sports.py tests/test_sports.py && git commit -m "feat: add sports.py for auto-discovery of active non-soccer sports"
```

---

## Task 5: Update scrapers/odds.py with consensus + multi-sport

**Files:**
- Modify: `scrapers/odds.py`
- Create: `tests/test_consensus.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_consensus.py`:

```python
import pytest
from datetime import datetime, timezone

def test_consensus_fair_prob_removes_vig():
    """consensus_fair_prob averages vig-free probs across bookmakers."""
    from scrapers.odds import consensus_fair_prob

    # Two bookmakers with vig baked in
    bookmakers = [
        {"markets": [{"key": "h2h", "outcomes": [
            {"name": "Home", "price": 2.10},
            {"name": "Draw", "price": 3.40},
            {"name": "Away", "price": 3.20},
        ]}]},
        {"markets": [{"key": "h2h", "outcomes": [
            {"name": "Home", "price": 2.00},
            {"name": "Draw", "price": 3.50},
            {"name": "Away", "price": 3.30},
        ]}]},
    ]
    event = {"home_team": "Home", "away_team": "Away", "bookmakers": bookmakers}
    ph, pd, pa = consensus_fair_prob(event)

    # Probabilities must sum to ~1.0
    assert abs(ph + pd + pa - 1.0) < 0.001
    # Home should be most likely
    assert ph > pa
    assert ph > pd

def test_consensus_fair_prob_returns_none_below_min_bookmakers():
    """consensus_fair_prob returns None when fewer than CONSENSUS_MIN_BOOKMAKERS."""
    from scrapers.odds import consensus_fair_prob

    bookmakers = [
        {"markets": [{"key": "h2h", "outcomes": [
            {"name": "Home", "price": 1.90},
            {"name": "Away", "price": 1.90},
        ]}]},
    ]
    event = {"home_team": "Home", "away_team": "Away", "bookmakers": bookmakers}
    result = consensus_fair_prob(event)
    assert result is None

def test_consensus_no_draw_sport():
    """consensus_fair_prob handles events with no Draw outcome (NBA, etc.)."""
    from scrapers.odds import consensus_fair_prob

    bookmakers = [
        {"markets": [{"key": "h2h", "outcomes": [
            {"name": "Lakers", "price": 1.85},
            {"name": "Celtics", "price": 1.95},
        ]}]},
        {"markets": [{"key": "h2h", "outcomes": [
            {"name": "Lakers", "price": 1.82},
            {"name": "Celtics", "price": 2.00},
        ]}]},
        {"markets": [{"key": "h2h", "outcomes": [
            {"name": "Lakers", "price": 1.87},
            {"name": "Celtics", "price": 1.93},
        ]}]},
    ]
    event = {"home_team": "Lakers", "away_team": "Celtics", "bookmakers": bookmakers}
    result = consensus_fair_prob(event)
    assert result is not None
    ph, pd, pa = result
    assert pd is None   # no draw
    assert abs(ph + pa - 1.0) < 0.001
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ~/apuestas-daemon && ~/.local/bin/uv run pytest tests/test_consensus.py -v --tb=short
```

Expected: FAIL — `ImportError: cannot import name 'consensus_fair_prob'`

- [ ] **Step 3: Rewrite scrapers/odds.py**

Replace the full file:

```python
"""Fetch odds from The Odds API. Provides Pinnacle edge odds + consensus fair_prob."""
import os
import httpx
from datetime import datetime, timezone
from models import Match
from config import ODDS_API_FOOTBALL_SPORTS, CONSENSUS_MIN_BOOKMAKERS

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY", "")

def _normalize(name: str) -> str:
    return name.lower().replace(" ", "").replace("-", "").replace("fc", "").replace(".", "")

def _match_team(api_name: str, fixture_name: str) -> bool:
    return _normalize(api_name) in _normalize(fixture_name) or \
           _normalize(fixture_name) in _normalize(api_name)

def consensus_fair_prob(event: dict) -> tuple[float, float | None, float] | None:
    """
    Compute vig-free consensus fair probabilities from all bookmakers in an event.
    Returns (p_home, p_draw_or_None, p_away), or None if fewer than CONSENSUS_MIN_BOOKMAKERS.
    p_draw is None for sports without a draw market.
    """
    home_team = event["home_team"]
    away_team = event["away_team"]
    bookmakers = event.get("bookmakers", [])

    home_fairs, draw_fairs, away_fairs = [], [], []

    for bk in bookmakers:
        for market in bk.get("markets", []):
            if market["key"] != "h2h":
                continue
            outcomes = {o["name"]: o["price"] for o in market["outcomes"]}
            home_p = 1 / outcomes[home_team] if home_team in outcomes else None
            away_p = 1 / outcomes[away_team] if away_team in outcomes else None
            draw_p = 1 / outcomes["Draw"] if "Draw" in outcomes else None

            if home_p is None or away_p is None:
                continue

            total = home_p + away_p + (draw_p or 0)
            home_fairs.append(home_p / total)
            away_fairs.append(away_p / total)
            if draw_p is not None:
                draw_fairs.append(draw_p / total)

    if len(home_fairs) < CONSENSUS_MIN_BOOKMAKERS:
        return None

    ph = sum(home_fairs) / len(home_fairs)
    pa = sum(away_fairs) / len(away_fairs)
    pd = sum(draw_fairs) / len(draw_fairs) if draw_fairs else None

    # Re-normalize
    total = ph + pa + (pd or 0)
    return round(ph / total, 4), round(pd / total, 4) if pd else None, round(pa / total, 4)

def _pinnacle_odds(event: dict) -> tuple[float | None, float | None, float | None]:
    """Extract Pinnacle h2h odds from event. Returns (home, draw, away)."""
    home_team = event["home_team"]
    away_team = event["away_team"]
    for bk in event.get("bookmakers", []):
        if bk["key"] != "pinnacle":
            continue
        for market in bk.get("markets", []):
            if market["key"] == "h2h":
                outcomes = {o["name"]: o["price"] for o in market["outcomes"]}
                return (
                    outcomes.get(home_team),
                    outcomes.get("Draw"),
                    outcomes.get(away_team),
                )
    return None, None, None

async def _fetch_sport_odds(sport_key: str) -> list[dict]:
    """Fetch all bookmaker h2h odds for a sport key."""
    if not ODDS_API_KEY:
        return []
    async with httpx.AsyncClient(timeout=15) as c:
        try:
            r = await c.get(
                f"{ODDS_API_BASE}/sports/{sport_key}/odds",
                params={
                    "apiKey": ODDS_API_KEY,
                    "regions": "eu,us",
                    "markets": "h2h",
                    "oddsFormat": "decimal",
                },
            )
            if r.status_code != 200:
                return []
            return r.json()
        except httpx.RequestError:
            return []

async def enrich_matches_with_odds(matches: list[Match]) -> list[Match]:
    """
    Attach Pinnacle h2h odds to football fixtures.
    Also sets consensus fair_prob for cup competitions.
    """
    if not ODDS_API_KEY:
        print("[odds] THE_ODDS_API_KEY not set — skipping odds fetch")
        return matches

    from config import CUP_COMPETITIONS

    # Fetch all bookmakers (not just Pinnacle) for consensus capability
    all_events: list[dict] = []
    async with httpx.AsyncClient(timeout=15) as c:
        for sport_key in ODDS_API_FOOTBALL_SPORTS:
            try:
                r = await c.get(
                    f"{ODDS_API_BASE}/sports/{sport_key}/odds",
                    params={
                        "apiKey": ODDS_API_KEY,
                        "regions": "eu,us",
                        "markets": "h2h",
                        "oddsFormat": "decimal",
                    },
                )
                if r.status_code == 200:
                    all_events.extend(r.json())
            except httpx.RequestError:
                continue

    for match in matches:
        for event in all_events:
            home_api = event["home_team"]
            away_api = event["away_team"]
            if not (_match_team(home_api, match.home_team) and _match_team(away_api, match.away_team)):
                continue

            # Always attach Pinnacle odds for edge comparison
            oh, od, oa = _pinnacle_odds(event)
            match.odds_home = oh
            match.odds_draw = od
            match.odds_away = oa

            # For cup competitions, also set consensus fair_prob
            if match.competition in CUP_COMPETITIONS:
                result = consensus_fair_prob(event)
                if result:
                    match.fair_prob_home, match.fair_prob_draw, match.fair_prob_away = result
            break

    return matches

async def fetch_sport_events(sport_key: str) -> list[Match]:
    """
    Fetch events for a non-football sport. Returns Match objects with
    Pinnacle odds and consensus fair_prob already set.
    """
    events = await _fetch_sport_odds(sport_key)
    matches: list[Match] = []

    for event in events:
        result = consensus_fair_prob(event)
        if result is None:
            continue

        oh, od, oa = _pinnacle_odds(event)
        if oh is None and oa is None:
            continue  # need at least Pinnacle odds for edge comparison

        try:
            date = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue

        ph, pd, pa = result
        match = Match(
            home_team=event["home_team"],
            away_team=event["away_team"],
            competition=event.get("sport_title", sport_key),
            date=date,
            sport=sport_key,
            odds_home=oh,
            odds_draw=od,
            odds_away=oa,
            fair_prob_home=ph,
            fair_prob_draw=pd,
            fair_prob_away=pa,
        )
        matches.append(match)

    return matches
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd ~/apuestas-daemon && ~/.local/bin/uv run pytest tests/test_consensus.py -v --tb=short
```

Expected: 3 tests PASS.

- [ ] **Step 5: Run all tests to catch regressions**

```bash
cd ~/apuestas-daemon && ~/.local/bin/uv run pytest tests/ -v --tb=short
```

Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
cd ~/apuestas-daemon && git add scrapers/odds.py tests/test_consensus.py && git commit -m "feat: add consensus_fair_prob and multi-sport fetch to odds.py"
```

---

## Task 6: Create results_checker.py

**Files:**
- Create: `results_checker.py`
- Create: `tests/test_results_checker.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_results_checker.py`:

```python
import pytest
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone

def _write_md(path: Path, frontmatter: dict) -> None:
    import yaml
    content = "---\n" + yaml.dump(frontmatter, allow_unicode=True) + "---\n"
    path.write_text(content, encoding="utf-8")

def test_did_market_win_home():
    from results_checker import _did_market_win
    assert _did_market_win("2-1", "Arsenal ML (home)") is True
    assert _did_market_win("1-2", "Arsenal ML (home)") is False
    assert _did_market_win("1-1", "Arsenal ML (home)") is False

def test_did_market_win_draw():
    from results_checker import _did_market_win
    assert _did_market_win("1-1", "Empate (draw)") is True
    assert _did_market_win("2-1", "Empate (draw)") is False

def test_did_market_win_away():
    from results_checker import _did_market_win
    assert _did_market_win("0-3", "PSG ML (away)") is True
    assert _did_market_win("1-0", "PSG ML (away)") is False

def test_compute_weekly_stats_empty_dir():
    from results_checker import compute_weekly_stats
    with tempfile.TemporaryDirectory() as d:
        stats = compute_weekly_stats(Path(d))
    assert stats == {}

def test_compute_weekly_stats_with_results():
    from results_checker import compute_weekly_stats
    with tempfile.TemporaryDirectory() as d:
        data_dir = Path(d)
        now = datetime.now(timezone.utc)
        # Won edge
        _write_md(data_dir / "edge1.md", {
            "match": "Arsenal FC vs PSG",
            "date": (now - timedelta(hours=10)).strftime("%Y-%m-%d %H:%M"),
            "market": "Arsenal ML (home)",
            "odds_current": 2.10,
            "fair_prob": 0.55,
            "won": True,
            "pnl_units": 1.10,
        })
        # Lost edge
        _write_md(data_dir / "edge2.md", {
            "match": "Arsenal FC vs PSG",
            "date": (now - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M"),
            "market": "Empate (draw)",
            "odds_current": 3.40,
            "fair_prob": 0.35,
            "won": False,
            "pnl_units": -1.0,
        })
        stats = compute_weekly_stats(data_dir)

    assert stats["total_edges"] == 2
    assert stats["won"] == 1
    assert stats["lost"] == 1
    assert abs(stats["pnl_units"] - 0.10) < 0.01
    assert stats["hit_rate"] == 0.5
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ~/apuestas-daemon && ~/.local/bin/uv run pytest tests/test_results_checker.py -v --tb=short
```

Expected: FAIL — `ModuleNotFoundError: No module named 'results_checker'`

- [ ] **Step 3: Create results_checker.py**

```python
"""
Check results of past edge alerts, write PnL back to frontmatter,
and compute weekly calibration stats.
"""
import os
import httpx
import yaml
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any

FDAPI_BASE = "https://api.football-data.org/v4"
FDAPI_KEY = os.getenv("FOOTBALL_DATA_API_KEY", "")

# ─── Frontmatter helpers ────────────────────────────────────────────────────

def _read_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    try:
        end = text.index("---", 3)
        return yaml.safe_load(text[3:end]) or {}
    except (ValueError, yaml.YAMLError):
        return {}

def _write_frontmatter(path: Path, data: dict) -> None:
    text = path.read_text(encoding="utf-8")
    body = ""
    if text.startswith("---"):
        try:
            end = text.index("---", 3)
            body = text[end + 3:]
        except ValueError:
            pass
    path.write_text(
        "---\n" + yaml.dump(data, allow_unicode=True, default_flow_style=False) + "---\n" + body,
        encoding="utf-8",
    )

# ─── Result lookup ──────────────────────────────────────────────────────────

def _did_market_win(result_score: str, market_label: str) -> bool:
    """Given 'home_score-away_score' and a market label, return True if the bet won."""
    try:
        h, a = map(int, result_score.split("-"))
    except ValueError:
        return False
    label = market_label.lower()
    if "home" in label:
        return h > a
    if "draw" in label or "empate" in label:
        return h == a
    if "away" in label:
        return a > h
    return False

async def _fetch_match_result(match_name: str, match_date: datetime) -> str | None:
    """
    Query football-data.org for a finished match score.
    Returns 'home-away' score string or None if not found.
    """
    if not FDAPI_KEY:
        return None
    date_str = match_date.strftime("%Y-%m-%d")
    parts = match_name.split(" vs ")
    if len(parts) != 2:
        return None
    home_frag = parts[0].strip().lower()[:6]
    away_frag = parts[1].strip().lower()[:6]

    async with httpx.AsyncClient(timeout=15) as c:
        try:
            r = await c.get(
                f"{FDAPI_BASE}/matches",
                headers={"X-Auth-Token": FDAPI_KEY},
                params={"dateFrom": date_str, "dateTo": date_str, "status": "FINISHED"},
            )
            if r.status_code != 200:
                return None
            for m in r.json().get("matches", []):
                api_home = m.get("homeTeam", {}).get("name", "").lower()
                api_away = m.get("awayTeam", {}).get("name", "").lower()
                if home_frag in api_home and away_frag in api_away:
                    score = m.get("score", {}).get("fullTime", {})
                    h, a = score.get("home"), score.get("away")
                    if h is not None and a is not None:
                        return f"{h}-{a}"
        except httpx.RequestError:
            pass
    return None

# ─── Main checker ───────────────────────────────────────────────────────────

async def check_pending_results(data_dir: Path) -> list[dict]:
    """
    Scan data_dir for unchecked edge .md files whose match has ended.
    Updates frontmatter with won/pnl_units/result_score/checked_at.
    Returns list of newly resolved results.
    """
    if not data_dir.exists():
        return []

    now = datetime.now(timezone.utc)
    resolved = []

    for md_path in data_dir.glob("*.md"):
        fm = _read_frontmatter(md_path)
        if not fm or fm.get("won") is not None:
            continue  # already checked or unreadable

        date_str = fm.get("date", "")
        try:
            match_date = datetime.strptime(date_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if now - match_date < timedelta(hours=2):
            continue  # match likely still in progress

        score = await _fetch_match_result(fm.get("match", ""), match_date)
        if score is None:
            continue  # not yet available, retry next cycle

        won = _did_market_win(score, fm.get("market", ""))
        odds = fm.get("odds_current", 2.0)
        pnl = round(float(odds) - 1, 4) if won else -1.0

        fm["won"] = won
        fm["result_score"] = score
        fm["pnl_units"] = pnl
        fm["checked_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        _write_frontmatter(md_path, fm)

        resolved.append({"match": fm.get("match"), "market": fm.get("market"), "won": won, "pnl_units": pnl})
        print(f"[results] {fm.get('match')} — {fm.get('market')} → {'WIN' if won else 'LOSS'} {pnl:+.2f}u")

    return resolved

# ─── Weekly stats ───────────────────────────────────────────────────────────

def compute_weekly_stats(data_dir: Path, days: int = 7) -> dict:
    """Aggregate PnL stats for checked edges in the last N days."""
    if not data_dir.exists():
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    total = won_count = 0
    pnl = 0.0
    fair_probs: list[float] = []

    for md_path in data_dir.glob("*.md"):
        fm = _read_frontmatter(md_path)
        if fm.get("won") is None:
            continue
        try:
            match_date = datetime.strptime(fm.get("date", ""), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if match_date < cutoff:
            continue

        total += 1
        if fm.get("won"):
            won_count += 1
        pnl += fm.get("pnl_units", -1.0)
        if fp := fm.get("fair_prob"):
            fair_probs.append(float(fp))

    if total == 0:
        return {}

    hit_rate = round(won_count / total, 4)
    avg_fair = round(sum(fair_probs) / len(fair_probs), 4) if fair_probs else 0.0

    return {
        "period_days": days,
        "total_edges": total,
        "won": won_count,
        "lost": total - won_count,
        "hit_rate": hit_rate,
        "pnl_units": round(pnl, 4),
        "avg_fair_prob": avg_fair,
        "model_gap": round(avg_fair - hit_rate, 4),
    }
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd ~/apuestas-daemon && ~/.local/bin/uv run pytest tests/test_results_checker.py -v --tb=short
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/apuestas-daemon && git add results_checker.py tests/test_results_checker.py && git commit -m "feat: add results_checker with PnL feedback loop and weekly stats"
```

---

## Task 7: Update notifier.py

**Files:**
- Modify: `notifier.py`
- Create: `tests/test_combinada.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_combinada.py`:

```python
import pytest
from datetime import datetime, timezone
from models import Match, EdgeResult

def _make_edge(odds: float, fair_prob: float, market: str = "home") -> tuple[Match, EdgeResult]:
    match = Match(
        home_team="Arsenal", away_team="PSG",
        competition="Champions League",
        date=datetime(2026, 5, 30, 16, 0, tzinfo=timezone.utc),
    )
    edge = EdgeResult(
        market=market, odds=odds, implied_prob=round(1/odds, 4),
        fair_prob=fair_prob, edge=round(fair_prob - 1/odds, 4),
        signal="media", qualifies=True,
    )
    return match, edge

def test_format_combinada_contains_legs():
    from notifier import format_combinada_alert
    legs = [_make_edge(2.10, 0.55), _make_edge(1.85, 0.60)]
    msg = format_combinada_alert(legs)
    assert "COMBINADA" in msg
    assert "2 legs" in msg
    assert "Leg 1" in msg
    assert "Leg 2" in msg

def test_format_combinada_kelly_calculation():
    from notifier import format_combinada_alert
    # Single leg with known values
    legs = [_make_edge(2.00, 0.60), _make_edge(2.00, 0.60)]
    msg = format_combinada_alert(legs)
    # odds_combinada = 4.00, p_win = 0.36
    # kelly_full = (4.0 * 0.36 - 1) / (4.0 - 1) = 0.44 / 3.0 = 0.1467
    # kelly_stake = 0.1467 * 0.25 = 0.0367 → ~3.7%
    assert "%" in msg
    assert "Odd total: 4.00" in msg

def test_format_results_summary():
    from notifier import format_results_summary
    stats = {
        "period_days": 7,
        "total_edges": 10,
        "won": 6,
        "lost": 4,
        "hit_rate": 0.60,
        "pnl_units": 2.5,
        "avg_fair_prob": 0.58,
        "model_gap": -0.02,
    }
    msg = format_results_summary(stats)
    assert "RESUMEN SEMANAL" in msg
    assert "60.0%" in msg
    assert "+2.50u" in msg
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ~/apuestas-daemon && ~/.local/bin/uv run pytest tests/test_combinada.py -v --tb=short
```

Expected: FAIL — `ImportError` for `format_combinada_alert`.

- [ ] **Step 3: Update notifier.py**

Replace the full file:

```python
import os
import httpx
from models import Match, EdgeResult
from config import KELLY_FRACTION

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SIGNAL_EMOJI = {"baja": "🟡", "media": "🟠", "alta": "🔴"}

_SPORT_EMOJI: dict[str, str] = {
    "football": "⚽",
    "basketball": "🏀",
    "americanfootball": "🏈",
    "baseball": "⚾",
    "tennis": "🎾",
    "icehockey": "🏒",
    "soccer": "⚽",
}

def _sport_emoji(sport: str) -> str:
    for prefix, emoji in _SPORT_EMOJI.items():
        if sport.startswith(prefix):
            return emoji
    return "🎯"

def format_sport_alert(match: Match, edge: EdgeResult) -> str:
    market_label = {
        "home": f"{match.home_team} ML",
        "draw": "Empate",
        "away": f"{match.away_team} ML",
    }[edge.market]

    emoji = _sport_emoji(match.sport)
    lines = [
        "🚨 EDGE DETECTADO",
        f"{emoji} {match.competition}",
        f"{match.home_team} vs {match.away_team}",
        f"📊 {market_label} @ {edge.odds:.2f}",
        f"Edge: +{edge.edge:.1%} | Signal: {edge.signal.upper()} {SIGNAL_EMOJI[edge.signal]}",
        f"Fair: {edge.fair_prob:.1%} | Impl: {edge.implied_prob:.1%}",
    ]
    if match.injuries:
        lines.append(f"🏥 {match.injuries}")
    lines.append("→ Pide análisis completo a Claude")
    return "\n".join(lines)

# Keep old name for backwards compatibility with vault_writer usage
format_football_alert = format_sport_alert

def format_combinada_alert(edges_with_matches: list[tuple[Match, EdgeResult]]) -> str:
    n = len(edges_with_matches)
    lines = [f"🎯 COMBINADA — {n} legs"]

    odds_product = 1.0
    p_win = 1.0

    for i, (match, edge) in enumerate(edges_with_matches, 1):
        market_label = {
            "home": f"{match.home_team} ML",
            "draw": "Empate",
            "away": f"{match.away_team} ML",
        }[edge.market]
        lines.append(f"Leg {i}: {market_label} @ {edge.odds:.2f} ({match.home_team} vs {match.away_team} — {match.competition})")
        odds_product *= edge.odds
        p_win *= edge.fair_prob

    if odds_product > 1:
        kelly_full = (odds_product * p_win - 1) / (odds_product - 1)
        kelly_stake = max(0.0, kelly_full * KELLY_FRACTION)
    else:
        kelly_stake = 0.0

    lines.append(f"Odd total: {odds_product:.2f} | Kelly 25%: {kelly_stake:.1%} bankroll")
    lines.append(f"Fair p_win: {p_win:.1%}")
    return "\n".join(lines)

def format_results_summary(stats: dict) -> str:
    lines = [
        "📊 RESUMEN SEMANAL — Apuestas Daemon",
        f"Período: últimos {stats.get('period_days', 7)} días",
        f"Edges: {stats.get('total_edges', 0)} | Ganados: {stats.get('won', 0)} | Perdidos: {stats.get('lost', 0)}",
        f"Hit rate: {stats.get('hit_rate', 0):.1%} | PnL: {stats.get('pnl_units', 0):+.2f}u",
        f"Fair prob prom: {stats.get('avg_fair_prob', 0):.1%} | Gap modelo: {stats.get('model_gap', 0):+.1%}",
    ]
    gap = stats.get("model_gap", 0)
    if abs(gap) > 0.05:
        direction = "sobreestimando" if gap > 0 else "subestimando"
        lines.append(f"⚠️ Gap significativo — {direction} edge real")
    return "\n".join(lines)

async def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[ALERT — Telegram not configured]\n{message}")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            r = await c.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message})
            return r.status_code == 200
        except httpx.RequestError:
            return False

async def send_alerts_for_cycle(edges_with_matches: list[tuple[Match, EdgeResult]]) -> None:
    """Send one individual alert per edge, then a combinada if 2+ edges."""
    for match, edge in edges_with_matches:
        await send_telegram(format_sport_alert(match, edge))
    if len(edges_with_matches) >= 2:
        await send_telegram(format_combinada_alert(edges_with_matches))
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd ~/apuestas-daemon && ~/.local/bin/uv run pytest tests/test_combinada.py tests/test_notifier.py -v --tb=short
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/apuestas-daemon && git add notifier.py tests/test_combinada.py && git commit -m "feat: add combinada, results summary, and multi-sport alerts to notifier"
```

---

## Task 8: Update main.py — unified multi-sport pipeline

**Files:**
- Modify: `main.py`

This task has no new unit tests — the pipeline integration is verified by running the daemon and checking logs.

- [ ] **Step 1: Rewrite main.py**

Replace the full file:

```python
"""
Apuestas Daemon — main entry point.
FastAPI + APScheduler. Runs football + multi-sport pipeline every 15 minutes.
Exposes local API on localhost:8001.
"""
import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

from config import (
    FOOTBALL_CONFIG, SCHEDULE_INTERVAL_MINUTES, DAEMON_PORT,
    CUP_COMPETITIONS,
)
from edge_detector import detect_edges
from models import Match, EdgeResult
from notifier import send_alerts_for_cycle, format_results_summary, send_telegram
from poisson_model import fair_probs_from_xg
from scrapers.fixtures import fetch_upcoming_fixtures
from scrapers.odds import enrich_matches_with_odds, fetch_sport_events
from scrapers.sports import get_active_sports
from scrapers.xg import find_team_xg
from results_checker import check_pending_results, compute_weekly_stats
from vault_writer import write_edge_alert

# ─── State ──────────────────────────────────────────────────────────────────
state = {
    "last_run": None,
    "edges_today": [],
    "status": "starting",
}

# ─── Football pipeline ───────────────────────────────────────────────────────
async def run_football_pipeline() -> list[tuple[Match, EdgeResult]]:
    print(f"[{datetime.now():%H:%M:%S}] Running football pipeline...")
    state["status"] = "running"

    matches = await fetch_upcoming_fixtures(hours_ahead=120)
    print(f"  -> {len(matches)} upcoming fixtures")

    matches = await enrich_matches_with_odds(matches)
    with_odds = [m for m in matches if m.odds_home]
    print(f"  -> {len(with_odds)} have odds")

    # xG / Poisson for domestic leagues; consensus already set for cups
    for match in with_odds:
        if match.competition in CUP_COMPETITIONS:
            continue  # consensus fair_prob already set in enrich_matches_with_odds
        home_xg = await find_team_xg(match.home_team, match.competition)
        away_xg = await find_team_xg(match.away_team, match.competition)
        if home_xg and away_xg:
            ph, pd, pa = fair_probs_from_xg(home_xg, away_xg)
            match.fair_prob_home = ph
            match.fair_prob_draw = pd
            match.fair_prob_away = pa

    with_probs = [m for m in with_odds if m.fair_prob_home]
    print(f"  -> {len(with_probs)} have fair_prob")

    cfg = FOOTBALL_CONFIG
    qualifying: list[tuple[Match, EdgeResult]] = []
    for match in with_probs:
        for edge in detect_edges(match, min_edge=cfg.min_edge, min_signal=cfg.min_signal):
            if not edge.qualifies:
                continue
            write_edge_alert(match, edge, data_dir=cfg.data_dir)
            print(f"  [EDGE] {match.home_team} vs {match.away_team} — {edge.market} edge={edge.edge:.1%}")
            qualifying.append((match, edge))

    return qualifying

# ─── Other sports pipeline ───────────────────────────────────────────────────
async def run_other_sports_pipeline() -> list[tuple[Match, EdgeResult]]:
    sport_keys = await get_active_sports()
    if not sport_keys:
        return []

    print(f"[{datetime.now():%H:%M:%S}] Running other-sports pipeline ({len(sport_keys)} sports)...")
    cfg = FOOTBALL_CONFIG  # reuse same edge thresholds
    qualifying: list[tuple[Match, EdgeResult]] = []

    for sport_key in sport_keys:
        events = await fetch_sport_events(sport_key)
        for match in events:
            for edge in detect_edges(match, min_edge=cfg.min_edge, min_signal=cfg.min_signal):
                if not edge.qualifies:
                    continue
                print(f"  [EDGE] {sport_key}: {match.home_team} vs {match.away_team} — {edge.market} edge={edge.edge:.1%}")
                qualifying.append((match, edge))

    return qualifying

# ─── Combined cycle ──────────────────────────────────────────────────────────
async def run_full_pipeline() -> None:
    try:
        football_edges = await run_football_pipeline()
        other_edges = await run_other_sports_pipeline()
        all_edges = football_edges + other_edges

        if all_edges:
            await send_alerts_for_cycle(all_edges)

        state["edges_today"].extend([
            {"match": f"{m.home_team} vs {m.away_team}", "sport": m.sport,
             "market": e.market, "edge": e.edge, "odds": e.odds}
            for m, e in all_edges
        ])
        state["last_run"] = datetime.now().isoformat()
        state["status"] = "idle"
        print(f"  [OK] Cycle complete. {len(all_edges)} new edges.")
    except Exception as exc:
        state["status"] = "error"
        print(f"  [ERROR] Pipeline error: {exc}")
        raise

# ─── Results checker jobs ────────────────────────────────────────────────────
async def run_results_check() -> None:
    cfg = FOOTBALL_CONFIG
    resolved = await check_pending_results(cfg.data_dir)
    if resolved:
        print(f"[results] Resolved {len(resolved)} pending edges")

async def run_weekly_summary() -> None:
    cfg = FOOTBALL_CONFIG
    stats = compute_weekly_stats(cfg.data_dir)
    if stats:
        await send_telegram(format_results_summary(stats))

# ─── FastAPI app ─────────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(run_full_pipeline())
    scheduler.add_job(run_full_pipeline, "interval", minutes=SCHEDULE_INTERVAL_MINUTES, id="full_pipeline")
    scheduler.add_job(run_results_check, "interval", hours=6, id="results_check")
    scheduler.add_job(run_weekly_summary, "cron", day_of_week="mon", hour=9, minute=0, id="weekly_summary")
    scheduler.start()
    yield
    scheduler.shutdown()

app = FastAPI(title="Apuestas Daemon", lifespan=lifespan)

@app.get("/health")
def health():
    return {"status": "ok", "daemon_status": state["status"]}

@app.get("/status")
def status():
    return {
        "last_run": state["last_run"],
        "status": state["status"],
        "edges_today_count": len(state["edges_today"]),
        "edges_today": state["edges_today"],
        "schedule_interval_minutes": SCHEDULE_INTERVAL_MINUTES,
    }

@app.post("/run-now")
async def run_now():
    """Trigger pipeline immediately."""
    asyncio.create_task(run_full_pipeline())
    return {"message": "pipeline triggered"}

@app.get("/stats")
def stats():
    """Return accumulated calibration stats from checked edge files."""
    cfg = FOOTBALL_CONFIG
    return compute_weekly_stats(cfg.data_dir, days=30)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=DAEMON_PORT, reload=False)
```

- [ ] **Step 2: Restart daemon and verify startup**

```bash
cd ~/apuestas-daemon && find . -name "*.pyc" -delete 2>/dev/null; pm2 restart ecosystem.config.cjs && sleep 25 && tail -20 ~/apuestas-daemon/logs/out.log
```

Expected output (order may vary):
```
[HH:MM:SS] Running football pipeline...
  -> 1 upcoming fixtures
  -> 1 have odds
  -> 1 have fair_prob        ← consensus from CL
  [EDGE] Paris Saint-Germain FC vs Arsenal FC — ...
[HH:MM:SS] Running other-sports pipeline (N sports)...
[OK] Cycle complete. N new edges.
```

- [ ] **Step 3: Verify Telegram received alerts**

Check your Telegram for individual edge alerts and (if 2+ edges) a combinada message.

- [ ] **Step 4: Verify /stats endpoint**

```bash
curl -s http://localhost:8001/stats
```

Expected: `{}` (no checked results yet) or a stats object if past edges exist.

- [ ] **Step 5: Run full test suite**

```bash
cd ~/apuestas-daemon && ~/.local/bin/uv run pytest tests/ -v --tb=short
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
cd ~/apuestas-daemon && git add main.py && git commit -m "feat: unified multi-sport pipeline, results_checker jobs, /stats endpoint"
```

---

## Task 9: Sofascore field name fix (conditional)

> **Only run this task if Task 3 Step 1 revealed that the Sofascore response uses different field names than `expectedGoals`, `expectedGoalsAgainst`, `matches`.**

- [ ] **Step 1: Identify actual field names from probe output in Task 3 Step 1**

Map the actual field names and update `scrapers/xg.py` lines that reference:
- `row.get("matches", 0)` → replace `"matches"` with actual key
- `row.get("expectedGoals", 0)` → replace with actual xG key
- `row.get("expectedGoalsAgainst", 0)` → replace with actual xGA key

- [ ] **Step 2: Update mock data in test_xg.py**

Update `MOCK_STANDINGS` in `tests/test_xg.py` to use the same field names.

- [ ] **Step 3: Run tests**

```bash
cd ~/apuestas-daemon && ~/.local/bin/uv run pytest tests/test_xg.py -v --tb=short
```

Expected: all 3 pass.

- [ ] **Step 4: Commit**

```bash
cd ~/apuestas-daemon && git add scrapers/xg.py tests/test_xg.py && git commit -m "fix: update Sofascore field names to match actual API response"
```
