import os
import httpx
from models import Match, EdgeResult
from config import KELLY_FRACTION

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SIGNAL_EMOJI = {"baja": "🟡", "media": "🟠", "alta": "🔴"}

_SPORT_EMOJI: dict[str, str] = {
    "football": "⚽",
    "basketball": "🏀",
    "americanfootball": "🏈",
    "baseball": "⚾",
    "tennis": "🎾",
    "icehockey": "🏒",
    "soccer": "⚽",
}

def _sport_emoji(sport: str) -> str:
    for prefix, emoji in _SPORT_EMOJI.items():
        if sport.startswith(prefix):
            return emoji
    return "🎯"

def format_sport_alert(match: Match, edge: EdgeResult) -> str:
    market_label = {
        "home": f"{match.home_team} ML",
        "draw": "Empate",
        "away": f"{match.away_team} ML",
    }[edge.market]

    emoji = _sport_emoji(match.sport)
    lines = [
        "🚨 EDGE DETECTADO",
        f"{emoji} {match.competition}",
        f"{match.home_team} vs {match.away_team}",
        f"📊 {market_label} @ {edge.odds:.2f}",
        f"Edge: +{edge.edge:.1%} | Signal: {edge.signal.upper()} {SIGNAL_EMOJI[edge.signal]}",
        f"Fair: {edge.fair_prob:.1%} | Impl: {edge.implied_prob:.1%}",
    ]
    if match.injuries:
        lines.append(f"🏥 {match.injuries}")
    lines.append("→ Pide análisis completo a Claude")
    return "\n".join(lines)

# Backwards-compatible alias
format_football_alert = format_sport_alert

def format_combinada_alert(edges_with_matches: list[tuple[Match, EdgeResult]]) -> str:
    n = len(edges_with_matches)
    lines = [f"🎯 COMBINADA — {n} legs"]

    odds_product = 1.0
    p_win = 1.0

    for i, (match, edge) in enumerate(edges_with_matches, 1):
        market_label = {
            "home": f"{match.home_team} ML",
            "draw": "Empate",
            "away": f"{match.away_team} ML",
        }[edge.market]
        lines.append(f"Leg {i}: {market_label} @ {edge.odds:.2f} ({match.home_team} vs {match.away_team} — {match.competition})")
        odds_product *= edge.odds
        p_win *= edge.fair_prob

    if odds_product > 1:
        kelly_full = (odds_product * p_win - 1) / (odds_product - 1)
        kelly_stake = max(0.0, kelly_full * KELLY_FRACTION)
    else:
        kelly_stake = 0.0

    lines.append(f"Odd total: {odds_product:.2f} | Kelly 25%: {kelly_stake:.1%} bankroll")
    lines.append(f"Fair p_win: {p_win:.1%}")
    return "\n".join(lines)

def format_results_summary(stats: dict) -> str:
    lines = [
        "📊 RESUMEN SEMANAL — Apuestas Daemon",
        f"Período: últimos {stats.get('period_days', 7)} días",
        f"Edges: {stats.get('total_edges', 0)} | Ganados: {stats.get('won', 0)} | Perdidos: {stats.get('lost', 0)}",
        f"Hit rate: {stats.get('hit_rate', 0):.1%} | PnL: {stats.get('pnl_units', 0):+.2f}u",
        f"Fair prob prom: {stats.get('avg_fair_prob', 0):.1%} | Gap modelo: {stats.get('model_gap', 0):+.1%}",
    ]
    gap = stats.get("model_gap", 0)
    if abs(gap) > 0.05:
        direction = "sobreestimando" if gap > 0 else "subestimando"
        lines.append(f"⚠️ Gap significativo — {direction} edge real")
    return "\n".join(lines)

async def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[ALERT — Telegram not configured]\n{message}")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            r = await c.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message})
            return r.status_code == 200
        except httpx.RequestError:
            return False

async def send_alerts_for_cycle(edges_with_matches: list[tuple[Match, EdgeResult]]) -> None:
    """Send one individual alert per edge, then a combinada if 2+ edges."""
    for match, edge in edges_with_matches:
        await send_telegram(format_sport_alert(match, edge))
    if len(edges_with_matches) >= 2:
        await send_telegram(format_combinada_alert(edges_with_matches))
