"""
Ejecuta análisis y sincronización de datos periódicamente.

Uso: python scheduler.py

Jobs programados:
  - Sync TML-Database  →  07:00 diario  (datos frescos antes del análisis)
  - Análisis de picks  →  cada 6h       (busca valor en los mercados)
"""
import schedule
import time
from loguru import logger
from main import run
from sync import run as sync_run

EVERY_HOURS  = 6    # cadencia del análisis de picks
SYNC_TIME    = "07:00"  # hora local del sync diario


def analysis_job():
    logger.info("Scheduler: lanzando análisis de picks...")
    try:
        run()
    except Exception as e:
        logger.error(f"Error en el análisis programado: {e}")


def sync_job():
    logger.info("Scheduler: sincronizando datos TML-Database...")
    try:
        sync_run()
    except Exception as e:
        logger.error(f"Error en la sincronización: {e}")


if __name__ == "__main__":
    logger.info(f"Scheduler iniciado — sync a las {SYNC_TIME}, análisis cada {EVERY_HOURS}h")

    # Sync + análisis al arrancar
    sync_job()
    analysis_job()

    # Programar jobs recurrentes
    schedule.every().day.at(SYNC_TIME).do(sync_job)
    schedule.every(EVERY_HOURS).hours.do(analysis_job)

    while True:
        schedule.run_pending()
        time.sleep(60)
