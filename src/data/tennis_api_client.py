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
        for player_name, results_field, form_key, is_first in [
            (p1, "firstPlayerResults",  "form_p1", True),
            (p2, "secondPlayerResults", "form_p2", False),
        ]:
            raw_matches = result.get(results_field) or []
            # Log raw sample to help diagnose parsing issues
            if raw_matches:
                logger.debug(f"api-tennis {results_field} sample: {raw_matches[0]}")
            wins = self._parse_form(raw_matches, is_first_player=is_first)
            if wins:
                n = len(wins)
                weights = [0.9 ** (n - 1 - i) for i in range(n)]
                out[form_key] = round(
                    sum(w * v for w, v in zip(weights, wins)) / sum(weights), 4
                )
                logger.info(
                    f"api-tennis forma {player_name}: {sum(wins):.0f}V/{len(wins)}P "
                    f"→ {out[form_key]:.0%} (decay)"
                )

        return out

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _winner_is_first(match: dict) -> bool | None:
        """
        Determina si el primer jugador del partido ganó.
        Maneja tres formatos posibles del campo event_winner:
          1. Literal "First Player" / "Second Player"  (más común en estas APIs)
          2. Nombre del jugador → comparar con first_player
          3. Ausente → parsear marcador "sets_p1:sets_p2"
        """
        winner = str(match.get("event_winner", "")).strip()
        w_lower = winner.lower()

        # Formato 1: literal "First Player" / "Second Player"
        if "first" in w_lower and "player" in w_lower:
            return True
        if "second" in w_lower and "player" in w_lower:
            return False

        # Formato 2: nombre del ganador
        first = str(match.get("first_player", match.get("event_first_player", ""))).strip()
        if winner and first:
            return normalize(winner) == normalize(first)

        # Formato 3: marcador "3:1"
        result = str(match.get("result", ""))
        parts  = result.split(":")
        if len(parts) == 2:
            try:
                return int(parts[0]) > int(parts[1])
            except ValueError:
                pass

        return None

    @classmethod
    def _did_first_player_win(cls, match: dict) -> bool:
        result = cls._winner_is_first(match)
        return bool(result)

    @classmethod
    def _parse_form(cls, matches: list, is_first_player: bool = True) -> list[float]:
        """
        Devuelve lista de scores de dominancia [0.0, 1.0] en orden cronológico.
        Usa ratio de juegos ganados/jugados si hay scores de sets disponibles,
        si no cae a win/loss binario (0.0 / 1.0).
        """
        scores = []
        for m in matches:
            first_won = cls._winner_is_first(m)
            if first_won is None:
                continue

            dominance = cls._dominance(m, is_first_player)
            scores.append(dominance)

        return scores

    @staticmethod
    def _dominance(match: dict, is_first_player: bool) -> float:
        """
        Ratio de juegos ganados sobre juegos totales (perspectiva del jugador).
        Ejemplo: ganó 6-3 6-2 → (6+6)/(6+3+6+2) = 12/17 ≈ 0.71
        Ejemplo: perdió 3-6 2-6 → (3+2)/(3+6+2+6) = 5/17 ≈ 0.29
        Fallback: 1.0 si ganó, 0.0 si perdió (sin datos de sets).
        """
        sets = match.get("scores") or []
        if sets:
            first_games  = sum(int(s.get("score_first",  0) or 0) for s in sets)
            second_games = sum(int(s.get("score_second", 0) or 0) for s in sets)
            total = first_games + second_games
            if total > 0:
                ratio = first_games / total if is_first_player else second_games / total
                return round(ratio, 4)

        # Sin scores de sets: binario
        first_won = TennisAPIClient._winner_is_first(match)
        if first_won is None:
            return 0.5
        won = first_won if is_first_player else not first_won
        return 1.0 if won else 0.0

    # ------------------------------------------------------------------
    # Diagnóstico
    # ------------------------------------------------------------------

    def debug_h2h(self, p1: str, p2: str, tour: str, max_entries: int = 3):
        """
        Imprime el response crudo de get_H2H para ayudar a depurar el parsing.
        Uso:  client.debug_h2h("Andrey Rublev", "Arthur Fils", "ATP")
        """
        import json
        standings = self.get_rankings(tour)
        key1 = self._find_key(p1, standings)
        key2 = self._find_key(p2, standings)
        print(f"\n=== debug_h2h: {p1} ({key1}) vs {p2} ({key2}) ===")
        result = self._get("get_H2H", first_player_key=key1, second_player_key=key2)
        if not result:
            print("Sin respuesta de la API")
            return
        for section in ("H2H", "firstPlayerResults", "secondPlayerResults"):
            entries = result.get(section) or []
            print(f"\n-- {section} ({len(entries)} entradas) --")
            for e in entries[:max_entries]:
                print(json.dumps(e, ensure_ascii=False))
