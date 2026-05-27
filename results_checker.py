"""
Check results of past edge alerts, write PnL back to frontmatter,
resolve pending combinadas, update bankroll, and send Telegram result alert.
"""
import re
import os
import httpx
import yaml
from pathlib import Path
from datetime import datetime, timedelta, timezone

FDAPI_BASE = "https://api.football-data.org/v4"
FDAPI_KEY = os.getenv("FOOTBALL_DATA_API_KEY", "")

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY", "")

ESPN_API_BASE = "https://site.api.espn.com/apis/site/v2/sports"

# Maps sport prefix → (odds_api_sport_key, espn_sport, espn_league)
_SPORT_ENDPOINTS: dict[str, tuple[str, str, str]] = {
    "basketball":       ("basketball_nba",          "basketball", "nba"),
    "americanfootball": ("americanfootball_nfl",     "football",   "nfl"),
    "icehockey":        ("icehockey_nhl",            "hockey",     "nhl"),
    "mma":              ("mma_mixed_martial_arts",   "mma",        "ufc"),
    "tennis":           ("tennis_atp",               "tennis",     "atp"),
}

# ── Frontmatter helpers ──────────────────────────────────────────────────────

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

# ── Data-file result helpers ─────────────────────────────────────────────────

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

async def _fetch_football_result(match_name: str, match_date: datetime) -> str | None:
    """Query football-data.org for a finished football match. Returns 'home-away' or None."""
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

async def _fetch_baseball_result(match_name: str, match_date: datetime) -> str | None:
    """Query MLB Stats API for a finished game. Returns 'home-away' (runs) or None."""
    date_str = match_date.strftime("%Y-%m-%d")
    parts = match_name.split(" vs ")
    if len(parts) != 2:
        return None
    home_frag = parts[0].strip().lower()[:6]
    away_frag = parts[1].strip().lower()[:6]

    async with httpx.AsyncClient(timeout=15) as c:
        try:
            r = await c.get(
                f"{MLB_API_BASE}/schedule",
                params={"sportId": 1, "date": date_str, "gameType": "R",
                        "hydrate": "linescore", "language": "en"},
            )
            if r.status_code != 200:
                return None
            for date_block in r.json().get("dates", []):
                for game in date_block.get("games", []):
                    if game.get("status", {}).get("abstractGameState") != "Final":
                        continue
                    api_home = game["teams"]["home"]["team"]["name"].lower()
                    api_away = game["teams"]["away"]["team"]["name"].lower()
                    if home_frag in api_home and away_frag in api_away:
                        h = game["teams"]["home"].get("score")
                        a = game["teams"]["away"].get("score")
                        if h is not None and a is not None:
                            return f"{h}-{a}"
        except httpx.RequestError:
            pass
    return None

async def _fetch_odds_api_scores(match_name: str, match_date: datetime, sport_key: str) -> str | None:
    """Fetch completed game score from The Odds API /scores endpoint.
    Returns 'home-away' or None. Works for NBA, NFL, NHL, MMA, Tennis."""
    if not ODDS_API_KEY:
        return None
    parts = match_name.split(" vs ")
    if len(parts) != 2:
        return None
    home_frag = parts[0].strip().lower()[:7]
    away_frag = parts[1].strip().lower()[:7]
    date_str = match_date.strftime("%Y-%m-%d")

    async with httpx.AsyncClient(timeout=15) as c:
        try:
            r = await c.get(
                f"{ODDS_API_BASE}/sports/{sport_key}/scores/",
                params={"apiKey": ODDS_API_KEY, "daysFrom": 3, "dateFormat": "iso"},
            )
            if r.status_code != 200:
                return None
            for game in r.json():
                if game.get("completed") is not True:
                    continue
                if date_str not in (game.get("commence_time") or ""):
                    continue
                api_home = (game.get("home_team") or "").lower()
                api_away = (game.get("away_team") or "").lower()
                if home_frag not in api_home and away_frag not in api_away:
                    continue
                scores = game.get("scores") or []
                score_map = {s["name"].lower(): s["score"] for s in scores if "name" in s}
                h = score_map.get(api_home) or score_map.get(api_home[:7])
                a = score_map.get(api_away) or score_map.get(api_away[:7])
                if h is not None and a is not None:
                    return f"{h}-{a}"
        except httpx.RequestError:
            pass
    return None

