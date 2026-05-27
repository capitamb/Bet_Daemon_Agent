# Apuestas Daemon v2 — Design Spec
**Date:** 2026-05-26  
**Status:** Approved

---

## 1. Objetivo

Extender el daemon existente con:
1. xG via Sofascore (reemplaza Understat, que está roto)
2. Consensus fair_prob para cups (CL/EL) y todos los deportes no-football
3. Auto-discovery de todos los deportes activos en The Odds API
4. Combinada automática por Telegram (alertas individuales + parlay cuando 2+ edges)
5. Results checker con feedback loop para calibración del modelo

---

## 2. Arquitectura

### Archivos modificados
| Archivo | Cambio |
|---|---|
| `scrapers/xg.py` | Reemplaza Understat con Sofascore internal API |
| `scrapers/odds.py` | Agrega `consensus_fair_prob()` + fetch sin filtro de bookmaker |
| `notifier.py` | Agrega `format_combinada_alert()` + `send_alerts_for_cycle()` |
| `config.py` | Agrega `CUP_COMPETITIONS`, `KELLY_FRACTION`, Sofascore tournament IDs |
| `main.py` | Pipeline unificado multi-sport + job results_checker cada 6h + endpoint `/stats` |

### Archivos nuevos
| Archivo | Propósito |
|---|---|
| `scrapers/sports.py` | Auto-discovery de sports activos via `/v4/sports`, cache 24h |
| `results_checker.py` | Lee edges pendientes, consulta resultados, escribe PnL, genera resumen semanal |

---

## 3. Componentes

### 3.1 `scrapers/xg.py` — Sofascore xG

**Fuente:** `https://api.sofascore.com/api/v1/unique-tournament/{tid}/season/{sid}/statistics/overall`

**Tournament IDs hardcodeados:**
```python
SOFASCORE_TOURNAMENTS = {
    "Premier League":    {"tid": 17,  "sid": None},  # sid resuelto en runtime
    "La Liga":           {"tid": 8,   "sid": None},
    "Serie A":           {"tid": 23,  "sid": None},
    "Bundesliga":        {"tid": 35,  "sid": None},
    "Ligue 1":           {"tid": 34,  "sid": None},
}
```

- `sid` (season ID) se resuelve via `/api/v1/unique-tournament/{tid}/seasons` → tomar el más reciente
- Response contiene `statistics.xG` y `statistics.xGAgainst` por equipo
- Interfaz de retorno: mismo `TeamXG` dataclass que hoy
- Cache en memoria por `(league, season_id)`, sin TTL (datos de temporada son estables)

### 3.2 `scrapers/odds.py` — Consensus fair_prob

**Cuándo se usa:**
- Football: competiciones en `CUP_COMPETITIONS` (`{"Champions League", "Europa League"}`)
- Todos los demás deportes (NBA, NFL, tenis, etc.)

**Algoritmo:**
```
Para cada bookmaker que tenga h2h odds:
  implied = [1/o for o in outcomes]
  vig = sum(implied) - 1.0
  fair = [p / sum(implied) for p in implied]  # normalizar
fair_prob_consensus = mean(fair_home), mean(fair_draw), mean(fair_away)
```

**Fetch:** sin `bookmakers=pinnacle`, con `regions=eu,us` para máxima cobertura  
**Mínimo bookmakers:** 3 para que el consensus sea válido; si < 3, skip el partido

### 3.3 `scrapers/sports.py` — Auto-discovery

```
GET /v4/sports?apiKey=...&all=false
```
- `all=false` → solo sports con eventos activos ahora
- Filtra `soccer_*` (manejados por pipeline football separado)
- Cache en memoria, TTL 24h
- En startup: loguea cuántos sports no-football se encontraron

### 3.4 `notifier.py` — Combinada

**`send_alerts_for_cycle(edges_with_matches)`:**
1. Manda una alerta individual por cada edge que califica
2. Si `len(edges) >= 2`: calcula combinada y manda alerta adicional

