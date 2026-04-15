import requests
from loguru import logger
from config import ODDS_API_KEY, BASE_URL_ODDS, SPORTS, BOOKMAKERS


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