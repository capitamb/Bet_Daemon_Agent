import pytest
from poisson_model import poisson_match_probs, compute_lambdas, fair_probs_from_xg
from models import TeamXG

def test_probs_sum_to_one():
    p_home, p_draw, p_away = poisson_match_probs(1.5, 1.2)
    assert abs(p_home + p_draw + p_away - 1.0) < 0.001

def test_strong_home_team_wins_more():
    p_home, _, p_away = poisson_match_probs(2.5, 0.8)
    assert p_home > p_away

def test_equal_teams_roughly_equal():
    p_home, _, p_away = poisson_match_probs(1.3, 1.3)
    assert abs(p_home - p_away) < 0.05

def test_compute_lambdas_returns_two_floats():
    home_xg = TeamXG("Arsenal", xg_scored_avg=1.8, xg_conceded_avg=0.9, games_sampled=5)
    away_xg = TeamXG("Chelsea", xg_scored_avg=1.4, xg_conceded_avg=1.1, games_sampled=5)
    lh, la = compute_lambdas(home_xg, away_xg, league_avg_xg=1.35)
    assert lh > 0
    assert la > 0

def test_fair_probs_from_xg_sums_to_one():
    home_xg = TeamXG("Arsenal", xg_scored_avg=1.8, xg_conceded_avg=0.9, games_sampled=5)
    away_xg = TeamXG("Chelsea", xg_scored_avg=1.4, xg_conceded_avg=1.1, games_sampled=5)
    ph, pd, pa = fair_probs_from_xg(home_xg, away_xg)
    assert abs(ph + pd + pa - 1.0) < 0.001