**Cálculo combinada:**
```python
odds_combinada = prod(e.odds for e in edges)
# p_win estimada como producto de fair_probs (independencia asumida)
p_win = prod(e.fair_prob for e in edges)
kelly_full = (odds_combinada * p_win - 1) / (odds_combinada - 1)
kelly_stake = max(0, kelly_full * KELLY_FRACTION)  # KELLY_FRACTION = 0.25
```

**Formato mensaje combinada:**
```
🎯 COMBINADA — N legs
Leg 1: PSG ML @ 2.37 (Arsenal vs PSG — UCL Final)
Leg 2: Lakers ML @ 1.85 (Lakers vs Celtics — NBA)
...
Odd total: X.XX | Kelly 25%: Y.Y% bankroll
Fair p_win: Z.Z%
```

### 3.5 `results_checker.py` — Feedback loop

**Trigger:** job APScheduler cada 6h + job lunes 9am para resumen semanal

**Flujo por edge pendiente:**
1. Lee todos los `.md` en `data_dir` donde frontmatter `won` es null/ausente
2. Filtra: `date < now - 2h` (partido debería haber terminado)
3. Extrae `match` y `date` del frontmatter para construir query a football-data.org
4. `GET /v4/matches?dateFrom=...&dateTo=...` → busca por nombre de equipos
5. Si `status == FINISHED`: extrae score, determina ganador
6. Compara con `market` del edge → `won: true/false`
7. `pnl_units`: +`(odds-1)` si won, `-1` si lost
8. Escribe `won`, `result_score`, `pnl_units`, `checked_at` en frontmatter

**Resumen semanal (lunes 9am):**
- Lee todos los `.md` con `won != null` de los últimos 7 días
- Calcula: total apostado, ganados, hit_rate, PnL neto en unidades
- Calibración: `avg_fair_prob_ganados vs hit_rate_real` → gap del modelo
- Manda por Telegram con `format_results_summary()`

### 3.6 `main.py` — Pipeline unificado

**Startup:**
1. Descubrir sports activos (Odds API)
2. Correr football pipeline inmediatamente
3. Correr otros-deportes pipeline inmediatamente
4. Scheduler: football + otros deportes cada 15min, results_checker cada 6h, resumen lunes 9am

**Pipeline otros deportes:**
```
Para cada sport_key no-soccer:
  Fetch odds (todos los bookmakers)
  Para cada evento con ≥3 bookmakers:
    consensus_fair_prob() → match.fair_prob_*
    detect_edges()
```

**Endpoint nuevo:** `GET /stats`
```json
{
  "total_edges": 42,
  "won": 18,
  "lost": 20,
  "pending": 4,
  "hit_rate": 0.47,
  "pnl_units": +3.2,
  "model_gap": -0.04
}
```

---

## 4. Config additions (`config.py`)

```python
CUP_COMPETITIONS = {"Champions League", "Europa League"}
KELLY_FRACTION = 0.25
CONSENSUS_MIN_BOOKMAKERS = 3

SOFASCORE_TOURNAMENTS = {
    "Premier League": 17,
    "La Liga": 8,
    "Serie A": 23,
    "Bundesliga": 35,
    "Ligue 1": 34,
}
```

---

## 5. Error handling

- Sofascore falla → log warning, `find_team_xg` retorna `None`, partido se salta (no edge)
- Odds API < 3 bookmakers → skip consensus, no fair_prob, no edge
- football-data.org no encuentra partido para results_checker → marca `won: null`, retry en siguiente ciclo
- Sport con 0 eventos activos → skip silencioso, no error

---

## 6. Testing

Los tests existentes (`test_edge_detector`, `test_poisson_model`, etc.) no se tocan.

Tests nuevos:
- `test_consensus_fair_prob.py` — verifica normalización de vig y promedio entre bookmakers
- `test_results_checker.py` — mock de football-data.org response, verifica escritura de frontmatter
- `test_combinada.py` — verifica cálculo de Kelly y formato del mensaje

---

## 7. Scope explícito (fuera de scope)

- Despliegue a VPS/servidor (fuera de scope en esta iteración)
- Otros mercados más allá de h2h (totales, handicaps) — futura iteración
- UI web / dashboard — futura iteración
