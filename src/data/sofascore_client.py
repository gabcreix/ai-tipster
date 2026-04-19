"""
Cliente para la API interna de Sofascore.

Descarga resultados ATP/WTA completados con estadísticas de servicio
y los convierte al esquema de match_history.

Normalización de serve stats:
  Como Sofascore proporciona porcentajes (no puntos brutos), se normalizan
  a una escala de 100 puntos por partido para compatibilidad con
  calculate_player_stats():
    w_svpt = 100,  w_1stWon = round(serve_win_pct * 100),  w_2ndWon = 0

  Esto preserva serve_win_pct exactamente y permite agregación.
  El umbral mínimo de 500 puntos en calculate_player_stats() equivale
  a >= 5 partidos Sofascore por superficie.
"""
import time
from datetime import datetime, timedelta, timezone
from loguru import logger

import pandas as pd
import requests
import urllib3

# Entornos corporativos con proxy SSL interceptor — suprimir warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# cloudscraper gestiona automáticamente el challenge de Cloudflare
try:
    import cloudscraper
    _scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    _scraper.verify = False   # proxy corporativo con CA propia
    logger.info("Sofascore: usando cloudscraper (Cloudflare bypass)")
except ImportError:
    _scraper = None
    logger.warning("cloudscraper no instalado — instala con: python -m pip install cloudscraper")

BASE     = "https://api.sofascore.com/api/v1"
HOME_URL = "https://www.sofascore.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "sec-ch-ua": '"Google Chrome";v="124", "Chromium";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}

# Sesión requests para fallback (recoge cookies al inicializarse)
_session: requests.Session | None = None


def _init_session() -> requests.Session:
    """
    Crea una sesión requests visitando la página principal de Sofascore
    para obtener cookies de sesión (cf_clearance, etc.).
    """
    global _session
    if _session is not None:
        return _session
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        resp = s.get(HOME_URL, timeout=15, allow_redirects=True, verify=False)
        logger.debug(f"Sofascore home: HTTP {resp.status_code}, cookies={list(resp.cookies.keys())}")
    except Exception as e:
        logger.warning(f"No se pudo inicializar sesión Sofascore: {e}")
    _session = s
    return _session

SURFACE_MAP = {
    "CLAY":         "Clay",
    "HARD":         "Hard",
    "GRASS":        "Grass",
    "INDOOR_HARD":  "Hard",
    "INDOOR_CLAY":  "Clay",
}

REQUEST_DELAY = 0.4   # segundos entre llamadas para no saturar la API
STATS_DELAY   = 0.25


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _get(path: str, retries: int = 2) -> dict | None:
    url = f"{BASE}{path}"
    for attempt in range(retries + 1):
        try:
            if _scraper is not None:
                r = _scraper.get(url, timeout=15, verify=False)
            else:
                r = _init_session().get(url, timeout=15, verify=False)

            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = 5 * (attempt + 1)
                logger.warning(f"Sofascore rate-limit, esperando {wait}s…")
                time.sleep(wait)
                continue
            if r.status_code == 404:
                return None
            logger.warning(f"Sofascore {path}: HTTP {r.status_code}")
            if r.status_code == 403 and attempt == 0:
                # Reintentar tras una pausa corta (puede ser rate-limit temporal)
                time.sleep(3)
                continue
            return None
        except Exception as e:
            logger.error(f"Sofascore {path}: {e}")
            if attempt < retries:
                time.sleep(2)
    return None


# ── Parsing helpers ───────────────────────────────────────────────────────────

def _pct(value: str) -> float | None:
    """Convierte '69%' → 0.69. Devuelve None si no parseable."""
    try:
        return round(float(str(value).rstrip("%")) / 100, 4)
    except (ValueError, TypeError):
        return None


