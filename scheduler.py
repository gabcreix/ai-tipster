"""
Ejecuta main.run() automáticamente cada N horas.
Uso: python scheduler.py
"""
import schedule
import time
from loguru import logger
from main import run

EVERY_HOURS = 6  # cada cuántas horas correr el análisis


def job():
    logger.info("⏰ Scheduler: lanzando análisis...")
    try:
        run()
    except Exception as e:
        logger.error(f"Error en el análisis programado: {e}")


if __name__ == "__main__":
    logger.info(f"Scheduler iniciado — análisis cada {EVERY_HOURS}h")
    job()  # ejecutar una vez al arrancar
    schedule.every(EVERY_HOURS).hours.do(job)

    while True:
        schedule.run_pending()
        time.sleep(60)
