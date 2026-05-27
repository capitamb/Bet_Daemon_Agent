"""
News signal scraper — fetches RSS from major sports outlets,
searches for team/player name mentions and flags injury/suspension keywords.
No extra dependencies: uses httpx (already installed) + stdlib xml.
Cache TTL: 30 minutes.
"""
import re
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# RSS feeds by sport prefix
_FEEDS: dict[str, list[str]] = {
    "football": [
        "https://feeds.bbci.co.uk/sport/football/rss.xml",
        "https://www.theguardian.com/football/rss",
    ],
    "soccer": [
        "https://feeds.bbci.co.uk/sport/football/rss.xml",
    ],
    "mma": [
        "https://www.mmafighting.com/rss/current.xml",
        "https://mmajunkie.usatoday.com/feed",
    ],
    "tennis": [
        "https://feeds.bbci.co.uk/sport/tennis/rss.xml",
    ],
    "basketball": [
        "https://feeds.bbci.co.uk/sport/basketball/rss.xml",
    ],
    "americanfootball": [
        "https://www.nfl.com/rss/rsslanding.html",
    ],
    "baseball": [
        "https://feeds.bbci.co.uk/sport/rss.xml",
    ],
}

_INJURY_KEYWORDS = [
    # English
    "injur", "doubtful", "doubt", "suspended", "suspension",
    "miss", "missing", "ruled out", " out ", "absence", "absent",
    "scratch", "dnp", "did not play", "hip", "hamstring", "ankle",
    "knee", "shoulder", "back", "illness", "concussion",
    # Spanish
    "lesión", "lesionado", "lesionada", "baja", "duda", "sancionado",
    "suspendido", "ausente", "descartado",
]

_TAGS = re.compile(r"<[^>]+>")

# Cache: sport_prefix → (headlines, fetched_at)
_cache: dict[str, tuple[list[str], datetime]] = {}
_CACHE_TTL = timedelta(minutes=30)


async def _fetch_rss(url: str) -> list[str]:
    """Return list of lowercased 'title description' strings from an RSS feed."""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ApuestasDaemon/1.0)"})
            if r.status_code != 200:
                return []
        root = ET.fromstring(r.text)
        results = []
        for item in root.iter("item"):
            title = item.findtext("title") or ""
            desc = item.findtext("description") or ""
            combined = _TAGS.sub(" ", f"{title} {desc}").lower()
            results.append(combined)
        return results
    except Exception:
        return []


async def _get_headlines(sport_prefix: str) -> list[str]:
    now = datetime.utcnow()
    cached = _cache.get(sport_prefix)
    if cached and (now - cached[1]) < _CACHE_TTL:
        return cached[0]

    feeds = _FEEDS.get(sport_prefix, _FEEDS["football"])
    all_headlines: list[str] = []
    for url in feeds:
        all_headlines.extend(await _fetch_rss(url))

    _cache[sport_prefix] = (all_headlines, now)
    return all_headlines


async def get_news_signal(team_names: list[str], sport: str = "football") -> str:
    """
    Search recent news for injury/suspension mentions of the given teams.

    Args:
        team_names: List of team or player names to search for.
        sport: Odds API sport key (e.g. "football", "mma_mixed_martial_arts").

    Returns:
        Compact string of relevant headlines (max 3), or "" if none found.
    """
    sport_prefix = sport.split("_")[0]
    headlines = await _get_headlines(sport_prefix)
    if not headlines:
        return ""

    search_terms = [name.lower() for name in team_names if name and len(name) > 3]
    found: list[str] = []

    for line in headlines:
        mentions_team = any(term in line for term in search_terms)
        has_injury = any(kw in line for kw in _INJURY_KEYWORDS)
        if mentions_team and has_injury:
            clean = line[:120].strip().replace("\n", " ")
            if clean not in found:
                found.append(clean)

    return " | ".join(found[:3])
