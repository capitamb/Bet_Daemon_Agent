import pytest

def test_consensus_fair_prob_removes_vig():
    """consensus_fair_prob averages vig-free probs across bookmakers."""
    from scrapers.odds import consensus_fair_prob

    bookmakers = [
        {"markets": [{"key": "h2h", "outcomes": [
            {"name": "Home", "price": 2.10},
            {"name": "Draw", "price": 3.40},
            {"name": "Away", "price": 3.20},
        ]}]},
        {"markets": [{"key": "h2h", "outcomes": [
            {"name": "Home", "price": 2.00},
            {"name": "Draw", "price": 3.50},
            {"name": "Away", "price": 3.30},
        ]}]},
        {"markets": [{"key": "h2h", "outcomes": [
            {"name": "Home", "price": 2.05},
            {"name": "Draw", "price": 3.45},
            {"name": "Away", "price": 3.25},
        ]}]},
    ]
    event = {"home_team": "Home", "away_team": "Away", "bookmakers": bookmakers}
    result = consensus_fair_prob(event)
    assert result is not None
    ph, pd, pa = result
    assert abs(ph + pd + pa - 1.0) < 0.001
    assert ph > pa
    assert ph > pd

def test_consensus_fair_prob_returns_none_below_min_bookmakers():
    """consensus_fair_prob returns None when fewer than CONSENSUS_MIN_BOOKMAKERS."""
    from scrapers.odds import consensus_fair_prob

    bookmakers = [
        {"markets": [{"key": "h2h", "outcomes": [
            {"name": "Home", "price": 1.90},
            {"name": "Away", "price": 1.90},
        ]}]},
    ]
    event = {"home_team": "Home", "away_team": "Away", "bookmakers": bookmakers}
    result = consensus_fair_prob(event)
    assert result is None

def test_consensus_no_draw_sport():
    """consensus_fair_prob handles events with no Draw outcome (NBA, etc.)."""
    from scrapers.odds import consensus_fair_prob

    bookmakers = [
        {"markets": [{"key": "h2h", "outcomes": [
            {"name": "Lakers", "price": 1.85},
            {"name": "Celtics", "price": 1.95},
        ]}]},
        {"markets": [{"key": "h2h", "outcomes": [
            {"name": "Lakers", "price": 1.82},
            {"name": "Celtics", "price": 2.00},
        ]}]},
        {"markets": [{"key": "h2h", "outcomes": [
            {"name": "Lakers", "price": 1.87},
            {"name": "Celtics", "price": 1.93},
        ]}]},
    ]
    event = {"home_team": "Lakers", "away_team": "Celtics", "bookmakers": bookmakers}
    result = consensus_fair_prob(event)
    assert result is not None
    ph, pd, pa = result
    assert pd is None
    assert abs(ph + pa - 1.0) < 0.001