def _int(value) -> int | None:
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _parse_stats(data: dict) -> dict:
    """
    Extrae estadísticas de servicio del JSON de /event/{id}/statistics.
    Devuelve {"home": {...}, "away": {...}} con campos normalizados.
    """
    result = {"home": {}, "away": {}}

    stats_list = data.get("statistics") or []
    # Usar el periodo "ALL" (partido completo)
    period_data = next(
        (s for s in stats_list if s.get("period") == "ALL"),
        stats_list[0] if stats_list else None,
    )
    if not period_data:
        return result

    for group in period_data.get("groups", []):
        for item in group.get("statisticsItems", []):
            name = item.get("name", "")
            home = item.get("home")
            away = item.get("away")

            if name == "Aces":
                result["home"]["aces"] = _int(home)
                result["away"]["aces"] = _int(away)
            elif name == "Double faults":
                result["home"]["df"]   = _int(home)
                result["away"]["df"]   = _int(away)
            elif name == "Service games played":
                result["home"]["sv_gms"] = _int(home)
                result["away"]["sv_gms"] = _int(away)
            elif name == "Service points won":
                result["home"]["serve_win_pct"] = _pct(home)
                result["away"]["serve_win_pct"] = _pct(away)
            elif name == "First serve percentage":
                result["home"]["first_serve_pct"] = _pct(home)
                result["away"]["first_serve_pct"] = _pct(away)
            elif name == "First serve points won":
                result["home"]["first_won_pct"] = _pct(home)
                result["away"]["first_won_pct"] = _pct(away)
            elif name == "Second serve points won":
                result["home"]["second_won_pct"] = _pct(home)
                result["away"]["second_won_pct"] = _pct(away)
            elif name == "Break points saved":
                # formato "3/5"
                try:
                    parts = str(home).split("/")
                    result["home"]["bp_saved"] = int(parts[0])
                    result["home"]["bp_faced"] = int(parts[1])
                except Exception:
                    pass
                try:
                    parts = str(away).split("/")
                    result["away"]["bp_saved"] = int(parts[0])
                    result["away"]["bp_faced"] = int(parts[1])
                except Exception:
                    pass

    return result


def _normalize_serve(stats: dict) -> dict:
    """
    Convierte estadísticas de porcentaje a conteos normalizados (base 100).
    Compatible con calculate_player_stats() que usa w_svpt / w_1stWon / w_2ndWon.
    """
    swp = stats.get("serve_win_pct")
    if swp is None:
        return {}
    return {
        "svpt":   100,
        "1stWon": round(swp * 100),
        "2ndWon": 0,
        "SvGms":  stats.get("sv_gms"),
        "ace":    stats.get("aces"),
        "df":     stats.get("df"),
        "bpSaved": stats.get("bp_saved"),
        "bpFaced": stats.get("bp_faced"),
    }


# ── Eventos por fecha ─────────────────────────────────────────────────────────

def get_events_by_date(date_str: str) -> list[dict]:
    """
    Devuelve lista de eventos de tenis para una fecha (YYYY-MM-DD).
    """
    data = _get(f"/sport/tennis/scheduled-events/{date_str}")
    if not data:
        return []
    events = data.get("events") or []
    logger.debug(f"Sofascore {date_str}: {len(events)} eventos totales")
    return events


# ── Función principal ─────────────────────────────────────────────────────────

