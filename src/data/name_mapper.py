import difflib
import unicodedata
from loguru import logger


def normalize(name: str) -> str:
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    return name.lower().strip()


def map_name(odds_name: str, known_players: list, cutoff: float = 0.75) -> str | None:
    """Mapea nombre de Odds API al nombre más cercano en JeffSackmann."""
    norm_map = {normalize(p): p for p in known_players}
    norm_query = normalize(odds_name)

    if norm_query in norm_map:
        return norm_map[norm_query]

    matches = difflib.get_close_matches(norm_query, norm_map.keys(), n=1, cutoff=cutoff)
    if matches:
        result = norm_map[matches[0]]
        if result != odds_name:
            logger.debug(f"Nombre mapeado: '{odds_name}' → '{result}'")
        return result

    logger.warning(f"Sin coincidencia para: '{odds_name}'")
    return None
