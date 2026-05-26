from notifier import format_football_alert
from models import Match, EdgeResult
from datetime import datetime

def test_format_alert_contains_match():
    match = Match(
        home_team="Arsenal", away_team="Chelsea",
        competition="Premier League", date=datetime(2026, 6, 1),
        injuries="Saka duda",
    )
    edge = EdgeResult(
        market="home", odds=2.5, implied_prob=0.40,
        fair_prob=0.48, edge=0.08, signal="media", qualifies=True,
    )
    msg = format_football_alert(match, edge)
    assert "Arsenal" in msg
    assert "Chelsea" in msg
    assert "8.0%" in msg
    assert "MEDIA" in msg

def test_format_alert_contains_injuries():
    match = Match(
        home_team="Arsenal", away_team="Chelsea",
        competition="Premier League", date=datetime(2026, 6, 1),
        injuries="Saka duda",
    )
    edge = EdgeResult(
        market="home", odds=2.5, implied_prob=0.40,
        fair_prob=0.48, edge=0.08, signal="media", qualifies=True,
    )
    msg = format_football_alert(match, edge)
    assert "Saka" in msg

def test_format_alert_no_injuries():
    match = Match(
        home_team="Arsenal", away_team="Chelsea",
        competition="Premier League", date=datetime(2026, 6, 1),
        injuries="",
    )
    edge = EdgeResult(
        market="home", odds=2.5, implied_prob=0.40,
        fair_prob=0.48, edge=0.08, signal="media", qualifies=True,
    )
    msg = format_football_alert(match, edge)
    assert "🚨" in msg  # header always present
