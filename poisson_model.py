from scipy.stats import poisson as scipy_poisson
from models import TeamXG

LEAGUE_AVG_XG = 1.35          # typical top-5 league average xG per team per game
HOME_ADVANTAGE = 1.10         # home teams score ~10% more goals on average

def poisson_match_probs(lambda_home: float, lambda_away: float, max_goals: int = 8) -> tuple[float, float, float]:
    """
    Compute (P_home_win, P_draw, P_away_win) using independent Poisson model.
    Sums over goal matrix up to max_goals x max_goals.
    """
    p_home = p_draw = p_away = 0.0
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = scipy_poisson.pmf(i, lambda_home) * scipy_poisson.pmf(j, lambda_away)
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p
    return p_home, p_draw, p_away

def compute_lambdas(
    home_xg: TeamXG,
    away_xg: TeamXG,
    league_avg_xg: float = LEAGUE_AVG_XG,
) -> tuple[float, float]:
    """
    Dixon-Robinson style attack/defense strengths from xG averages.
    lambda_home = home_attack * away_defense * league_avg * HOME_ADVANTAGE
    lambda_away = away_attack * home_defense * league_avg
    """
    home_attack = home_xg.xg_scored_avg / league_avg_xg
    home_defense = home_xg.xg_conceded_avg / league_avg_xg
    away_attack = away_xg.xg_scored_avg / league_avg_xg
    away_defense = away_xg.xg_conceded_avg / league_avg_xg

    lambda_home = home_attack * away_defense * league_avg_xg * HOME_ADVANTAGE
    lambda_away = away_attack * home_defense * league_avg_xg
    return lambda_home, lambda_away

def fair_probs_from_xg(
    home_xg: TeamXG,
    away_xg: TeamXG,
    league_avg_xg: float = LEAGUE_AVG_XG,
) -> tuple[float, float, float]:
    """Return (P_home_win, P_draw, P_away_win) from TeamXG objects."""
    lh, la = compute_lambdas(home_xg, away_xg, league_avg_xg)
    return poisson_match_probs(lh, la)