async def _fetch_espn_scores(match_name: str, match_date: datetime, espn_sport: str, espn_league: str) -> str | None:
    """Fallback: fetch score from ESPN unofficial API. No key required."""
    parts = match_name.split(" vs ")
    if len(parts) != 2:
        return None
    home_frag = parts[0].strip().lower()[:6]
    away_frag = parts[1].strip().lower()[:6]
    date_str = match_date.strftime("%Y%m%d")

    async with httpx.AsyncClient(timeout=15) as c:
        try:
            r = await c.get(
                f"{ESPN_API_BASE}/{espn_sport}/{espn_league}/scoreboard",
                params={"dates": date_str},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if r.status_code != 200:
                return None
            for event in r.json().get("events", []):
                competitors = event.get("competitions", [{}])[0].get("competitors", [])
                if len(competitors) < 2:
                    continue
                home = next((x for x in competitors if x.get("homeAway") == "home"), None)
                away = next((x for x in competitors if x.get("homeAway") == "away"), None)
                if not home or not away:
                    continue
                home_name = home.get("team", {}).get("displayName", "").lower()
                away_name = away.get("team", {}).get("displayName", "").lower()
                if home_frag not in home_name and away_frag not in away_name:
                    continue
                if event.get("status", {}).get("type", {}).get("completed") is not True:
                    continue
                h = home.get("score")
                a = away.get("score")
                if h is not None and a is not None:
                    return f"{h}-{a}"
        except httpx.RequestError:
            pass
    return None

async def _fetch_result(match_name: str, match_date: datetime, sport: str) -> str | None:
    """Dispatch result fetching to the appropriate API based on sport.

    Priority: sport-native API → The Odds API scores → ESPN fallback
    """
    s = sport.lower()
    prefix = s.split("_")[0]

    # Native APIs (most accurate)
    if prefix in ("baseball",):
        result = await _fetch_baseball_result(match_name, match_date)
        if result:
            return result

    if prefix in ("football", "soccer"):
        result = await _fetch_football_result(match_name, match_date)
        if result:
            return result

    # The Odds API scores (covers NBA, NFL, NHL, MMA, Tennis, and baseball fallback)
    endpoints = _SPORT_ENDPOINTS.get(prefix)
    if endpoints:
        odds_key, espn_sport, espn_league = endpoints
        result = await _fetch_odds_api_scores(match_name, match_date, odds_key)
        if result:
            return result
        # ESPN fallback
        return await _fetch_espn_scores(match_name, match_date, espn_sport, espn_league)

    return None

# ── Combinadas resolver ──────────────────────────────────────────────────────

def _parse_combinada_legs(text: str) -> list[dict]:
    """Extract bet legs from a combinada's markdown table.

    Expects rows like: | N | Match | Competition | Market | Odds | ... |
    Returns list of {market, odds}.
    """
    legs = []
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 7:
            continue
        try:
            int(parts[1])  # data rows start with a leg number
        except ValueError:
            continue
        market = parts[4]
        try:
            odds = float(parts[5])
        except ValueError:
            continue
        if market:
            legs.append({"market": market, "odds": odds})
    return legs

def _eval_leg(market: str, home_team: str, away_team: str, home_score: int, away_score: int) -> bool:
    """Return True if a single bet leg won given the 90-min score."""
    m = market.lower()
    total = home_score + away_score
    # Totals
    if "under 3.5" in m: return total < 4
    if "over 3.5"  in m: return total >= 4
    if "under 2.5" in m: return total < 3
    if "over 2.5"  in m: return total >= 3
    if "under 1.5" in m: return total < 2
    if "over 1.5"  in m: return total >= 2
    # Draw
    if "empate" in m or "draw" in m:
        return home_score == away_score
    # ML — identify side by team name fragment (first 8 chars)
    if away_team and away_team.lower()[:8] in m:
        return away_score > home_score
    if home_team and home_team.lower()[:8] in m:
        return home_score > away_score
    # Fallback keywords
    if "away" in m: return away_score > home_score
    if "home" in m: return home_score > away_score
    return False

def update_combinadas(match_name: str, result_score: str, combinadas_dir: Path) -> list[dict]:
    """Find pending combinadas for this match, evaluate all legs, update files.

    Returns list of resolved combinada dicts with P&L info.
    """
    if not combinadas_dir.exists():
        return []
    try:
        h, a = map(int, result_score.split("-"))
    except ValueError:
        return []

    parts = match_name.split(" vs ")
    home_team = parts[0].strip() if len(parts) >= 1 else ""
    away_team = parts[1].strip() if len(parts) >= 2 else ""
    search_term = home_team.lower()[:8]

    resolved = []
    for md_path in combinadas_dir.glob("*.md"):
        text = md_path.read_text(encoding="utf-8")
        if search_term not in text.lower():
            continue

        fm = _read_frontmatter(md_path)
        if not fm or fm.get("estado") != "pendiente":
            continue

        legs = _parse_combinada_legs(text)
        if not legs:
            continue

        won = all(_eval_leg(leg["market"], home_team, away_team, h, a) for leg in legs)
        stake = int(fm.get("stake_monetario", 1000))
        cuota = float(fm.get("cuota_combinada", 2.0))
        pnl = round(stake * (cuota - 1)) if won else -stake
        now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        # Update frontmatter
        fm["estado"] = "ganada" if won else "perdida"
        fm["resultado_score"] = result_score
        fm["pnl_cop"] = pnl
        fm["fecha_cierre"] = now_str
        _write_frontmatter(md_path, fm)

        # Update result section in body
        legs_detail = " + ".join(leg["market"] for leg in legs)
        result_block = (
            f"## Resultado\n\n"
            f"**{'✅ GANADA' if won else '❌ PERDIDA'}**\n\n"
            f"- **Score:** {result_score} ({match_name})\n"
            f"- **Legs:** {legs_detail}\n"
            f"- **P&L:** {pnl:+,} COP\n"
            f"- **Fecha cierre:** {now_str}\n"
        )
        text = md_path.read_text(encoding="utf-8")
        if "## Resultado" in text:
            text = re.sub(r"## Resultado.*", result_block, text, flags=re.DOTALL)
        else:
            text = text.rstrip() + "\n\n" + result_block
        md_path.write_text(text, encoding="utf-8")

        resolved.append({
            "ticket": md_path.stem,
            "won": won,
            "pnl_cop": pnl,
            "stake": stake,
            "cuota": cuota,
            "legs_count": len(legs),
            "legs_detail": legs_detail,
        })
        print(f"[combinadas] {md_path.stem} → {'WIN' if won else 'LOSS'} {pnl:+,} COP")

    return resolved

def update_bankroll(bankroll_path: Path, resolved_combinadas: list[dict]) -> int:
    """Apply P&L from resolved combinadas to bankroll.md. Returns new saldo."""
    if not bankroll_path.exists() or not resolved_combinadas:
        return 0

    fm = _read_frontmatter(bankroll_path)
    if not fm:
        return 0

    total_pnl = sum(c["pnl_cop"] for c in resolved_combinadas)
    old_saldo = int(fm.get("saldo_actual", 0))
    new_saldo = old_saldo + total_pnl
    today = datetime.now().strftime("%Y-%m-%d")

    # Update frontmatter
    fm["saldo_actual"] = new_saldo
    fm["ultima_actualizacion"] = today
    fm.pop("nota_pendientes", None)
    _write_frontmatter(bankroll_path, fm)

    # Build historial entries
    running = old_saldo
    historial_lines = []
    for c in resolved_combinadas:
        running += c["pnl_cop"]
        won_str = "GANADA" if c["won"] else "PERDIDA"
        label = (
            c["ticket"].split(" - ", 2)[-1][:55]
            if " - " in c["ticket"]
            else c["ticket"][:55]
        )
        historial_lines.append(
            f"- **{today}** — {running:,} COP ({c['pnl_cop']:+,}, {label} {c['legs_count']}L {won_str})"
        )
    entry = "\n".join(historial_lines)

    # Insert historial entries + patch saldo display in body
    text = bankroll_path.read_text(encoding="utf-8")
    MARKER = "## Historial de saldo"
    if MARKER in text:
        idx = text.index(MARKER) + len(MARKER)
        nl = text.index("\n", idx)
        text = text[:nl + 1] + "\n" + entry + "\n" + text[nl + 1:]

    text = re.sub(r"\*\*Bankroll total:\*\* [\d.,]+ COP", f"**Bankroll total:** {new_saldo:,} COP", text)
    text = re.sub(r"\| Saldo actual \|[^|]+\|", f"| Saldo actual | **{new_saldo:,} COP** |", text)
    text = re.sub(
        r"\*\*Fecha actualización:\*\* \d{4}-\d{2}-\d{2}",
        f"**Fecha actualización:** {today}",
        text,
    )
    bankroll_path.write_text(text, encoding="utf-8")
    print(f"[bankroll] {old_saldo:,} → {new_saldo:,} COP ({total_pnl:+,})")
    return new_saldo

# ── Weekly stats ─────────────────────────────────────────────────────────────

def compute_weekly_stats(data_dir: Path, days: int = 7) -> dict:
    """Aggregate PnL stats across all sport subdirectories under data_dir's parent,
    or just data_dir if no subdirs exist. Covers football + baseball + all sports."""
    # Support both Data/football/ (single dir) and Data/ (root with sport subdirs)
    dirs_to_scan: list[Path] = []
    if data_dir.exists():
        subdirs = [d for d in data_dir.iterdir() if d.is_dir()]
        if subdirs:
            dirs_to_scan = subdirs          # Data/ root — scan all sport dirs
        else:
            dirs_to_scan = [data_dir]       # Data/football/ — legacy single-sport

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    total = won_count = 0
    pnl = 0.0
    fair_probs: list[float] = []
    by_sport: dict[str, dict] = {}

    for scan_dir in dirs_to_scan:
        sport_label = scan_dir.name
        s_total = s_won = 0
        s_pnl = 0.0

        for md_path in scan_dir.glob("*.md"):
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
            s_total += 1
            if fm.get("won"):
                won_count += 1
                s_won += 1
            edge_pnl = fm.get("pnl_units", -1.0)
            pnl += edge_pnl
            s_pnl += edge_pnl
            if fp := fm.get("fair_prob"):
                fair_probs.append(float(fp))

        if s_total:
            by_sport[sport_label] = {
                "total": s_total,
                "won": s_won,
                "hit_rate": round(s_won / s_total, 4),
                "pnl_units": round(s_pnl, 4),
            }

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
        "by_sport": by_sport,
    }

