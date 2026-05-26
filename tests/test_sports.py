import pytest
from unittest.mock import patch
import os

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
        with patch.dict(os.environ, {"THE_ODDS_API_KEY": "test_key"}):
            from scrapers import sports as sports_mod
            sports_mod._sports_cache.clear()
            sports_mod._cache_time = None
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
        with patch.dict(os.environ, {"THE_ODDS_API_KEY": "test_key"}):
            from scrapers import sports as sports_mod
            sports_mod._sports_cache.clear()
            sports_mod._cache_time = None
            await sports_mod.get_active_sports()
            await sports_mod.get_active_sports()
            assert mock_fetch.call_count == 1
