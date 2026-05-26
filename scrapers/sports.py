"""Auto-discover active non-soccer sports from The Odds API."""
import os
import httpx
from datetime import datetime, timedelta

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
_CACHE_TTL = timedelta(hours=24)

_sports_cache: list[str] = []
_cache_time: datetime | None = None

async def _fetch_sports() -> list[dict]:
    api_key = os.getenv("THE_ODDS_API_KEY", "")
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{ODDS_API_BASE}/sports", params={"apiKey": api_key, "all": "false"})
        r.raise_for_status()
        return r.json()

async def get_active_sports(exclude_prefixes: tuple[str, ...] = ("soccer",)) -> list[str]:
    """Return sport keys with active events, excluding soccer (handled by football pipeline)."""
    global _sports_cache, _cache_time

    now = datetime.utcnow()
    if _sports_cache and _cache_time and (now - _cache_time) < _CACHE_TTL:
        return _sports_cache

    api_key = os.getenv("THE_ODDS_API_KEY", "")
    if not api_key:
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
