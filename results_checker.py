"""
Check results of past edge alerts, write PnL back to frontmatter,
and compute weekly calibration stats.
"""
import os
import httpx
import yaml
from pathlib import Path
from datetime import datetime, timedelta, timezone

FDAPI_BASE = "https://api.football-data.org/v4"
FDAPI_KEY = os.getenv("FOOTBALL_DATA_API_KEY", "")

def _read_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    try:
        end = text.index("---", 3)
        return yaml.safe_load(text[3:end]) or {}
    except (ValueError, yaml.YAMLError):
        return {}

def _write_frontmatter(path: Path, data: dict) -> None:
    text = path.read_text(encoding="utf-8")
    body = ""
    if text.startswith("---"):
        try:
            end = text.index("---", 3)
            body = text[end + 3:]
        except ValueError:
            pass
    path.write_text(
        "---\n" + yaml.dump(data, allow_unicode=True, default_flow_style=False) + "---\n" + body,
        encoding="utf-8",
    )

def _did_market_win(result_score: str, market_label: str) -> bool:
    """Given 'home_score-away_score' and a market label, return True if the bet won."""
    try:
        h, a = map(int, result_score.split("-"))
    except ValueError:
        return False
    label = market_label.lower()
    if "home" in label:
        return h > a
    if "draw" in label or "empate" in label:
        return h == a
    if "away" in label:
        return a > h
    return False

async def _fetch_match_result(match_name: str, match_date: datetime) -> str | None:
    """Query football-data.org for a finished match score. Returns 'home-away' or None."""
    if not FDAPI_KEY:
        return None
    date_str = match_date.strftime("%Y-%m-%d")
    parts = match_name.split(" vs ")
    if len(parts) != 2:
        return None
    home_frag = parts[0].strip().lower()[:6]
    away_frag = parts[1].strip().lower()[:6]

    async with httpx.AsyncClient(timeout=15) as c:
        try:
            r = await c.get(
                f"{FDAPI_BASE}/matches",
                headers={"X-Auth-Token": FDAPI_KEY},
                params={"dateFrom": date_str, "dateTo": date_str, "status": "FINISHED"},
            )
            if r.status_code != 200:
                return None
            for m in r.json().get("matches", []):
                api_home = m.get("homeTeam", {}).get("name", "").lower()
                api_away = m.get("awayTeam", {}).get("name", "").lower()
                if home_frag in api_home and away_frag in api_away:
                    score = m.get("score", {}).get("fullTime", {})
                    h, a = score.get("home"), score.get("away")
                    if h is not None and a is not None:
                        return f"{h}-{a}"
        except httpx.RequestError:
            pass
    return None

async def check_pending_results(data_dir: Path) -> list[dict]:
    """Scan data_dir for unchecked edge .md files whose match has ended.
    Updates frontmatter with won/pnl_units/result_score/checked_at."""
    if not data_dir.exists():
        return []

    now = datetime.now(timezone.utc)
    resolved = []

    for md_path in data_dir.glob("*.md"):
        fm = _read_frontmatter(md_path)
        if not fm or fm.get("won") is not None:
            continue

        date_str = fm.get("date", "")
        try:
            match_date = datetime.strptime(date_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if now - match_date < timedelta(hours=2):
            continue

        score = await _fetch_match_result(fm.get("match", ""), match_date)
        if score is None:
            continue

        won = _did_market_win(score, fm.get("market", ""))
        odds = fm.get("odds_current", 2.0)
        pnl = round(float(odds) - 1, 4) if won else -1.0

        fm["won"] = won
        fm["result_score"] = score
        fm["pnl_units"] = pnl
        fm["checked_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        _write_frontmatter(md_path, fm)

        resolved.append({"match": fm.get("match"), "market": fm.get("market"), "won": won, "pnl_units": pnl})
        print(f"[results] {fm.get('match')} — {fm.get('market')} → {'WIN' if won else 'LOSS'} {pnl:+.2f}u")

    return resolved

def compute_weekly_stats(data_dir: Path, days: int = 7) -> dict:
    """Aggregate PnL stats for checked edges in the last N days."""
    if not data_dir.exists():
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    total = won_count = 0
    pnl = 0.0
    fair_probs: list[float] = []

    for md_path in data_dir.glob("*.md"):
        fm = _read_frontmatter(md_path)
        if fm.get("won") is None:
            continue
        try:
            match_date = datetime.strptime(fm.get("date", ""), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if match_date < cutoff:
            continue

        total += 1
        if fm.get("won"):
            won_count += 1
        pnl += fm.get("pnl_units", -1.0)
        if fp := fm.get("fair_prob"):
            fair_probs.append(float(fp))

    if total == 0:
        return {}

    hit_rate = round(won_count / total, 4)
    avg_fair = round(sum(fair_probs) / len(fair_probs), 4) if fair_probs else 0.0

    return {
        "period_days": days,
        "total_edges": total,
        "won": won_count,
        "lost": total - won_count,
        "hit_rate": hit_rate,
        "pnl_units": round(pnl, 4),
        "avg_fair_prob": avg_fair,
        "model_gap": round(avg_fair - hit_rate, 4),
    }
