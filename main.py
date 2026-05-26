"""
Apuestas Daemon — main entry point.
FastAPI + APScheduler. Runs football + multi-sport pipeline every 15 minutes.
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

from config import (
    FOOTBALL_CONFIG, SCHEDULE_INTERVAL_MINUTES, DAEMON_PORT,
    CUP_COMPETITIONS,
)
from edge_detector import detect_edges
from models import Match, EdgeResult
from notifier import send_alerts_for_cycle, format_results_summary, send_telegram
from poisson_model import fair_probs_from_xg
from scrapers.fixtures import fetch_upcoming_fixtures
from scrapers.odds import enrich_matches_with_odds, fetch_sport_events
from scrapers.sports import get_active_sports
from scrapers.xg import find_team_xg
from results_checker import check_pending_results, compute_weekly_stats
from vault_writer import write_edge_alert

# ─── State ──────────────────────────────────────────────────────────────────
state = {
    "last_run": None,
    "edges_today": [],
    "status": "starting",
}

# ─── Football pipeline ───────────────────────────────────────────────────────
async def run_football_pipeline() -> list[tuple[Match, EdgeResult]]:
    print(f"[{datetime.now():%H:%M:%S}] Running football pipeline...")

    matches = await fetch_upcoming_fixtures(hours_ahead=120)
    print(f"  -> {len(matches)} upcoming fixtures")

    matches = await enrich_matches_with_odds(matches)
    with_odds = [m for m in matches if m.odds_home]
    print(f"  -> {len(with_odds)} have odds")

    # xG / Poisson for domestic leagues; consensus already set for cups in enrich step
    for match in with_odds:
        if match.competition in CUP_COMPETITIONS:
            continue
        home_xg = await find_team_xg(match.home_team, match.competition)
        away_xg = await find_team_xg(match.away_team, match.competition)
        if home_xg and away_xg:
            ph, pd, pa = fair_probs_from_xg(home_xg, away_xg)
            match.fair_prob_home = ph
            match.fair_prob_draw = pd
            match.fair_prob_away = pa

    with_probs = [m for m in with_odds if m.fair_prob_home]
    print(f"  -> {len(with_probs)} have fair_prob")

    cfg = FOOTBALL_CONFIG
    qualifying: list[tuple[Match, EdgeResult]] = []
    for match in with_probs:
        for edge in detect_edges(match, min_edge=cfg.min_edge, min_signal=cfg.min_signal):
            if not edge.qualifies:
                continue
            write_edge_alert(match, edge, data_dir=cfg.data_dir)
            print(f"  [EDGE] {match.home_team} vs {match.away_team} — {edge.market} edge={edge.edge:.1%}")
            qualifying.append((match, edge))

    return qualifying

# ─── Other sports pipeline ───────────────────────────────────────────────────
async def run_other_sports_pipeline() -> list[tuple[Match, EdgeResult]]:
    sport_keys = await get_active_sports()
    if not sport_keys:
        return []

    print(f"[{datetime.now():%H:%M:%S}] Running other-sports pipeline ({len(sport_keys)} sports)...")
    cfg = FOOTBALL_CONFIG
    qualifying: list[tuple[Match, EdgeResult]] = []

    for sport_key in sport_keys:
        events = await fetch_sport_events(sport_key)
        for match in events:
            for edge in detect_edges(match, min_edge=cfg.min_edge, min_signal=cfg.min_signal):
                if not edge.qualifies:
                    continue
                print(f"  [EDGE] {sport_key}: {match.home_team} vs {match.away_team} — {edge.market} edge={edge.edge:.1%}")
                qualifying.append((match, edge))

    return qualifying

# ─── Combined cycle ──────────────────────────────────────────────────────────
async def run_full_pipeline() -> None:
    state["status"] = "running"
    try:
        football_edges = await run_football_pipeline()
        other_edges = await run_other_sports_pipeline()
        all_edges = football_edges + other_edges

        if all_edges:
            await send_alerts_for_cycle(all_edges)

        state["edges_today"].extend([
            {"match": f"{m.home_team} vs {m.away_team}", "sport": m.sport,
             "market": e.market, "edge": round(e.edge, 4), "odds": e.odds}
            for m, e in all_edges
        ])
        state["last_run"] = datetime.now().isoformat()
        state["status"] = "idle"
        print(f"  [OK] Cycle complete. {len(all_edges)} new edges.")
    except Exception as exc:
        state["status"] = "error"
        print(f"  [ERROR] Pipeline error: {exc}")
        raise

# ─── Results checker jobs ────────────────────────────────────────────────────
async def run_results_check() -> None:
    cfg = FOOTBALL_CONFIG
    resolved = await check_pending_results(cfg.data_dir)
    if resolved:
        print(f"[results] Resolved {len(resolved)} pending edges")

async def run_weekly_summary() -> None:
    cfg = FOOTBALL_CONFIG
    stats = compute_weekly_stats(cfg.data_dir)
    if stats:
        await send_telegram(format_results_summary(stats))

# ─── FastAPI app ─────────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(run_full_pipeline())
    scheduler.add_job(run_full_pipeline, "interval", minutes=SCHEDULE_INTERVAL_MINUTES, id="full_pipeline")
    scheduler.add_job(run_results_check, "interval", hours=6, id="results_check")
    scheduler.add_job(run_weekly_summary, "cron", day_of_week="mon", hour=9, minute=0, id="weekly_summary")
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
    """Trigger pipeline immediately."""
    asyncio.create_task(run_full_pipeline())
    return {"message": "pipeline triggered"}

@app.get("/stats")
def stats():
    """Return accumulated calibration stats from checked edge files."""
    cfg = FOOTBALL_CONFIG
    return compute_weekly_stats(cfg.data_dir, days=30)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=DAEMON_PORT, reload=False)
