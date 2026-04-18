import difflib
import unicodedata
from loguru import logger


def normalize(name: str) -> str:
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    return name.lower().strip()


def _last_name(name: str) -> str:
    parts = name.strip().split()
    return parts[-1] if parts else name


def _last_names_compatible(query: str, candidate: str, cutoff: float = 0.6) -> bool:
    """Verifica que los apellidos sean suficientemente similares."""
    lq = _last_name(query)
    lc = _last_name(candidate)
    ratio = difflib.SequenceMatcher(None, lq, lc).ratio()
    return ratio >= cutoff


def map_name(odds_name: str, known_players: list, cutoff: float = 0.85) -> str | None:
    """Mapea nombre de Odds API al nombre más cercano en JeffSackmann.

    Usa cutoff alto (0.85) + verificación de apellido para evitar falsos
    positivos como 'Rafael Jodar' → 'Rafael Nadal'.
    """
    norm_map = {normalize(p): p for p in known_players}
    norm_query = normalize(odds_name)

    if norm_query in norm_map:
        return norm_map[norm_query]

    matches = difflib.get_close_matches(norm_query, norm_map.keys(), n=3, cutoff=cutoff)
    for match in matches:
        if _last_names_compatible(norm_query, match):
            result = norm_map[match]
            if result != odds_name:
                logger.debug(f"Nombre mapeado: '{odds_name}' → '{result}'")
            return result

    logger.warning(f"Sin coincidencia para: '{odds_name}'")
    return None
