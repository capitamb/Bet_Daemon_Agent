from pathlib import Path
from dataclasses import dataclass

VAULT_ROOT = Path(r"C:\Users\cmene\OneDrive\Documents\Brain Proyect Claude\Second Brain\Apuestas")
DAEMON_PORT = 8001
LOOKAHEAD_HOURS = 48
SCHEDULE_INTERVAL_MINUTES = 15

@dataclass(frozen=True)
class SportConfig:
    name: str
    min_edge: float        # minimum edge fraction (e.g. 0.06 = 6%)
    min_signal: str        # "baja" | "media" | "alta"
    data_dir: Path

SIGNAL_RANK = {"baja": 1, "media": 2, "alta": 3}

FOOTBALL_CONFIG = SportConfig(
    name="football",
    min_edge=0.06,
    min_signal="media",
    data_dir=VAULT_ROOT / "Data" / "football",
)

# football-data.org competition IDs mapped to display names
FOOTBALL_COMPETITIONS = {
    "PL":  "Premier League",
    "PD":  "La Liga",
    "SA":  "Serie A",
    "BL1": "Bundesliga",
    "FL1": "Ligue 1",
    "CL":  "Champions League",
    "EL":  "Europa League",
}

# The Odds API sport keys for football
ODDS_API_FOOTBALL_SPORTS = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
]

# Understat league names
UNDERSTAT_LEAGUES = {
    "Premier League": "EPL",
    "La Liga": "La_liga",
    "Serie A": "Serie_A",
    "Bundesliga": "Bundesliga",
    "Ligue 1": "Ligue_1",
}
