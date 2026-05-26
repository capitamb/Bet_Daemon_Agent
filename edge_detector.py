from models import Match, EdgeResult
from config import SIGNAL_RANK

def implied_prob_from_odds(odds: float) -> float:
    return 1.0 / odds

def classify_signal(edge: float) -> str:
    if edge >= 0.10:
        return "alta"
    elif edge >= 0.06:
        return "media"
    return "baja"

def detect_edges(match: Match, min_edge: float, min_signal: str) -> list[EdgeResult]:
    """Return EdgeResult for each market (home/draw/away) that has odds + fair_prob."""
    markets = [
        ("home", match.odds_home, match.fair_prob_home),
        ("draw", match.odds_draw, match.fair_prob_draw),
        ("away", match.odds_away, match.fair_prob_away),
    ]
    results = []
    for label, odds, fair_prob in markets:
        if odds is None or fair_prob is None:
            continue
        imp = implied_prob_from_odds(odds)
        edge = fair_prob - imp
        signal = classify_signal(edge)
        qualifies = (
            edge >= min_edge
            and SIGNAL_RANK[signal] >= SIGNAL_RANK[min_signal]
        )
        results.append(EdgeResult(
            market=label, odds=odds, implied_prob=imp,
            fair_prob=fair_prob, edge=edge,
            signal=signal, qualifies=qualifies,
        ))
    return results
