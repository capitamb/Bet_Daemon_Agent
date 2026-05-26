"""Fetch team xG stats from Sofascore for top 5 European leagues.

Strategy: standings endpoint provides the team list (with IDs), then for each
team we fetch the last SAMPLE_MATCHES events and aggregate per-match xG data
(key: ``expectedGoals`` in match statistics, period=ALL).
"""
import asyncio
import httpx
from models import TeamXG
from config import SOFASCORE_TOURNAMENTS

SOFASCORE_BASE = "https://api.sofascore.com/api/v1"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# How many recent matches to sample per team for the rolling xG average
SAMPLE_MATCHES = 10

_season_cache: dict[int, int] = {}
_xg_cache: dict[str, dict[str, TeamXG]] = {}


def _normalize_team(name: str) -> str:
    return name.lower().replace(" ", "").replace("-", "").replace("fc", "").replace(".", "")


# ---------------------------------------------------------------------------
# Low-level fetch helpers (isolated so tests can patch them easily)
# ---------------------------------------------------------------------------

async def _fetch_seasons(tid: int) -> dict:
    async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as c:
        r = await c.get(f"{SOFASCORE_BASE}/unique-tournament/{tid}/seasons")
        r.raise_for_status()
        return r.json()


async def _fetch_standings(tid: int, sid: int) -> dict:
    """Return standings (total) for tournament *tid* and season *sid*.

    The standings rows contain ``team.id`` which is needed to look up per-team
    events.  They do NOT contain xG data — that lives in per-match stats.
    """
    async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as c:
        r = await c.get(
            f"{SOFASCORE_BASE}/unique-tournament/{tid}/season/{sid}/standings/total"
        )
        r.raise_for_status()
        return r.json()


async def _fetch_team_events(team_id: int, page: int) -> dict:
    """Return the last-events page for a team (page 0 = most recent)."""
    async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as c:
        r = await c.get(f"{SOFASCORE_BASE}/team/{team_id}/events/last/{page}")
        r.raise_for_status()
        return r.json()


async def _fetch_event_stats(event_id: int) -> dict:
    """Return full statistics for a single match event."""
    async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as c:
        r = await c.get(f"{SOFASCORE_BASE}/event/{event_id}/statistics")
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# Season helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# xG extraction helpers
# ---------------------------------------------------------------------------

def _extract_xg_from_stats(stats_data: dict, team_id: int, home_team_id: int) -> tuple[float, float] | None:
    """Return (xg_for, xg_against) for the given team from match stats, or None."""
    is_home = team_id == home_team_id
    for period_block in stats_data.get("statistics", []):
        if period_block.get("period") != "ALL":
            continue
        for group in period_block.get("groups", []):
            for item in group.get("statisticsItems", []):
                if item.get("key") == "expectedGoals":
                    home_val = float(item.get("homeValue") or 0)
                    away_val = float(item.get("awayValue") or 0)
                    if is_home:
                        return home_val, away_val
                    else:
                        return away_val, home_val
    return None


async def _team_xg_from_events(team_id: int, sample: int) -> tuple[float, float, int]:
    """Fetch the last *sample* events for a team and aggregate xG.

    Returns (total_xg_for, total_xg_against, match_count).
    """
    collected: list[dict] = []
    page = 0
    while len(collected) < sample:
        try:
            data = await _fetch_team_events(team_id, page)
        except Exception as e:
            print(f"[xg] Failed to fetch events for team {team_id} page {page}: {e}")
            break
        events = [e for e in data.get("events", []) if e.get("hasXg")]
        collected.extend(events)
        if not data.get("hasNextPage", False):
            break
        page += 1

    # Use only the most-recent *sample* (list comes newest-first from API)
    collected = collected[:sample]

    tasks = [_fetch_event_stats(e["id"]) for e in collected]
    stats_results = await asyncio.gather(*tasks, return_exceptions=True)

    total_for = 0.0
    total_against = 0.0
    count = 0
    for event, stats_data in zip(collected, stats_results):
        if isinstance(stats_data, Exception):
            continue
        home_team_id = event.get("homeTeam", {}).get("id")
        pair = _extract_xg_from_stats(stats_data, team_id, home_team_id)
        if pair is None:
            continue
        total_for += pair[0]
        total_against += pair[1]
        count += 1

    return total_for, total_against, count


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_league_xg(league_name: str) -> dict[str, TeamXG]:
    """Return ``{team_name: TeamXG}`` for all teams in a league.

    Uses Sofascore standings to discover teams, then aggregates xG from the
    last :data:`SAMPLE_MATCHES` league events per team.
    """
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
        standings_data = await _fetch_standings(tid, sid)
        rows = standings_data.get("standings", [{}])[0].get("rows", [])

        async def _process_team(row: dict) -> tuple[str, TeamXG] | None:
            team_obj = row.get("team", {})
            team_name = team_obj.get("name", "")
            team_id = team_obj.get("id")
            if not team_name or not team_id:
                return None
            total_for, total_against, count = await _team_xg_from_events(team_id, SAMPLE_MATCHES)
            if count == 0:
                return None
            return team_name, TeamXG(
                team=team_name,
                xg_scored_avg=round(total_for / count, 3),
                xg_conceded_avg=round(total_against / count, 3),
                games_sampled=count,
            )

        team_results = await asyncio.gather(*[_process_team(row) for row in rows])
        for item in team_results:
            if item is not None:
                result[item[0]] = item[1]

    except Exception as e:
        print(f"[xg] Sofascore fetch failed for {league_name}: {e}")

    _xg_cache[league_name] = result
    return result


# For cups/CL/EL: map normalized team name fragment → domestic league name
_TEAM_LEAGUE_FALLBACK: dict[str, str] = {
    # PL clubs
    "arsenal": "Premier League",
    "chelsea": "Premier League",
    "liverpool": "Premier League",
    "mancity": "Premier League",
    "manchestercity": "Premier League",
    "manchesterunited": "Premier League",
    "tottenham": "Premier League",
    "newcastle": "Premier League",
    "astonvilla": "Premier League",
    # La Liga
    "realmadrid": "La Liga",
    "barcelona": "La Liga",
    "atleticomadrid": "La Liga",
    "sevilla": "La Liga",
    "villarreal": "La Liga",
    "realsociedad": "La Liga",
    "athletic": "La Liga",
    # Serie A
    "juventus": "Serie A",
    "inter": "Serie A",
    "intermilan": "Serie A",
    "acmilan": "Serie A",
    "milan": "Serie A",
    "napoli": "Serie A",
    "roma": "Serie A",
    "atalanta": "Serie A",
    "lazio": "Serie A",
    # Bundesliga
    "bayernmunich": "Bundesliga",
    "bayernmünchen": "Bundesliga",
    "borussiadortmund": "Bundesliga",
    "dortmund": "Bundesliga",
    "bayer04": "Bundesliga",
    "bayer": "Bundesliga",
    "leipzig": "Bundesliga",
    # Ligue 1
    "parissaintgermain": "Ligue 1",
    "psg": "Ligue 1",
    "marseille": "Ligue 1",
    "monaco": "Ligue 1",
    "lyon": "Ligue 1",
    "lille": "Ligue 1",
}


async def find_team_xg(team_name: str, competition: str) -> TeamXG | None:
    """Find TeamXG for a team by fuzzy-matching against league data.

    Falls back to domestic league lookup for cup competitions (CL, EL, etc.).
    """
    norm = _normalize_team(team_name)

    league_xg = await get_league_xg(competition)
    for name, xg in league_xg.items():
        n = _normalize_team(name)
        if norm in n or n in norm:
            return xg

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
