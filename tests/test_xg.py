import pytest
from unittest.mock import patch
from models import TeamXG

# Standings response: just team names and IDs (no xG fields in this endpoint)
MOCK_STANDINGS = {
    "standings": [{
        "rows": [
            {
                "team": {"name": "Arsenal", "id": 42},
                "matches": 38,
            },
            {
                "team": {"name": "Manchester City", "id": 17},
                "matches": 38,
            },
        ]
    }]
}

# Team events: list of match events (last page)
def _make_events(team_id: int, event_ids: list[int], home_team_id: int = None) -> dict:
    if home_team_id is None:
        home_team_id = team_id
    return {
        "events": [
            {
                "id": eid,
                "homeTeam": {"id": home_team_id, "name": "Home Team"},
                "awayTeam": {"id": 999, "name": "Away Team"},
                "hasXg": True,
            }
            for eid in event_ids
        ],
        "hasNextPage": False,
    }

MOCK_ARSENAL_EVENTS = {
    "events": [
        {"id": 1001, "homeTeam": {"id": 42, "name": "Arsenal"}, "awayTeam": {"id": 99, "name": "Opp"}, "hasXg": True},
        {"id": 1002, "homeTeam": {"id": 50, "name": "Opp2"}, "awayTeam": {"id": 42, "name": "Arsenal"}, "hasXg": True},
        {"id": 1003, "homeTeam": {"id": 42, "name": "Arsenal"}, "awayTeam": {"id": 60, "name": "Opp3"}, "hasXg": True},
    ],
    "hasNextPage": False,
}

MOCK_MANCITY_EVENTS = {
    "events": [
        {"id": 2001, "homeTeam": {"id": 17, "name": "Manchester City"}, "awayTeam": {"id": 99, "name": "Opp"}, "hasXg": True},
    ],
    "hasNextPage": False,
}

# Per-event statistics responses: key=event_id
# Arsenal: home xG 2.0, away xG 1.0 (Arsenal is home in 1001, away in 1002, home in 1003)
# Event 1001: Arsenal home → xG for=2.0, against=1.0
# Event 1002: Arsenal away → xG for=1.5, against=0.8
# Event 1003: Arsenal home → xG for=1.8, against=0.6
def _make_event_stats(home_xg: float, away_xg: float) -> dict:
    return {
        "statistics": [{
            "period": "ALL",
            "groups": [{
                "groupName": "Match overview",
                "statisticsItems": [
                    {"key": "expectedGoals", "homeValue": home_xg, "awayValue": away_xg},
                ]
            }]
        }]
    }

MOCK_EVENT_STATS = {
    1001: _make_event_stats(2.0, 1.0),   # Arsenal home: xGF=2.0, xGA=1.0
    1002: _make_event_stats(1.5, 0.8),   # Arsenal away: xGF=0.8, xGA=1.5  (values swapped for away)
    1003: _make_event_stats(1.8, 0.6),   # Arsenal home: xGF=1.8, xGA=0.6
    2001: _make_event_stats(3.0, 0.5),   # ManCity home: xGF=3.0, xGA=0.5
}

# Arsenal: 3 games
# xGF total = 2.0 + 0.8 + 1.8 = 4.6, avg = round(4.6/3, 3) = 1.533
# xGA total = 1.0 + 1.5 + 0.6 = 3.1, avg = round(3.1/3, 3) = 1.033
ARSENAL_EXPECTED_XGF = round(4.6 / 3, 3)
ARSENAL_EXPECTED_XGA = round(3.1 / 3, 3)


async def _mock_fetch_seasons(tid: int) -> dict:
    return {"seasons": [{"id": 76986, "year": "25/26"}]}


async def _mock_fetch_standings(tid: int, sid: int) -> dict:
    return MOCK_STANDINGS


async def _mock_fetch_team_events(team_id: int, page: int) -> dict:
    if team_id == 42:
        return MOCK_ARSENAL_EVENTS
    if team_id == 17:
        return MOCK_MANCITY_EVENTS
    return {"events": [], "hasNextPage": False}


async def _mock_fetch_event_stats(event_id: int) -> dict:
    return MOCK_EVENT_STATS.get(event_id, {"statistics": []})


def _all_mocks():
    return (
        patch("scrapers.xg._fetch_seasons", side_effect=_mock_fetch_seasons),
        patch("scrapers.xg._fetch_standings", side_effect=_mock_fetch_standings),
        patch("scrapers.xg._fetch_team_events", side_effect=_mock_fetch_team_events),
        patch("scrapers.xg._fetch_event_stats", side_effect=_mock_fetch_event_stats),
    )


@pytest.mark.asyncio
async def test_get_league_xg_returns_team_xg():
    p1, p2, p3, p4 = _all_mocks()
    with p1, p2, p3, p4:
        from scrapers.xg import get_league_xg, _xg_cache, _season_cache
        _xg_cache.clear()
        _season_cache.clear()
        result = await get_league_xg("Premier League")
        assert "Arsenal" in result
        arsenal = result["Arsenal"]
        assert isinstance(arsenal, TeamXG)
        assert arsenal.xg_scored_avg == ARSENAL_EXPECTED_XGF
        assert arsenal.xg_conceded_avg == ARSENAL_EXPECTED_XGA
        assert arsenal.games_sampled == 3


@pytest.mark.asyncio
async def test_find_team_xg_fuzzy_match():
    p1, p2, p3, p4 = _all_mocks()
    with p1, p2, p3, p4:
        from scrapers.xg import find_team_xg, _xg_cache, _season_cache
        _xg_cache.clear()
        _season_cache.clear()
        result = await find_team_xg("Arsenal FC", "Premier League")
        assert result is not None
        assert result.team == "Arsenal"


@pytest.mark.asyncio
async def test_find_team_xg_returns_none_for_unknown():
    p1, p2, p3, p4 = _all_mocks()
    with p1, p2, p3, p4:
        from scrapers.xg import find_team_xg, _xg_cache, _season_cache
        _xg_cache.clear()
        _season_cache.clear()
        result = await find_team_xg("Unknown Team XYZ", "Premier League")
        assert result is None
