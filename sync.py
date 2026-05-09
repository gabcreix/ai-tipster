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
import time
from datetime import datetime
from io import StringIO

import pandas as pd
import requests
from loguru import logger


from src.data.database import (
    init_db, upsert_matches, get_match_history_counts, has_source_data,
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
        df = None
        for attempt in range(4):
            try:
                r = requests.get(url, timeout=15)
                if r.status_code == 200:
                    df = pd.read_csv(StringIO(r.text))
                    cache.save(cache_key, df, ttl_hours=1)
                    logger.info(f"  {len(df)} partidos en ongoing_tourneys")
                    break
                else:
                    logger.warning(f"  ongoing_tourneys: HTTP {r.status_code} (intento {attempt+1}/4)")
            except Exception as e:
                logger.error(f"  Error descargando ongoing_tourneys (intento {attempt+1}/4): {e}")
            if attempt < 3:
                wait = 2 ** (attempt + 1)
                logger.info(f"  Reintentando en {wait}s...")
                time.sleep(wait)
        if df is None:
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
        # El motivo real (suscripción caducada, sin datos) ya se logueó en _get()
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


def sync_jeffsackmann(
    years: list[int] = None,
    tours: list[str] = None,
    force: bool = False,
) -> dict[str, int]:
    """
    Descarga datos históricos JeffSackmann y los persiste en match_history.

    Años estables (< año_actual - 1): INSERT OR IGNORE — se saltan si ya tienen filas.
    Años vivos (año_actual y año_actual-1): INSERT OR REPLACE — siempre se re-sincronizan
        porque JeffSackmann sigue añadiendo partidos a esos CSVs a lo largo del año.

    Tras ejecutar esto una vez, main.py leerá de DB en vez de HTTP.
    Añadir años anteriores:  python sync.py --backfill 2018 2019 2020 2021
    """
    from src.data.tennis_data import _download_matches, BASE_URL_ATP, BASE_URL_WTA
    from src.data.database import get_match_history_counts

    if years is None:
        years = [2022, 2023, 2024]
    if tours is None:
        tours = ["ATP", "WTA"]

    live_years = {CURRENT_YEAR, CURRENT_YEAR - 1}

    counts = {
        (r["tour"], r["year"]): r["matches"]
        for r in get_match_history_counts()
        if r["source"] == "jeffsackmann"
    }

    totals: dict[str, int] = {}
    for tour in tours:
        base_url = BASE_URL_ATP if tour == "ATP" else BASE_URL_WTA
        prefix   = "atp"       if tour == "ATP" else "wta"

        if force:
            years_stable = []
            years_live   = list(years)
        else:
            years_stable = [
                y for y in years
                if y not in live_years and counts.get((tour, y), 0) == 0
            ]
            years_live = [y for y in years if y in live_years]

        if not years_stable and not years_live:
            logger.info(f"JeffSackmann {tour}: sin años que sincronizar")
            totals[tour] = 0
            continue

        total_new = 0

        # Años estables: INSERT OR IGNORE
        if years_stable:
            logger.info(f"Backfill JeffSackmann {tour} estables {years_stable}...")
            df_stable = _download_matches(base_url, prefix, years_stable)
            if not df_stable.empty:
                n = upsert_matches(df_stable, source="jeffsackmann", tour=tour, replace=False)
                logger.info(f"  {tour} estables: {len(df_stable)} partidos → {n} nuevos")
                total_new += n

        # Años vivos: INSERT OR REPLACE (siempre, aunque ya existan filas)
        if years_live:
            logger.info(f"Sincronizando JeffSackmann {tour} vivos {years_live} (reemplaza filas existentes)...")
            df_live = _download_matches(base_url, prefix, years_live)
            if not df_live.empty:
                n = upsert_matches(df_live, source="jeffsackmann", tour=tour, replace=True)
                logger.info(f"  {tour} vivos: {len(df_live)} partidos → {n} insertados/actualizados")
                total_new += n

        totals[tour] = total_new

        # Invalida caché de disco para que main.py lea de DB en el próximo run
        cache.invalidate(f"{'atp' if tour == 'ATP' else 'wta'}_matches_{min(years)}_{max(years)}")
        cache.invalidate(f"atp_matches_{min(years)}_{max(years)}_recent")

    return totals


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

    # Primera ejecución: poblar datos históricos JeffSackmann automáticamente
    if not has_source_data("jeffsackmann"):
        logger.info("Primera ejecución detectada — backfill JeffSackmann 2022-2025...")
        sync_jeffsackmann(years=[2022, 2023, 2024, 2025], tours=["ATP", "WTA"])

    logger.info(f"=== Sync — años: {years} {'(force)' if force else ''} ===")
    totals      = sync_tml(years, force=force)
    ongoing_new = sync_ongoing(force=force)
    sf_new      = sync_sofascore(days_back=2, force=force) if sofascore else 0

    total_new = sum(totals.values()) + ongoing_new + sf_new
    logger.info(f"\n  Total nuevos partidos insertados: {total_new}")
    print_summary()
    return totals


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sincroniza TML-Database + Sofascore → DB local")
    parser.add_argument(
        "--years", nargs="+", type=int,
        default=DEFAULT_YEARS,
        help=f"Años a sincronizar con TML-Database (defecto: {DEFAULT_YEARS})",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Invalida caché y re-descarga aunque los datos ya estén en caché",
    )
    parser.add_argument(
        "--no-sofascore", action="store_true",
        help="Omitir la sincronización de Sofascore",
    )
    parser.add_argument(
        "--backfill",
        nargs="*",
        type=int,
        metavar="YEAR",
        help=(
            "Descarga y persiste datos JeffSackmann en DB. "
            "Sin argumentos: 2022-2024. "
            "Con años: --backfill 2018 2019 2020 2021"
        ),
    )
    parser.add_argument(
        "--tours",
        nargs="+",
        choices=["ATP", "WTA"],
        default=["ATP", "WTA"],
        help="Circuitos a backfillear con --backfill (defecto: ATP WTA)",
    )
    args = parser.parse_args()

    if args.backfill is not None:
        init_db()
        bf_years = args.backfill if args.backfill else [2022, 2023, 2024]
        logger.info(f"=== Backfill JeffSackmann — años: {bf_years} | tours: {args.tours} ===")
        sync_jeffsackmann(years=bf_years, tours=args.tours, force=args.force)
        print_summary()
    else:
        run(years=args.years, force=args.force, sofascore=not args.no_sofascore)
