"""
Apuestas Daemon — main entry point.
FastAPI + APScheduler. Runs football + multi-sport pipeline every 15 minutes.
Exposes local API on localhost:8001.
"""
import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

load_dotenv()

from config import (
    FOOTBALL_CONFIG, SCHEDULE_INTERVAL_MINUTES, DAEMON_PORT,
    CUP_COMPETITIONS, VAULT_ROOT, get_sport_threshold,
)
from edge_detector import detect_edges
from models import Match, EdgeResult
from notifier import send_alerts_for_cycle, format_results_summary, send_telegram
from poisson_model import fair_probs_from_xg
from scrapers.fixtures import fetch_upcoming_fixtures
from scrapers.news import get_news_signal
from scrapers.odds import enrich_matches_with_odds, fetch_sport_events
from scrapers.polymarket import get_any_sport_signal
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

# Live activity log — last 50 events, pushed via SSE
activity_log: list[dict] = []

def _log(event_type: str, message: str, data: dict | None = None) -> None:
    """Append to activity log and keep last 50 entries."""
    entry = {
        "ts": datetime.now().strftime("%H:%M:%S"),
        "type": event_type,   # "cycle" | "edge" | "result" | "alert" | "error"
        "msg": message,
        "data": data or {},
    }
    activity_log.append(entry)
    if len(activity_log) > 50:
        activity_log.pop(0)

# ─── Football pipeline ───────────────────────────────────────────────────────
async def run_football_pipeline() -> list[tuple[Match, EdgeResult]]:
    print(f"[{datetime.now():%H:%M:%S}] Running football pipeline...")

    matches = await fetch_upcoming_fixtures(hours_ahead=120)
    print(f"  -> {len(matches)} upcoming fixtures")

    matches = await enrich_matches_with_odds(matches)
    with_odds = [m for m in matches if m.odds_home]
    print(f"  -> {len(with_odds)} have odds")

    # xG / Poisson for domestic leagues; consensus already set for cups
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

    # Enrich with news signals
    for match in with_probs:
        match.news_signal = await get_news_signal(
            [match.home_team, match.away_team], sport="football"
        )

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
    qualifying: list[tuple[Match, EdgeResult]] = []

    for sport_key in sport_keys:
        min_edge, min_signal = get_sport_threshold(sport_key)
        sport_prefix = sport_key.split("_")[0]
        sport_data_dir = VAULT_ROOT / "Data" / sport_prefix
        events = await fetch_sport_events(sport_key)
        for match in events:
            # Enrich with news + Polymarket signals
            match.news_signal = await get_news_signal(
                [match.home_team, match.away_team], sport=sport_key
            )
            match.polymarket_signal = await get_any_sport_signal(
                match.home_team, match.away_team, sport_key
            )
            for edge in detect_edges(match, min_edge=min_edge, min_signal=min_signal):
                if not edge.qualifies:
                    continue
                write_edge_alert(match, edge, data_dir=sport_data_dir)
                print(
                    f"  [EDGE] {sport_key}: {match.home_team} vs {match.away_team} "
                    f"— {edge.market} edge={edge.edge:.1%}"
                    + (f" | poly={match.polymarket_signal}" if match.polymarket_signal else "")
                )
                qualifying.append((match, edge))

    return qualifying

# ─── Combined cycle ──────────────────────────────────────────────────────────
async def run_full_pipeline() -> None:
    state["status"] = "running"
    _log("cycle", "Iniciando ciclo completo...")
    try:
        football_edges = await run_football_pipeline()
        other_edges = await run_other_sports_pipeline()
        all_edges = football_edges + other_edges

        if all_edges:
            await send_alerts_for_cycle(all_edges)
            for m, e in all_edges:
                _log("edge", f"{m.home_team} vs {m.away_team} — {e.market} +{e.edge:.1%} @{e.odds}",
                     {"sport": m.sport, "edge": e.edge, "odds": e.odds, "signal": e.signal})
            _log("alert", f"{len(all_edges)} edge(s) enviados a Telegram")
        else:
            _log("cycle", "Sin edges nuevos sobre threshold")

        state["edges_today"].extend([
            {
                "match": f"{m.home_team} vs {m.away_team}",
                "sport": m.sport,
                "market": e.market,
                "edge": round(e.edge, 4),
                "odds": e.odds,
                "news": m.news_signal[:60] if m.news_signal else "",
                "polymarket": m.polymarket_signal or "",
            }
            for m, e in all_edges
        ])
        state["last_run"] = datetime.now().isoformat()
        state["status"] = "idle"
        _log("cycle", f"Ciclo completo — {len(all_edges)} edges nuevos")
        print(f"  [OK] Cycle complete. {len(all_edges)} new edges.")
    except Exception as exc:
        state["status"] = "error"
        _log("error", f"Pipeline error: {exc}")
        print(f"  [ERROR] Pipeline error: {exc}")
        raise

