import pytest
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone

def _write_md(path: Path, frontmatter: dict) -> None:
    import yaml
    content = "---\n" + yaml.dump(frontmatter, allow_unicode=True) + "---\n"
    path.write_text(content, encoding="utf-8")

def test_did_market_win_home():
    from results_checker import _did_market_win
    assert _did_market_win("2-1", "Arsenal ML (home)") is True
    assert _did_market_win("1-2", "Arsenal ML (home)") is False
    assert _did_market_win("1-1", "Arsenal ML (home)") is False

def test_did_market_win_draw():
    from results_checker import _did_market_win
    assert _did_market_win("1-1", "Empate (draw)") is True
    assert _did_market_win("2-1", "Empate (draw)") is False

def test_did_market_win_away():
    from results_checker import _did_market_win
    assert _did_market_win("0-3", "PSG ML (away)") is True
    assert _did_market_win("1-0", "PSG ML (away)") is False

def test_compute_weekly_stats_empty_dir():
    from results_checker import compute_weekly_stats
    with tempfile.TemporaryDirectory() as d:
        stats = compute_weekly_stats(Path(d))
    assert stats == {}

def test_compute_weekly_stats_with_results():
    from results_checker import compute_weekly_stats
    with tempfile.TemporaryDirectory() as d:
        data_dir = Path(d)
        now = datetime.now(timezone.utc)
        _write_md(data_dir / "edge1.md", {
            "match": "Arsenal FC vs PSG",
            "date": (now - timedelta(hours=10)).strftime("%Y-%m-%d %H:%M"),
            "market": "Arsenal ML (home)",
            "odds_current": 2.10,
            "fair_prob": 0.55,
            "won": True,
            "pnl_units": 1.10,
        })
        _write_md(data_dir / "edge2.md", {
            "match": "Arsenal FC vs PSG",
            "date": (now - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M"),
            "market": "Empate (draw)",
            "odds_current": 3.40,
            "fair_prob": 0.35,
            "won": False,
            "pnl_units": -1.0,
        })
        stats = compute_weekly_stats(data_dir)

    assert stats["total_edges"] == 2
    assert stats["won"] == 1
    assert stats["lost"] == 1
    assert abs(stats["pnl_units"] - 0.10) < 0.01
    assert stats["hit_rate"] == 0.5
