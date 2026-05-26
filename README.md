# Apuestas Daemon v2

Betting intelligence daemon for football + multi-sport. Detects value edges against Pinnacle odds, sends Telegram alerts with individual picks and Kelly-sized parlays, and tracks results with a PnL feedback loop for model calibration.

---

## What It Does

- **Football pipeline** — fetches upcoming fixtures from football-data.org (top 5 leagues + UCL/UEL), enriches with Pinnacle odds from The Odds API, computes fair probabilities via Poisson/Dixon-Robinson xG model (Sofascore data for domestic leagues, consensus for cups), detects edges ≥ 6%, saves alert files to vault.
- **Multi-sport pipeline** — auto-discovers all active sports on The Odds API (NBA, NFL, tennis, MMA, etc.), builds consensus fair_prob from all available bookmakers, detects edges using same threshold.
- **Telegram alerts** — individual alert per edge + combinada (parlay) when 2 or more edges qualify in the same cycle, with quarter-Kelly stake sizing.
- **Results checker** — every 6h resolves pending edge files against football-data.org results, writes PnL (units won/lost per edge), tracks hit rate and model gap.
- **Weekly summary** — Monday 9am Telegram message with total edges, hit rate, PnL units, and avg fair_prob vs real hit rate (model calibration gap).
- **REST API** — local endpoints to check status, trigger pipeline, and view accumulated stats.

---

## Architecture

```
main.py                   FastAPI + APScheduler entry point
├── scrapers/
│   ├── fixtures.py       football-data.org upcoming fixtures
│   ├── odds.py           The Odds API — Pinnacle odds + consensus fair_prob
│   ├── xg.py             Sofascore internal API — rolling 10-match xG per team
│   └── sports.py         auto-discovery of active non-soccer sports (24h cache)
├── poisson_model.py      Dixon-Robinson Poisson — xG → fair probabilities
├── edge_detector.py      compares fair_prob vs Pinnacle implied, emits EdgeResult
├── notifier.py           formats + sends Telegram alerts (individual + combinada)
├── results_checker.py    resolves pending edges, computes weekly stats
├── vault_writer.py       writes edge alerts as YAML-frontmatter .md files
├── models.py             Match, EdgeResult, TeamXG dataclasses
└── config.py             thresholds, sport keys, tournament IDs
```

**Scheduler jobs:**
| Job | Frequency |
|-----|-----------|
| Full pipeline (football + all sports) | every 15 min |
| Results checker | every 6 h |
| Weekly PnL summary (Telegram) | Monday 09:00 |

**Edge detection logic:**
1. Compute fair probability (Poisson from xG for domestic leagues; consensus vig-removed average for cups and all other sports)
2. Compare against Pinnacle implied probability: `edge = fair_prob - 1/pinnacle_odds`
3. Qualify if `edge ≥ 6%` and signal ≥ `"media"`

**Combinada (parlay) Kelly sizing:**
```
odds_combined = Π(pinnacle_odds per leg)
p_win         = Π(fair_prob per leg)
kelly_full    = (odds_combined × p_win − 1) / (odds_combined − 1)
kelly_stake   = max(0, kelly_full × 0.25)   # quarter Kelly
```

---

## APIs Used

| Service | Purpose | Free tier |
|---------|---------|-----------|
| [football-data.org](https://www.football-data.org) | Fixtures, results | 10 req/min, top competitions |
| [The Odds API](https://the-odds-api.com) | Pinnacle odds + all sports | 500 req/month |
| [Sofascore](https://sofascore.com) | Team xG (internal API) | No auth required |
| Telegram Bot API | Alerts | Free |

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/capitamb/apuestas-daemon.git
cd apuestas-daemon
```

Requires [uv](https://docs.astral.sh/uv/):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
```

### 2. Environment variables

Copy `.env.example` and fill in your keys:

```bash
cp .env.example .env
```

```env
FOOTBALL_DATA_API_KEY=your_football_data_key
THE_ODDS_API_KEY=your_odds_api_key
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_numeric_chat_id
```

- **football-data.org** — free at [football-data.org/client](https://www.football-data.org/client)
- **The Odds API** — free tier at [the-odds-api.com](https://the-odds-api.com)
- **Telegram** — create a bot via [@BotFather](https://t.me/BotFather), get your chat ID via [@userinfobot](https://t.me/userinfobot)

### 3. Configure vault path

Edit `config.py` and set `VAULT_ROOT` to where you want edge alert files saved:

```python
VAULT_ROOT = Path("/your/path/to/vault")
```

Edge files are written to `VAULT_ROOT/Data/football/`.

### 4. Run

**Development:**
```bash
uv run python main.py
```

**Production (PM2):**
```bash
npm install -g pm2
pm2 start ecosystem.config.cjs
pm2 save
pm2 startup   # auto-start on reboot
```

---

## API Endpoints

All endpoints on `http://localhost:8001`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check |
| `GET` | `/status` | Last run time, edge count, pipeline status |
| `POST` | `/run-now` | Trigger full pipeline immediately |
| `GET` | `/stats` | Accumulated PnL stats (last 30 days) |

**`/status` response:**
```json
{
  "last_run": "2026-05-26T15:30:00",
  "status": "idle",
  "edges_today_count": 3,
  "edges_today": [...],
  "schedule_interval_minutes": 15
}
```

**`/stats` response:**
```json
{
  "period_days": 30,
  "total_edges": 42,
  "won": 18,
  "lost": 20,
  "pending": 4,
  "hit_rate": 0.47,
  "pnl_units": 3.2,
  "avg_fair_prob": 0.52,
  "model_gap": -0.05
}
```

---

## Tests

```bash
uv run pytest tests/ -v
```

34 tests covering edge detection, Poisson model, consensus fair_prob, xG scraper, sports auto-discovery, results checker, notifier (combinada Kelly calc), and vault writer.

---

## Edge Alert File Format

Each qualifying edge is saved as a Markdown file with YAML frontmatter:

```yaml
---
match: PSG vs Arsenal
competition: Champions League
date: "2026-05-30T20:00:00+00:00"
market: home
odds: 2.45
fair_prob: 0.47
edge: 0.062
signal: alta
won: null        # filled in by results_checker after match ends
result_score: null
pnl_units: null
checked_at: null
---
```

The results checker runs every 6h, finds files where `won: null` and the match date has passed, queries football-data.org for the final score, and writes the outcome + PnL.

---

## Supported Leagues (xG model)

| League | Sofascore Tournament ID |
|--------|------------------------|
| Premier League | 17 |
| La Liga | 8 |
| Serie A | 23 |
| Bundesliga | 35 |
| Ligue 1 | 34 |

Cup competitions (UCL, UEL) and all non-football sports use consensus fair_prob from The Odds API bookmakers instead of the xG/Poisson model.
