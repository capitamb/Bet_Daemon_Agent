"""Fetch team xG stats from Understat for top 5 European leagues."""
import aiohttp
import understat as us
from datetime import datetime
from models import TeamXG
from config import UNDERSTAT_LEAGUES

# Cache: cache_key → {team_name: TeamXG}
_cache: dict[str, dict[str, TeamXG]] = {}

def _current_season() -> int:
    now = datetime.now()
    # Season starts in Aug/Sep; if before August, we're in previous season
    return now.year if now.month >= 8 else now.year - 1

def _normalize_team(name: str) -> str:
    return name.lower().replace(" ", "").replace("fc", "").replace(".", "")

async def get_league_xg(league_name: str, season: int | None = None) -> dict[str, TeamXG]:
    """
    Return {team_name: TeamXG} for all teams in a league.
    Uses Understat data. league_name must be a key in UNDERSTAT_LEAGUES.
    """
    if season is None:
        season = _current_season()

    cache_key = f"{league_name}_{season}"
    if cache_key in _cache:
        return _cache[cache_key]

    understat_league = UNDERSTAT_LEAGUES.get(league_name)
    if not understat_league:
        return {}

    result: dict[str, TeamXG] = {}
    try:
        async with aiohttp.ClientSession() as session:
            client = us.Understat(session)
            teams_data = await client.get_league_table(understat_league, season)
            for team in teams_data:
                name = team.get("title", "")
                xg_for = float(team.get("xG", 0))
                xg_against = float(team.get("xGA", 0))
                matches_played = int(team.get("m", 1)) or 1
                result[name] = TeamXG(
                    team=name,
                    xg_scored_avg=round(xg_for / matches_played, 3),
                    xg_conceded_avg=round(xg_against / matches_played, 3),
                    games_sampled=matches_played,
                )
    except Exception as e:
        print(f"[xg] Understat fetch failed for {league_name}: {e}")

    _cache[cache_key] = result
    return result

async def find_team_xg(team_name: str, competition: str) -> TeamXG | None:
    """Find TeamXG for a team by fuzzy-matching team name across the league."""
    league_xg = await get_league_xg(competition)
    norm_target = _normalize_team(team_name)
    for name, xg in league_xg.items():
        if norm_target in _normalize_team(name) or _normalize_team(name) in norm_target:
            return xg
    return None
