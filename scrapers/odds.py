"""Fetch current odds from The Odds API free tier."""
import os
import httpx
from models import Match
from config import ODDS_API_FOOTBALL_SPORTS

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY", "")

def _normalize(name: str) -> str:
    return name.lower().replace(" ", "").replace("fc", "").replace(".", "")

def _match_team(api_name: str, fixture_name: str) -> bool:
    return _normalize(api_name) in _normalize(fixture_name) or \
           _normalize(fixture_name) in _normalize(api_name)

async def enrich_matches_with_odds(matches: list[Match]) -> list[Match]:
    """Attach odds_home, odds_draw, odds_away to each match where available."""
    if not ODDS_API_KEY:
        print("[odds] THE_ODDS_API_KEY not set — skipping odds fetch")
        return matches

    odds_map: dict[str, dict] = {}

    async with httpx.AsyncClient(timeout=15) as client:
        for sport_key in ODDS_API_FOOTBALL_SPORTS:
            url = f"{ODDS_API_BASE}/sports/{sport_key}/odds"
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": "eu",
                "markets": "h2h",
                "oddsFormat": "decimal",
                "bookmakers": "pinnacle",  # sharpest line
            }
            try:
                r = await client.get(url, params=params)
                if r.status_code != 200:
                    continue
                for event in r.json():
                    key = (event["home_team"], event["away_team"])
                    odds_map[str(key)] = event
            except (httpx.RequestError, KeyError):
                continue

    for match in matches:
        for key_str, event in odds_map.items():
            home_api = event["home_team"]
            away_api = event["away_team"]
            if _match_team(home_api, match.home_team) and _match_team(away_api, match.away_team):
                for bookmaker in event.get("bookmakers", []):
                    for market in bookmaker.get("markets", []):
                        if market["key"] == "h2h":
                            outcomes = {o["name"]: o["price"] for o in market["outcomes"]}
                            match.odds_home = outcomes.get(home_api)
                            match.odds_draw = outcomes.get("Draw")
                            match.odds_away = outcomes.get(away_api)
                            break
                break

    return matches
