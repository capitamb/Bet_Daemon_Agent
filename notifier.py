import os
import httpx
from models import Match, EdgeResult

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SIGNAL_EMOJI = {"baja": "🟡", "media": "🟠", "alta": "🔴"}

def format_football_alert(match: Match, edge: EdgeResult) -> str:
    market_label = {
        "home": f"{match.home_team} ML",
        "draw": "Empate",
        "away": f"{match.away_team} ML",
    }[edge.market]

    lines = [
        "🚨 EDGE DETECTADO",
        f"⚽ Fútbol | {match.competition}",
        f"{match.home_team} vs {match.away_team}",
        f"📊 {market_label} @ {edge.odds:.2f}",
        f"Edge: +{edge.edge:.1%} | Signal: {edge.signal.upper()} {SIGNAL_EMOJI[edge.signal]}",
        f"Fair: {edge.fair_prob:.1%} | Impl: {edge.implied_prob:.1%}",
    ]
    if match.injuries:
        lines.append(f"🏥 {match.injuries}")
    lines.append("→ Pide análisis completo a Claude")
    return "\n".join(lines)

async def send_telegram(message: str) -> bool:
    """Send message via Telegram Bot API. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[ALERT — Telegram not configured]\n{message}")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
            })
            return r.status_code == 200
        except httpx.RequestError:
            return False
