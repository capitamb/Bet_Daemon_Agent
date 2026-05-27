from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass
class TeamXG:
    team: str
    xg_scored_avg: float    # avg xG scored per game (last 5)
    xg_conceded_avg: float  # avg xG conceded per game (last 5)
    games_sampled: int

@dataclass
class Match:
    home_team: str
    away_team: str
    competition: str
    date: datetime
    sport: str = "football"
    # Odds (decimal)
    odds_home: Optional[float] = None
    odds_draw: Optional[float] = None
    odds_away: Optional[float] = None
    # xG averages
    home_xg: Optional[TeamXG] = None
    away_xg: Optional[TeamXG] = None
    # Computed fair probabilities
    fair_prob_home: Optional[float] = None
    fair_prob_draw: Optional[float] = None
    fair_prob_away: Optional[float] = None
    # Context
    injuries: str = ""
    line_movement: str = ""
    news_signal: str = ""       # breaking news: injuries, suspensions (from RSS)
    polymarket_signal: str = "" # Polymarket implied prob (MMA only, price discovery)

@dataclass
class EdgeResult:
    market: str             # "home" | "draw" | "away"
    odds: float
    implied_prob: float
    fair_prob: float
    edge: float             # fair_prob - implied_prob
    signal: str             # "baja" | "media" | "alta"
    qualifies: bool         # edge >= min_edge and signal >= min_signal
