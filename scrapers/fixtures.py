"""Fetch upcoming football fixtures from football-data.org free API."""
import os
import httpx
from datetime import datetime, timedelta, timezone
from models import Match
from config import FOOTBALL_COMPETITIONS

FDAPI_BASE = "https://api.football-data.org/v4"
FDAPI_KEY = os.getenv("FOOTBALL_DATA_API_KEY", "")

COMP_IDS = {
    "PL":  "Premier League",
    "PD":  "La Liga",
    "SA":  "Serie A",
    "BL1": "Bundesliga",
    "FL1": "Ligue 1",
    "CL":  "Champions League",
    "EL":  "Europa League",
}

async def fetch_upcoming_fixtures(hours_ahead: int = 48) -> list[Match]:
    """Return upcoming matches in the next `hours_ahead` hours across all competitions."""
    if not FDAPI_KEY:
        print("[fixtures] FOOTBALL_DATA_API_KEY not set — skipping fixture fetch")
        return []

    now = datetime.now(timezone.utc)
    date_from = now.strftime("%Y-%m-%d")
    date_to = (now + timedelta(hours=hours_ahead)).strftime("%Y-%m-%d")

    matches: list[Match] = []
    headers = {"X-Auth-Token": FDAPI_KEY}

    async with httpx.AsyncClient(timeout=15) as client:
        for comp_id, comp_name in COMP_IDS.items():
            url = f"{FDAPI_BASE}/competitions/{comp_id}/matches"
            params = {"dateFrom": date_from, "dateTo": date_to}
            try:
                r = await client.get(url, headers=headers, params=params)
                if r.status_code != 200:
                    continue
                data = r.json()
                for m in data.get("matches", []):
                    utc_date = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
                    matches.append(Match(
                        home_team=m["homeTeam"]["name"],
                        away_team=m["awayTeam"]["name"],
                        competition=comp_name,
                        date=utc_date,
                    ))
            except (httpx.RequestError, KeyError):
                continue

    return matches
