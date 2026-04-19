import requests
from loguru import logger
from config import ODDS_API_KEY, BASE_URL_ODDS, SPORTS, BOOKMAKERS, SPORTS_TENNIS


def get_available_tennis_sports() -> list[str]:
    """
    Devuelve los sport_keys de tenis activos en the-odds-api.
    Un sport es "activo" cuando tiene partidos con odds disponibles ahora mismo.
    Fallback: SPORTS_TENNIS de config si la API no responde.
    """
    if not ODDS_API_KEY:
        return list(SPORTS_TENNIS)
    url = f"{BASE_URL_ODDS}/sports"
    params = {"apiKey": ODDS_API_KEY}
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            logger.warning(f"Sports API: HTTP {r.status_code} — usando SPORTS_TENNIS de config")
            return list(SPORTS_TENNIS)
        active = [
            s["key"] for s in r.json()
            if "tennis" in s.get("key", "").lower()
            and s.get("active", False)
        ]
        logger.info(f"Torneos de tenis activos: {len(active)}")
        return active
    except Exception as e:
        logger.error(f"Error obteniendo sports activos: {e}")
        return list(SPORTS_TENNIS)


def get_odds(sport: str) -> list:
    """Obtiene odds en vivo para un deporte dado."""
    url = f"{BASE_URL_ODDS}/sports/{sport}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": "h2h",
        "bookmakers": ",".join(BOOKMAKERS),
        "oddsFormat": "decimal",
    }

    response = requests.get(url, params=params)

    if response.status_code != 200:
        logger.error(f"Error API: {response.status_code} — {response.text}")
        return []

    remaining = response.headers.get("x-requests-remaining", "?")
    logger.info(f"Requests restantes este mes: {remaining}")

    return response.json()


if __name__ == "__main__":
    for sport in SPORTS:
        logger.info(f"Obteniendo odds para {sport}...")
        odds = get_odds(sport)
        logger.info(f"Partidos encontrados: {len(odds)}")
        if odds:
            logger.info(f"Ejemplo: {odds[0]}")