# ── Main result checker ──────────────────────────────────────────────────────

async def check_pending_results(
    data_dir: Path,
    combinadas_dir: Path | None = None,
    bankroll_path: Path | None = None,
) -> list[dict]:
    """Scan ALL sport directories under data_dir (or data_dir itself) for unchecked edges.

    For each resolved match:
    - Updates data file frontmatter (won / pnl_units / result_score / checked_at)
    - Uses sport-appropriate API (football-data.org for football, MLB Stats for baseball)
    - Resolves matching pending combinadas
    - Updates bankroll.md
    - Sends Telegram result alert
    """
    if not data_dir.exists():
        return []

    # Determine which directories to scan
    subdirs = [d for d in data_dir.iterdir() if d.is_dir()] if data_dir.is_dir() else []
    scan_dirs = subdirs if subdirs else [data_dir]

    now = datetime.now(timezone.utc)
    resolved_data: list[dict] = []
    all_resolved_combinadas: list[dict] = []

    for scan_dir in scan_dirs:
        for md_path in scan_dir.glob("*.md"):
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

            sport = fm.get("sport", "football")
            match_name = fm.get("match", "")
            score = await _fetch_result(match_name, match_date, sport)
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

            resolved_data.append({
                "match": match_name,
                "sport": sport,
                "market": fm.get("market"),
                "won": won,
                "pnl_units": pnl,
                "result_score": score,
                "paper": fm.get("modo_simulacion", False),
            })
            mode = "[PAPER]" if fm.get("modo_simulacion") else "[REAL]"
            print(f"[results] {mode} {match_name} — {fm.get('market')} → {'WIN' if won else 'LOSS'} {pnl:+.2f}u")

            # Resolve combinadas for this match (real bets only)
            if combinadas_dir and not fm.get("modo_simulacion"):
                sport_prefix = sport.split("_")[0]
                sport_combinadas = combinadas_dir / sport_prefix
                resolved_combis = update_combinadas(match_name, score, sport_combinadas)
                all_resolved_combinadas.extend(resolved_combis)

    # Update bankroll (real bets only)
    new_saldo = 0
    if bankroll_path and all_resolved_combinadas:
        new_saldo = update_bankroll(bankroll_path, all_resolved_combinadas)

    # Send Telegram alert if anything resolved
    if resolved_data or all_resolved_combinadas:
        from notifier import send_telegram, format_result_alert
        if new_saldo == 0 and bankroll_path and bankroll_path.exists():
            fm_br = _read_frontmatter(bankroll_path)
            new_saldo = int(fm_br.get("saldo_actual", 0))
        msg = format_result_alert(resolved_data, all_resolved_combinadas, new_saldo)
        await send_telegram(msg)

    return resolved_data