# ─── Results checker jobs ────────────────────────────────────────────────────
async def run_results_check() -> None:
    _log("cycle", "Verificando resultados pendientes...")
    resolved = await check_pending_results(
        VAULT_ROOT / "Data",
        combinadas_dir=VAULT_ROOT / "Combinadas",
        bankroll_path=VAULT_ROOT / "bankroll.md",
    )
    for r in resolved:
        icon = "✅" if r["won"] else "❌"
        mode = "[PAPER]" if r.get("paper") else "[REAL]"
        _log("result", f"{icon} {mode} {r['match']} — {r['result_score']} | {r['pnl_units']:+.2f}u",
             {"won": r["won"], "pnl": r["pnl_units"], "sport": r.get("sport", "")})
    if resolved:
        print(f"[results] Resolved {len(resolved)} pending edges")

async def run_weekly_summary() -> None:
    stats = compute_weekly_stats(FOOTBALL_CONFIG.data_dir)
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8002", "http://127.0.0.1:8002"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ─── Endpoints ───────────────────────────────────────────────────────────────

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
    """Trigger full pipeline immediately."""
    asyncio.create_task(run_full_pipeline())
    return {"message": "pipeline triggered"}

@app.post("/check-results-now")
async def check_results_now_endpoint():
    """Resolve pending results across all sports, update combinadas + bankroll, send Telegram."""
    asyncio.create_task(
        check_pending_results(
            VAULT_ROOT / "Data",
            combinadas_dir=VAULT_ROOT / "Combinadas",
            bankroll_path=VAULT_ROOT / "bankroll.md",
        )
    )
    return {"message": "result check triggered"}

@app.get("/stats")
def stats(days: int = 30):
    """Calibration stats aggregated across ALL sport directories."""
    return compute_weekly_stats(VAULT_ROOT / "Data", days=days)

@app.get("/activity")
def activity():
    """Last 50 live activity events."""
    return list(reversed(activity_log))

@app.get("/events")
async def events():
    """SSE stream — pushes state every 3 seconds for live dashboard."""
    async def generator():
        last_count = -1
        while True:
            current_count = len(activity_log)
            if current_count != last_count:
                payload = json.dumps({
                    "status": state["status"],
                    "last_run": state["last_run"],
                    "edges_count": len(state["edges_today"]),
                    "activity": list(reversed(activity_log))[:15],
                    "ts": datetime.now().strftime("%H:%M:%S"),
                })
                yield f"data: {payload}\n\n"
                last_count = current_count
            else:
                # Heartbeat every 3s to keep connection alive
                yield f"data: {json.dumps({'heartbeat': True, 'status': state['status'], 'ts': datetime.now().strftime('%H:%M:%S')})}\n\n"
            await asyncio.sleep(3)
    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.get("/bankroll")
def bankroll():
    """Parse bankroll.md and return equity history for the dashboard."""
    path = VAULT_ROOT / "bankroll.md"
    if not path.exists():
        return {"saldo_actual": 0, "saldo_inicial": 0, "historia": []}

    text = path.read_text(encoding="utf-8")

    # Extract frontmatter values
    fm_saldo = 0
    fm_inicial = 0
    if text.startswith("---"):
        try:
            import yaml
            end = text.index("---", 3)
            fm = yaml.safe_load(text[3:end]) or {}
            fm_saldo = fm.get("saldo_actual", 0)
            fm_inicial = fm.get("saldo_inicial", 0)
        except Exception:
            pass

    # Parse historial lines: "- **YYYY-MM-DD** — 117.746 COP (...)"
    historia: list[dict] = []
    pattern = re.compile(r"\*\*(\d{4}-\d{2}-\d{2})\*\*\s*[—-]\s*([\d.,]+)\s*COP")
    for match in pattern.finditer(text):
        date_str, saldo_str = match.group(1), match.group(2)
        try:
            saldo = int(saldo_str.replace(".", "").replace(",", ""))
            historia.append({"date": date_str, "saldo": saldo})
        except ValueError:
            continue

    return {
        "saldo_actual": fm_saldo,
        "saldo_inicial": fm_inicial,
        "historia": historia,
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=DAEMON_PORT, reload=False)
