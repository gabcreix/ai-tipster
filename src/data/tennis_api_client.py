"""
Cliente para api-tennis.com.

Proporciona rankings actuales, forma reciente y H2H en tiempo real
como complemento a los datos históricos de JeffSackmann.

Autenticación: query param APIkey=<token>
Documentación: https://api-tennis.com/documentation
"""
import requests
import unicodedata
from loguru import logger

from src.data.name_mapper import map_name, normalize

BASE_URL = "https://api.api-tennis.com/tennis/"


class TennisAPIClient:
    def __init__(self, api_key: str):
        self.api_key   = api_key
        self._standings: dict = {}  # cache {TOUR: {name: {rank, points, key}}}

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    def _get(self, method: str, **params):
        p = {"method": method, "APIkey": self.api_key, **params}
        try:
            r = requests.get(BASE_URL, params=p, timeout=10)
            data = r.json()
            if data.get("success") == 1:
                return data.get("result")
            logger.warning(f"api-tennis {method}: success=0 — {data}")
        except Exception as e:
            logger.error(f"api-tennis {method}: {e}")
        return None

    # ------------------------------------------------------------------
    # Rankings
    # ------------------------------------------------------------------

    def get_rankings(self, tour: str) -> dict:
        """
        Devuelve {player_name: {"rank": int, "points": int, "key": str}}.
        Compatible con get_current_rankings() de tennis_data.py.
        """
        tour_upper = tour.upper()
        if tour_upper in self._standings:
            return self._standings[tour_upper]

        result = self._get("get_standings", event_type=tour_upper)
        if not result:
            logger.warning(f"api-tennis: rankings {tour_upper} no disponibles")
            return {}

        out: dict = {}
        for entry in result:
            name = str(entry.get("player", "")).strip()
            if not name:
                continue
            try:
                out[name] = {
                    "rank":   int(entry.get("place",  0) or 0),
                    "points": int(entry.get("points", 0) or 0),
                    "key":    str(entry.get("player_key", "")),
                }
            except (ValueError, TypeError):
                continue

        logger.info(f"api-tennis: {len(out)} jugadores en rankings {tour_upper}")
        self._standings[tour_upper] = out
        return out

    # ------------------------------------------------------------------
    # Player key lookup
    # ------------------------------------------------------------------

    def _find_key(self, player_name: str, standings: dict) -> str | None:
        known  = list(standings.keys())
        mapped = map_name(player_name, known)
        if mapped and mapped in standings:
            return standings[mapped]["key"]
        logger.warning(f"api-tennis: no se encontró clave para '{player_name}'")
        return None

    # ------------------------------------------------------------------
    # H2H + recent form  (una sola llamada por partido)
    # ------------------------------------------------------------------

    def get_match_live_data(self, p1: str, p2: str, tour: str) -> dict:
        """
        Devuelve datos en tiempo real para un partido concreto::

            {
              "h2h_prob_p1": float,   # prob. de que p1 gane (0.5 si sin datos)
              "form_p1":     float,   # win-rate reciente p1 con decay exponencial
              "form_p2":     float,
              "h2h_matches": int,     # nº enfrentamientos directos encontrados
            }

        Usa un único endpoint get_H2H que devuelve H2H + últimos resultados
        de cada jugador por separado.
        """
        default = {"h2h_prob_p1": 0.5, "form_p1": 0.5, "form_p2": 0.5, "h2h_matches": 0}

        standings = self.get_rankings(tour)
        if not standings:
            return default

        key1 = self._find_key(p1, standings)
        key2 = self._find_key(p2, standings)
        if not key1 or not key2:
            return default

        result = self._get("get_H2H", first_player_key=key1, second_player_key=key2)
        if not result:
            return default

        out = dict(default)

        # ---------- H2H directo ----------
        h2h_matches = result.get("H2H") or []
        if len(h2h_matches) >= 2:
            p1_wins = sum(
                1 for m in h2h_matches
                if self._did_first_player_win(m)
            )
            total = len(h2h_matches)
            out["h2h_prob_p1"] = round(p1_wins / total, 4)
            out["h2h_matches"] = total
            logger.info(
                f"api-tennis H2H: {p1} {p1_wins}/{total} vs {p2}"
            )
        else:
            logger.info(f"api-tennis H2H: < 2 enfrentamientos para {p1} vs {p2}")

        # ---------- Forma reciente ----------
        for key, results_field, form_key, is_first in [
            (p1, "firstPlayerResults",  "form_p1", True),
            (p2, "secondPlayerResults", "form_p2", False),
        ]:
            matches = result.get(results_field) or []
            wins = self._parse_form(matches, is_first_player=True)
            if wins:
                n = len(wins)
                weights = [0.9 ** (n - 1 - i) for i in range(n)]
                out[form_key] = round(
                    sum(w * v for w, v in zip(weights, wins)) / sum(weights), 4
                )
                logger.info(
                    f"api-tennis forma {key}: {sum(wins):.0f}V/{len(wins)}P "
                    f"→ {out[form_key]:.0%} (decay)"
                )

        return out

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _did_first_player_win(match: dict) -> bool:
        """
        En el endpoint H2H, first_player_key es siempre nuestro p1.
        El campo 'result' tiene formato 'sets_p1:sets_p2' (e.g. '3:1').
        """
        # Preferir event_winner si está disponible
        winner = str(match.get("event_winner", "")).strip()
        first  = str(match.get("first_player", "")).strip()
        if winner and first:
            return normalize(winner) == normalize(first)

        # Fallback: parsear marcador de sets
        result = str(match.get("result", ""))
        parts  = result.split(":")
        if len(parts) == 2:
            try:
                return int(parts[0]) > int(parts[1])
            except ValueError:
                pass
        return False

    @staticmethod
    def _parse_form(matches: list, is_first_player: bool = True) -> list[float]:
        """
        Devuelve lista de 1.0 (victoria) / 0.0 (derrota) en orden cronológico.
        is_first_player=True → el primer número del marcador corresponde al jugador.
        """
        wins = []
        for m in matches:
            winner = str(m.get("event_winner", "")).strip()
            first  = str(m.get("event_first_player", "")).strip()

            won = None
            if winner and first:
                won = normalize(winner) == normalize(first) if is_first_player \
                    else normalize(winner) != normalize(first)
            else:
                result = str(m.get("result", ""))
                parts  = result.split(":")
                if len(parts) == 2:
                    try:
                        p1_sets = int(parts[0])
                        p2_sets = int(parts[1])
                        if p1_sets != p2_sets:
                            won = (p1_sets > p2_sets) == is_first_player
                    except ValueError:
                        pass

            if won is not None:
                wins.append(1.0 if won else 0.0)

        return wins
