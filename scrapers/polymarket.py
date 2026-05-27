"""
Polymarket price discovery for MMA/UFC markets.
Queries the public Gamma API (no auth required) to extract implied probabilities
from prediction markets. Used as a supplementary signal — NOT for betting.
Cache TTL: 30 minutes.
"""
import json
import httpx
from datetime import datetime, timedelta

GAMMA_BASE = "https://gamma-api.polymarket.com"

_cache: tuple[list[dict], datetime] | tuple[None, None] = (None, None)
_CACHE_TTL = timedelta(minutes=30)


def _normalize(text: str) -> str:
    return text.lower().replace(" ", "").replace("-", "").replace("'", "").replace(".", "")


async def _fetch_ufc_markets() -> list[dict]:
    global _cache
    markets, fetched_at = _cache
    now = datetime.utcnow()

    if markets is not None and fetched_at and (now - fetched_at) < _CACHE_TTL:
        return markets

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{GAMMA_BASE}/markets",
                params={"tag_slug": "ufc", "active": "true", "closed": "false", "limit": 100},
            )
            if r.status_code == 200:
                markets = r.json() if isinstance(r.json(), list) else r.json().get("markets", [])
                _cache = (markets, now)
                return markets
    except Exception:
        pass

    _cache = ([], now)
    return []


async def get_ufc_market_signal(fighter1: str, fighter2: str) -> str:
    """
    Look up Polymarket implied probability for a UFC fight.

    Searches active markets for any question mentioning fighter1 or fighter2.
    Returns a string like "Polymarket: Jones 64% | Miocic 36%" or "" if not found.
    Used only as a price discovery reference — not as a betting signal.
    """
    markets = await _fetch_ufc_markets()
    if not markets:
        return ""

    f1 = _normalize(fighter1)
    f2 = _normalize(fighter2)

    for market in markets:
        question = _normalize(market.get("question", ""))
        if not ((f1 in question or f2 in question) and
                any(kw in question for kw in ("win", "vs", "fight", "beat"))):
            continue

        try:
            outcome_list = market.get("outcomes", [])
            price_list = market.get("outcomePrices", [])
            if isinstance(outcome_list, str):
                outcome_list = json.loads(outcome_list)
            if isinstance(price_list, str):
                price_list = json.loads(price_list)

            if len(outcome_list) >= 2 and len(price_list) >= 2:
                parts = []
                for i, outcome in enumerate(outcome_list[:2]):
                    try:
                        prob = float(price_list[i])
                        parts.append(f"{outcome} {prob:.0%}")
                    except (ValueError, IndexError):
                        pass
                if parts:
                    return "Polymarket: " + " | ".join(parts)
        except Exception:
            continue

    return ""


async def get_any_sport_signal(home: str, away: str, sport_key: str) -> str:
    """
    Generic Polymarket signal lookup for non-UFC sports.
    Currently only MMA has enough Polymarket liquidity to be useful.
    Returns "" for non-MMA sports.
    """
    if not sport_key.startswith("mma"):
        return ""
    return await get_ufc_market_signal(home, away)
