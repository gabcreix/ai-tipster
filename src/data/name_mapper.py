import difflib
import time
import unicodedata
from loguru import logger

_alias_cache: dict | None = None
_alias_cache_ts: float = 0
_CACHE_TTL = 300  # segundos


def _get_aliases() -> dict:
    """Carga aliases de DB con caché en memoria (refresca cada 5 min)."""
    global _alias_cache, _alias_cache_ts
    now = time.time()
    if _alias_cache is None or now - _alias_cache_ts > _CACHE_TTL:
        try:
            from src.data.database import get_aliases
            _alias_cache = get_aliases()
            _alias_cache_ts = now
        except Exception:
            _alias_cache = {}
    return _alias_cache


def invalidate_alias_cache():
    """Fuerza recarga de aliases en el próximo map_name (llamar tras guardar alias)."""
    global _alias_cache
    _alias_cache = None


def normalize(name: str) -> str:
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    return name.lower().strip()


def _last_name(name: str) -> str:
    parts = name.strip().split()
    return parts[-1] if parts else name


def _last_names_compatible(query: str, candidate: str, cutoff: float = 0.6) -> bool:
    lq = _last_name(query)
    lc = _last_name(candidate)
    ratio = difflib.SequenceMatcher(None, lq, lc).ratio()
    return ratio >= cutoff


def map_name(odds_name: str, known_players: list, cutoff: float = 0.85, tour: str = None) -> str | None:
    """Mapea nombre de Odds API al nombre más cercano en JeffSackmann.

    Prioridad:
    1. player_aliases (DB) — confirmado manualmente
    2. Coincidencia exacta normalizada
    3. Fuzzy match con verificación de apellido (cutoff 0.85)

    Si no hay coincidencia, registra el nombre en unresolved_names para
    revisión con `python names.py`.
    """
    # 1. Alias confirmados en DB
    aliases = _get_aliases()
    if odds_name in aliases:
        return aliases[odds_name]  # None = jugador nuevo sin datos históricos

    # 2. Coincidencia exacta normalizada
    norm_map = {normalize(p): p for p in known_players}
    norm_query = normalize(odds_name)

    if norm_query in norm_map:
        return norm_map[norm_query]

    # 3. Fuzzy match con verificación de apellido
    matches = difflib.get_close_matches(norm_query, norm_map.keys(), n=3, cutoff=cutoff)
    for match in matches:
        if _last_names_compatible(norm_query, match):
            result = norm_map[match]
            if result != odds_name:
                logger.debug(f"Nombre mapeado: '{odds_name}' → '{result}'")
            return result

    # 4. Sin coincidencia — registrar para revisión manual
    try:
        from src.data.database import log_unresolved
        log_unresolved(odds_name, tour)
    except Exception:
        pass
    logger.warning(f"Sin coincidencia para: '{odds_name}'")
    return None
