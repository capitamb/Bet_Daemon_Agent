"""
Apuestas Daemon — main entry point.
FastAPI app + APScheduler.
Runs football pipeline every 15 minutes.
Exposes local API on localhost:8001.
"""
import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

from config import FOOTBALL_CONFIG, SCHEDULE_INTERVAL_MINUTES, DAEMON_PORT
from edge_detector import detect_edges
from models import Match
from notifier import format_football_alert, send_telegram
from poisson_model import fair_probs_from_xg
from scrapers.fixtures import fetch_upcoming_fixtures
from scrapers.odds import enrich_matches_with_odds
from scrapers.xg import find_team_xg
from vault_writer import write_edge_alert

# ─────────────────────────────────────────────────────
# State (in-memory, refreshed each cycle)
# ─────────────────────────────────────────────────────
state = {
    "last_run": None,
    "edges_today": [],
    "status": "starting",
}

# ─────────────────────────────────────────────────────
# Football pipeline
# ─────────────────────────────────────────────────────
async def run_football_pipeline() -> None:
    print(f"[{datetime.now():%H:%M:%S}] Running football pipeline...")
    state["status"] = "running"

    try:
        # 1. Fetch fixtures
        matches = await fetch_upcoming_fixtures(hours_ahead=48)
        print(f"  -> {len(matches)} upcoming fixtures")

        # 2. Enrich with odds
        matches = await enrich_matches_with_odds(matches)
        with_odds = [m for m in matches if m.odds_home]
        print(f"  -> {len(with_odds)} have odds")

        # 3. Enrich with xG + compute fair_prob
        for match in with_odds:
            home_xg = await find_team_xg(match.home_team, match.competition)
            away_xg = await find_team_xg(match.away_team, match.competition)
            if home_xg and away_xg:
                ph, pd, pa = fair_probs_from_xg(home_xg, away_xg)
                match.fair_prob_home = ph
                match.fair_prob_draw = pd
                match.fair_prob_away = pa

        with_probs = [m for m in with_odds if m.fair_prob_home]
        print(f"  -> {len(with_probs)} have xG + fair_prob")

        # 4. Detect edges + alert
        new_edges = []
        cfg = FOOTBALL_CONFIG
        for match in with_probs:
            edges = detect_edges(match, min_edge=cfg.min_edge, min_signal=cfg.min_signal)
            for edge in edges:
                if not edge.qualifies:
                    continue
                path = write_edge_alert(match, edge, data_dir=cfg.data_dir)
                print(f"  [EDGE] {match.home_team} vs {match.away_team} -- {edge.market} edge={edge.edge:.1%}")
                msg = format_football_alert(match, edge)
                await send_telegram(msg)
                new_edges.append({
                    "match": f"{match.home_team} vs {match.away_team}",
                    "market": edge.market,
                    "edge": edge.edge,
                    "signal": edge.signal,
                    "file": str(path),
                })

        state["edges_today"].extend(new_edges)
        state["last_run"] = datetime.now().isoformat()
        state["status"] = "idle"
        print(f"  [OK] Pipeline complete. {len(new_edges)} new edges.")

    except Exception as e:
        state["status"] = "error"
        print(f"  [ERROR] Pipeline error: {e}")
        raise

# ─────────────────────────────────────────────────────
# FastAPI app
# ─────────────────────────────────────────────────────
scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run once immediately on startup
    asyncio.create_task(run_football_pipeline())
    # Schedule every N minutes
    scheduler.add_job(
        run_football_pipeline,
        "interval",
        minutes=SCHEDULE_INTERVAL_MINUTES,
        id="football_pipeline",
    )
    scheduler.start()
    yield
    scheduler.shutdown()

app = FastAPI(title="Apuestas Daemon", lifespan=lifespan)

@app.get("/health")
def health():
    return {"status": "ok", "daemon_status": state["status"]}

@app.get("/status")
def status():
    return {
        "last_run": state["last_run"],
        "status": state["status"],
        "edges_today_count": len(state["edges_today"]),
        "edges_today": state["edges_today"],
        "schedule_interval_minutes": SCHEDULE_INTERVAL_MINUTES,
    }

@app.post("/run-now")
async def run_now():
    """Trigger pipeline immediately (for on-demand refresh from agents)."""
    asyncio.create_task(run_football_pipeline())
    return {"message": "pipeline triggered"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=DAEMON_PORT, reload=False)
