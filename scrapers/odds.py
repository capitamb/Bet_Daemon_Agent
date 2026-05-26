"""Fetch odds from The Odds API. Provides Pinnacle edge odds + consensus fair_prob."""
import os
import httpx
from datetime import datetime, timezone
from models import Match
from config import ODDS_API_FOOTBALL_SPORTS, CONSENSUS_MIN_BOOKMAKERS, CUP_COMPETITIONS

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
    Also sets consensus fair_prob for cup competitions (CL/EL).
    """
    if not ODDS_API_KEY:
        print("[odds] THE_ODDS_API_KEY not set — skipping odds fetch")
        return matches

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

            oh, od, oa = _pinnacle_odds(event)
            match.odds_home = oh
            match.odds_draw = od
            match.odds_away = oa

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
            continue

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
