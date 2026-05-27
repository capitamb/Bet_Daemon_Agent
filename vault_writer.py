import yaml
from pathlib import Path
from datetime import datetime
from models import Match, EdgeResult

def parse_frontmatter(path: Path) -> dict:
    """Parse YAML frontmatter from a .md file."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    end = text.index("---", 3)
    return yaml.safe_load(text[3:end])

def _market_label(match: Match, market: str) -> str:
    if market == "home":
        return f"{match.home_team} ML (home)"
    elif market == "draw":
        return "Empate (draw)"
    return f"{match.away_team} ML (away)"

def write_edge_alert(match: Match, edge: EdgeResult, data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    date_str = match.date.strftime("%Y-%m-%d")
    slug = match.home_team.lower().replace(" ", "-")
    filename = f"{date_str}-{slug}-vs-{match.away_team.lower().replace(' ', '-')}-{edge.market}.md"
    path = data_dir / filename

    frontmatter = {
        "sport": match.sport,
        "match": f"{match.home_team} vs {match.away_team}",
        "date": match.date.strftime("%Y-%m-%d %H:%M"),
        "competition": match.competition,
        "market": _market_label(match, edge.market),
        "implied_prob": round(edge.implied_prob, 4),
        "fair_prob": round(edge.fair_prob, 4),
        "edge": round(edge.edge, 4),
        "signal": edge.signal,
        "odds_current": edge.odds,
        "injuries": match.injuries or "",
        "line_movement": match.line_movement or "",
        "news_signal": match.news_signal or "",
        "polymarket_signal": match.polymarket_signal or "",
        "daemon_run": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }

    content = "---\n" + yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False) + "---\n"
    path.write_text(content, encoding="utf-8")
    return path
