"""
Sincroniza partidos de TML-Database (ATP 2025/2026) a la BD local.

Fuentes TML-Database:
  - {year}.csv          → partidos del año completo (se actualiza al cerrar torneos)
  - ongoing_tourneys.csv → torneos en curso (se actualiza más frecuentemente)

Uso:
  python sync.py                  # sincroniza 2025 + año actual + ongoing
  python sync.py --years 2025     # sólo 2025
  python sync.py --years 2025 2026 --force  # fuerza re-descarga aunque haya caché

El script es idempotente: usa INSERT OR IGNORE, por lo que relanzarlo
no genera duplicados. Ideal para cron diario o llamada desde scheduler.py.
"""
import argparse
from datetime import datetime
from io import StringIO

import pandas as pd
import requests
from loguru import logger


from src.data.database import (
    init_db, upsert_matches, get_match_history_counts,
)
from src.data.tennis_data import _download_tml, BASE_URL_TML
from src.data import cache


CURRENT_YEAR = datetime.now().year
DEFAULT_YEARS = sorted({2025, CURRENT_YEAR})


def sync_ongoing(force: bool = False) -> int:
    """
    Descarga ongoing_tourneys.csv y lo persiste en match_history.
    Este archivo contiene torneos en curso no volcados aún al CSV anual.
    """
    cache_key = "tml_ongoing"
    if force:
        cache.invalidate(cache_key)

    cached = cache.load(cache_key)
    if cached is not None:
        df = cached
    else:
        url = f"{BASE_URL_TML}/ongoing_tourneys.csv"
        logger.info("Descargando ongoing_tourneys.csv...")
        try:
            r = requests.get(url, timeout=15)
            if r.status_code != 200:
                logger.warning(f"  ongoing_tourneys: HTTP {r.status_code}")
                return 0
            df = pd.read_csv(StringIO(r.text))
            cache.save(cache_key, df, ttl_hours=1)
            logger.info(f"  {len(df)} partidos en ongoing_tourneys")
        except Exception as e:
            logger.error(f"  Error descargando ongoing_tourneys: {e}")
            return 0

    new_rows = upsert_matches(df, source="tml_ongoing", tour="ATP")
    logger.info(f"  ongoing_tourneys → {new_rows} nuevos en DB")
    return new_rows


def sync_tml(years: list[int], force: bool = False) -> dict[int, int]:
    """
    Descarga TML-Database para los años indicados y persiste en match_history.
    Devuelve {year: new_rows}.
    """
    result = {}
    for year in years:
        cache_key = f"tml_atp_{year}"
        if force:
            cache.invalidate(cache_key)

        logger.info(f"Sincronizando TML-Database ATP {year}...")
        df = _download_tml([year])

        if df.empty:
            logger.warning(f"  Sin datos para {year}")
            result[year] = 0
            continue

        new_rows = upsert_matches(df, source="tml", tour="ATP")
        logger.info(f"  {year}: {len(df)} partidos descargados → {new_rows} nuevos en DB")
        result[year] = new_rows

    return result


def print_summary():
    rows = get_match_history_counts()
    if not rows:
        logger.info("match_history vacía.")
        return
    logger.info("\n  Estado actual de match_history:")
    logger.info(f"  {'Source':15s} {'Tour':5s} {'Año':6s} {'Partidos':>8s}")
    logger.info(f"  {'-'*38}")
    for r in rows:
        logger.info(f"  {r['source']:15s} {r['tour']:5s} {r['year']:6d} {r['matches']:>8d}")


def sync_api_tennis(days_back: int = 7) -> int:
    """
    Descarga partidos ATP/WTA Singles completados de api-tennis.com
    (últimos `days_back` días) y persiste en match_history.
    Usa la misma API key que el análisis de picks — sin configuración extra.
    """
    from config import TENNIS_API_KEY
    if not TENNIS_API_KEY:
        logger.info("TENNIS_API_KEY no configurado — omitiendo sync api-tennis fixtures")
        return 0

    from src.data.tennis_api_client import TennisAPIClient
    client = TennisAPIClient(TENNIS_API_KEY)

    logger.info(f"Sincronizando api-tennis fixtures (últimos {days_back} días)…")
    df = client.get_recent_results(days_back=days_back)

    if df.empty:
        logger.info("  api-tennis fixtures: sin partidos nuevos")
        return 0

    new_rows = 0
    for tour in ("ATP", "WTA"):
        sub = df[df["tour"] == tour] if "tour" in df.columns else pd.DataFrame()
        if sub.empty:
            continue
        n = upsert_matches(sub, source="api_tennis", tour=tour)
        logger.info(f"  api-tennis {tour}: {len(sub)} partidos → {n} nuevos en DB")
        new_rows += n

    return new_rows


def sync_sofascore(days_back: int = 2, force: bool = False) -> int:
    """
    Descarga partidos ATP/WTA de Sofascore de los últimos `days_back` días.
    Persiste en match_history con source='sofascore'.
    """
    try:
        from src.data.sofascore_client import fetch_matches
    except ImportError as e:
        logger.error(f"sofascore_client no disponible: {e}")
        return 0

    cache_key = f"sofascore_sync_{days_back}"
    if force:
        cache.invalidate(cache_key)

    logger.info(f"Sincronizando Sofascore (últimos {days_back} días)…")
    try:
        df = fetch_matches(days_back=days_back)
    except Exception as e:
        if "SofascoreUnavailable" in type(e).__name__ or "403" in str(e):
            logger.warning("  Sofascore no disponible (proxy corporativo) — omitiendo")
        else:
            logger.error(f"  Sofascore error inesperado: {e}")
        return 0

    if df.empty:
        logger.info("  Sofascore: sin partidos nuevos")
        return 0

    new_rows = 0
    for tour in ("ATP", "WTA"):
        sub = df[df["tour"] == tour] if "tour" in df.columns else pd.DataFrame()
        if sub.empty:
            continue
        n = upsert_matches(sub, source="sofascore", tour=tour)
        logger.info(f"  Sofascore {tour}: {len(sub)} partidos → {n} nuevos en DB")
        new_rows += n

    return new_rows


def run(years: list[int] = None, force: bool = False, sofascore: bool = True):
    if years is None:
        years = DEFAULT_YEARS

    init_db()

    logger.info(f"=== Sync — años: {years} {'(force)' if force else ''} ===")
    totals      = sync_tml(years, force=force)
    ongoing_new = sync_ongoing(force=force)
    at_new      = sync_api_tennis(days_back=7)
    sf_new      = sync_sofascore(days_back=2, force=force) if sofascore else 0

    total_new = sum(totals.values()) + ongoing_new + at_new + sf_new
    logger.info(f"\n  Total nuevos partidos insertados: {total_new}")
    print_summary()
    return totals


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sincroniza TML-Database + Sofascore → DB local")
    parser.add_argument(
        "--years", nargs="+", type=int,
        default=DEFAULT_YEARS,
        help=f"Años a sincronizar (defecto: {DEFAULT_YEARS})",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Invalida caché y re-descarga aunque los datos ya estén en caché",
    )
    parser.add_argument(
        "--no-sofascore", action="store_true",
        help="Omitir la sincronización de Sofascore",
    )
    args = parser.parse_args()
    run(years=args.years, force=args.force, sofascore=not args.no_sofascore)