def fetch_matches(days_back: int = 1) -> pd.DataFrame:
    """
    Descarga partidos ATP/WTA completados de los últimos `days_back` días.
    Devuelve DataFrame compatible con match_history (listo para upsert_matches).
    """
    today = datetime.now(tz=timezone.utc).date()
    dates = [
        (today - timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(1, days_back + 1)
    ]

    records = []

    for date_str in dates:
        logger.info(f"Sofascore: obteniendo eventos {date_str}…")
        events = get_events_by_date(date_str)
        time.sleep(REQUEST_DELAY)

        atp_wta = [
            e for e in events
            if (
                e.get("status", {}).get("type") == "finished"
                and e.get("tournament", {}).get("category", {}).get("name") in ("ATP", "WTA")
            )
        ]
        logger.info(f"  {len(atp_wta)} partidos ATP/WTA finalizados")

        for event in atp_wta:
            row = _process_event(event)
            if row:
                records.append(row)
            time.sleep(STATS_DELAY)

    if not records:
        logger.info("Sofascore: sin partidos nuevos.")
        return pd.DataFrame()

    df = pd.DataFrame(records)
    logger.info(f"Sofascore: {len(df)} partidos procesados ({days_back} día(s))")
    return df


def _process_event(event: dict) -> dict | None:
    """
    Procesa un evento de Sofascore y devuelve un dict listo para match_history.
    Devuelve None si faltan datos esenciales o las stats de servicio.
    """
    event_id  = event.get("id")
    tour_info = event.get("tournament", {})
    category  = tour_info.get("category", {}).get("name", "")  # ATP / WTA
    uniq      = tour_info.get("uniqueTournament", {})

    home_name = event.get("homeTeam", {}).get("name", "").strip()
    away_name = event.get("awayTeam", {}).get("name", "").strip()
    if not home_name or not away_name or not event_id:
        return None

    # winner_code: 1 = home ganó, 2 = away ganó
    winner_code = event.get("winnerCode")
    if winner_code not in (1, 2):
        return None

    winner_name = home_name if winner_code == 1 else away_name
    loser_name  = away_name if winner_code == 1 else home_name

    # Fecha y torneo
    ts           = event.get("startTimestamp", 0)
    dt           = datetime.fromtimestamp(ts, tz=timezone.utc)
    tourney_date = int(dt.strftime("%Y%m%d"))
    tourney_id   = f"sf_{uniq.get('id', 0)}_{dt.year}"
    tourney_name = uniq.get("name", tour_info.get("name", ""))

    # Superficie
    raw_surface = event.get("groundType", "")
    surface     = SURFACE_MAP.get(raw_surface, "Hard")

    # Rankings (si Sofascore los incluye)
    home_rank = event.get("homeTeam", {}).get("ranking")
    away_rank = event.get("awayTeam", {}).get("ranking")
    winner_rank = home_rank if winner_code == 1 else away_rank
    loser_rank  = away_rank if winner_code == 1 else home_rank

    # Estadísticas de servicio
    stats_data = _get(f"/event/{event_id}/statistics")
    if not stats_data:
        logger.debug(f"  Sin stats para evento {event_id} ({winner_name} vs {loser_name})")
        return None

    parsed = _parse_stats(stats_data)
    home_stats = parsed["home"]
    away_stats = parsed["away"]

    if not home_stats.get("serve_win_pct") or not away_stats.get("serve_win_pct"):
        logger.debug(f"  Stats incompletas para {event_id}")
        return None

    w_stats = _normalize_serve(home_stats if winner_code == 1 else away_stats)
    l_stats = _normalize_serve(away_stats if winner_code == 1 else home_stats)

    logger.debug(
        f"  {winner_name} vs {loser_name} | "
        f"serve: {(home_stats if winner_code==1 else away_stats).get('serve_win_pct'):.0%} "
        f"vs {(away_stats if winner_code==1 else home_stats).get('serve_win_pct'):.0%}"
    )

    return {
        "tourney_id":          tourney_id,
        "match_num":           event_id,
        "tourney_date":        tourney_date,
        "tourney_name":        tourney_name,
        "surface":             surface,
        "tour":                category,
        "winner_name":         winner_name,
        "loser_name":          loser_name,
        "winner_rank":         winner_rank,
        "loser_rank":          loser_rank,
        "w_svpt":              w_stats.get("svpt"),
        "w_1stIn":             None,
        "w_1stWon":            w_stats.get("1stWon"),
        "w_2ndWon":            w_stats.get("2ndWon"),
        "l_svpt":              l_stats.get("svpt"),
        "l_1stIn":             None,
        "l_1stWon":            l_stats.get("1stWon"),
        "l_2ndWon":            l_stats.get("2ndWon"),
        "score":               None,
    }


# ── Diagnóstico ───────────────────────────────────────────────────────────────

def debug_event(event_id: int):
    """
    Imprime el JSON crudo de un evento y sus estadísticas.
    Uso: python -c "from src.data.sofascore_client import debug_event; debug_event(12345)"
    """
    import json
    print(f"\n=== Evento {event_id} ===")
    data = _get(f"/event/{event_id}")
    print(json.dumps(data, indent=2, ensure_ascii=False)[:2000] if data else "Sin datos")

    print(f"\n=== Stats {event_id} ===")
    stats = _get(f"/event/{event_id}/statistics")
    print(json.dumps(stats, indent=2, ensure_ascii=False)[:3000] if stats else "Sin stats")


def debug_date(date_str: str = None):
    """
    Lista los partidos ATP/WTA de una fecha y muestra un ejemplo de stats.
    Uso: python -c "from src.data.sofascore_client import debug_date; debug_date('2026-04-18')"
    """
    import json
    if date_str is None:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    events = get_events_by_date(date_str)
    atp_wta = [
        e for e in events
        if e.get("tournament", {}).get("category", {}).get("name") in ("ATP", "WTA")
    ]
    print(f"\n{date_str}: {len(events)} eventos totales, {len(atp_wta)} ATP/WTA")

    for e in atp_wta[:5]:
        status = e.get("status", {}).get("type")
        cat    = e.get("tournament", {}).get("category", {}).get("name")
        home   = e.get("homeTeam", {}).get("name")
        away   = e.get("awayTeam", {}).get("name")
        surf   = e.get("groundType")
        eid    = e.get("id")
        print(f"  [{status}] {cat} | {home} vs {away} | {surf} | id={eid}")

    # Stats de ejemplo del primer partido finalizado
    finished = [e for e in atp_wta if e.get("status", {}).get("type") == "finished"]
    if finished:
        eid = finished[0]["id"]
        print(f"\n--- Stats ejemplo (evento {eid}) ---")
        stats = _get(f"/event/{eid}/statistics")
        if stats:
            parsed = _parse_stats(stats)
            print("  home:", parsed["home"])
            print("  away:", parsed["away"])
        else:
            print("  Sin stats disponibles")
