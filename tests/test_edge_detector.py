import pytest
from edge_detector import implied_prob_from_odds, classify_signal, detect_edges
from models import Match, EdgeResult, TeamXG
from datetime import datetime

def test_implied_prob_from_odds():
    assert abs(implied_prob_from_odds(2.0) - 0.5) < 0.001
    assert abs(implied_prob_from_odds(1.5) - 0.667) < 0.001
    assert abs(implied_prob_from_odds(4.0) - 0.25) < 0.001

def test_classify_signal_alta():
    assert classify_signal(0.12) == "alta"

def test_classify_signal_media():
    assert classify_signal(0.07) == "media"

def test_classify_signal_baja():
    assert classify_signal(0.03) == "baja"

def test_detect_edges_qualifies():
    match = Match(
        home_team="Arsenal", away_team="Chelsea",
        competition="Premier League", date=datetime.now(),
        odds_home=2.5, odds_draw=3.4, odds_away=2.8,
        fair_prob_home=0.48, fair_prob_draw=0.27, fair_prob_away=0.25,
    )
    results = detect_edges(match, min_edge=0.06, min_signal="media")
    # fair_prob_home=0.48, implied=1/2.5=0.40, edge=0.08 → qualifies
    assert any(r.market == "home" and r.qualifies for r in results)

def test_detect_edges_no_qualify():
    match = Match(
        home_team="Arsenal", away_team="Chelsea",
        competition="Premier League", date=datetime.now(),
        odds_home=2.0, odds_draw=3.4, odds_away=3.8,
        fair_prob_home=0.50, fair_prob_draw=0.27, fair_prob_away=0.23,
    )
    results = detect_edges(match, min_edge=0.06, min_signal="media")
    # fair_prob_home=0.50, implied=0.50, edge=0.00 → doesn't qualify
    assert not any(r.market == "home" and r.qualifies for r in results)

def test_detect_edges_returns_none_without_odds():
    match = Match(
        home_team="Arsenal", away_team="Chelsea",
        competition="Premier League", date=datetime.now(),
        fair_prob_home=0.48, fair_prob_draw=0.27, fair_prob_away=0.25,
    )
    results = detect_edges(match, min_edge=0.06, min_signal="media")
    assert results == []
