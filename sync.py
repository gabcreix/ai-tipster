"""
Sincroniza partidos de TML-Database (ATP 2025/2026) a la BD local.

Uso:
  python sync.py                  # sincroniza 2025 + año actual
  python sync.py --years 2025     # sólo 2025
  python sync.py --years 2025 2026 --force  # fuerza re-descarga aunque haya caché

El script es idempotente: usa INSERT OR IGNORE, por lo que relanzarlo
no genera duplicados. Ideal para cron diario o llamada desde scheduler.py.
"""
import argparse
from datetime import datetime
from loguru import logger

from src.data.database import (
    init_db, upsert_matches, get_match_history_counts,
)
from src.data.tennis_data import _download_tml
from src.data import cache


CURRENT_YEAR = datetime.now().year
DEFAULT_YEARS = sorted({2025, CURRENT_YEAR})


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
    logger.info(f"  {'Source':12s} {'Tour':5s} {'Año':6s} {'Partidos':>8s}")
    logger.info(f"  {'-'*35}")
    for r in rows:
        logger.info(f"  {r['source']:12s} {r['tour']:5s} {r['year']:6d} {r['matches']:>8d}")


def run(years: list[int] = None, force: bool = False):
    if years is None:
        years = DEFAULT_YEARS

    init_db()

    logger.info(f"=== Sync — años: {years} {'(force)' if force else ''} ===")
    totals = sync_tml(years, force=force)

    total_new = sum(totals.values())
    logger.info(f"\n  Total nuevos partidos insertados: {total_new}")
    print_summary()
    return totals


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sincroniza TML-Database → DB local")
    parser.add_argument(
        "--years", nargs="+", type=int,
        default=DEFAULT_YEARS,
        help=f"Años a sincronizar (defecto: {DEFAULT_YEARS})",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Invalida caché y re-descarga aunque los datos ya estén en caché",
    )
    args = parser.parse_args()
    run(years=args.years, force=args.force)
