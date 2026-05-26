import pytest
from datetime import datetime, timezone
from models import Match, EdgeResult

def _make_edge(odds: float, fair_prob: float, market: str = "home") -> tuple[Match, EdgeResult]:
    match = Match(
        home_team="Arsenal", away_team="PSG",
        competition="Champions League",
        date=datetime(2026, 5, 30, 16, 0, tzinfo=timezone.utc),
    )
    edge = EdgeResult(
        market=market, odds=odds, implied_prob=round(1/odds, 4),
        fair_prob=fair_prob, edge=round(fair_prob - 1/odds, 4),
        signal="media", qualifies=True,
    )
    return match, edge

def test_format_combinada_contains_legs():
    from notifier import format_combinada_alert
    legs = [_make_edge(2.10, 0.55), _make_edge(1.85, 0.60)]
    msg = format_combinada_alert(legs)
    assert "COMBINADA" in msg
    assert "2 legs" in msg
    assert "Leg 1" in msg
    assert "Leg 2" in msg

def test_format_combinada_kelly_calculation():
    from notifier import format_combinada_alert
    legs = [_make_edge(2.00, 0.60), _make_edge(2.00, 0.60)]
    msg = format_combinada_alert(legs)
    # odds_combinada = 4.00
    assert "Odd total: 4.00" in msg
    assert "%" in msg

def test_format_results_summary():
    from notifier import format_results_summary
    stats = {
        "period_days": 7,
        "total_edges": 10,
        "won": 6,
        "lost": 4,
        "hit_rate": 0.60,
        "pnl_units": 2.5,
        "avg_fair_prob": 0.58,
        "model_gap": -0.02,
    }
    msg = format_results_summary(stats)
    assert "RESUMEN SEMANAL" in msg
    assert "60.0%" in msg
    assert "+2.50u" in msg
