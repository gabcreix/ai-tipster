"""
Caché en disco simple con TTL.
Guarda/carga objetos Python (DataFrames, dicts, listas) en data/cache/.
"""
import json
import pickle
from pathlib import Path
from datetime import datetime, timedelta
from loguru import logger

CACHE_DIR = Path("data/cache")


def _paths(key: str):
    return CACHE_DIR / f"{key}.meta.json", CACHE_DIR / f"{key}.pkl"


def save(key: str, data, ttl_hours: float = 24.0):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    meta_path, data_path = _paths(key)
    meta_path.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "ttl_hours": ttl_hours,
    }))
    with open(data_path, "wb") as f:
        pickle.dump(data, f)
    logger.debug(f"Caché guardado: {key} (TTL {ttl_hours}h)")


def invalidate(key: str):
    """Elimina una entrada de caché para forzar re-descarga."""
    meta_path, data_path = _paths(key)
    for p in (meta_path, data_path):
        if p.exists():
            p.unlink()
    logger.debug(f"Caché invalidado: {key}")


def load(key: str):
    meta_path, data_path = _paths(key)
    if not meta_path.exists() or not data_path.exists():
        return None
    try:
        meta       = json.loads(meta_path.read_text())
        cached_at  = datetime.fromisoformat(meta["cached_at"])
        ttl        = timedelta(hours=meta["ttl_hours"])
        if datetime.now() - cached_at > ttl:
            logger.debug(f"Caché expirado: {key}")
            return None
        with open(data_path, "rb") as f:
            data = pickle.load(f)
        age_min = int((datetime.now() - cached_at).total_seconds() / 60)
        logger.info(f"Caché hit: {key} (hace {age_min} min)")
        return data
    except Exception as e:
        logger.warning(f"Error leyendo caché {key}: {e}")
        return None
