import pytest
import yaml
from pathlib import Path
from datetime import datetime
from models import Match, EdgeResult
from vault_writer import write_edge_alert, parse_frontmatter

def test_write_edge_alert_creates_file(tmp_path):
    match = Match(
        home_team="Arsenal", away_team="Chelsea",
        competition="Premier League", date=datetime(2026, 6, 1, 15, 0),
        odds_home=2.5, injuries="Saka - hamstring (doubt)",
    )
    edge = EdgeResult(
        market="home", odds=2.5, implied_prob=0.40,
        fair_prob=0.48, edge=0.08, signal="media", qualifies=True,
    )
    path = write_edge_alert(match, edge, data_dir=tmp_path)
    assert path.exists()

def test_write_edge_alert_valid_frontmatter(tmp_path):
    match = Match(
        home_team="Arsenal", away_team="Chelsea",
        competition="Premier League", date=datetime(2026, 6, 1, 15, 0),
        odds_home=2.5, injuries="",
    )
    edge = EdgeResult(
        market="home", odds=2.5, implied_prob=0.40,
        fair_prob=0.48, edge=0.08, signal="media", qualifies=True,
    )
    path = write_edge_alert(match, edge, data_dir=tmp_path)
    fm = parse_frontmatter(path)
    assert fm["sport"] == "football"
    assert fm["edge"] == pytest.approx(0.08, abs=0.001)
    assert fm["signal"] == "media"
    assert fm["market"] == "Arsenal ML (home)"

def test_write_edge_alert_filename_format(tmp_path):
    match = Match(
        home_team="Arsenal", away_team="Chelsea",
        competition="Premier League", date=datetime(2026, 6, 1, 15, 0),
        odds_home=2.5,
    )
    edge = EdgeResult(
        market="home", odds=2.5, implied_prob=0.40,
        fair_prob=0.48, edge=0.08, signal="media", qualifies=True,
    )
    path = write_edge_alert(match, edge, data_dir=tmp_path)
    assert path.name.startswith("2026-06-01")
    assert "arsenal" in path.name.lower()
