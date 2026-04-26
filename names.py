"""
CLI para normalizar nombres de jugadores.
Mapea nombres de the-odds-api a nombres canónicos de JeffSackmann.

Uso:
  python names.py            # revisa nombres sin resolver
  python names.py --list     # muestra aliases ya guardados
"""
import argparse
import difflib

from loguru import logger

from src.data.database import init_db, get_unresolved_names, save_alias, get_aliases
from src.data.name_mapper import normalize, invalidate_alias_cache


def _load_known_players() -> dict:
    """Carga nombres canónicos de match_history agrupados por tour."""
    from src.data.database import get_connection
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT winner_name AS name, tour FROM match_history WHERE winner_name IS NOT NULL
            UNION
            SELECT loser_name  AS name, tour FROM match_history WHERE loser_name  IS NOT NULL
        """).fetchall()

    result: dict[str, list] = {"ATP": [], "WTA": [], "ALL": []}
    seen: set = set()
    for r in rows:
        name = r["name"]
        tour = r["tour"] or "ALL"
        if not name or name in seen:
            continue
        seen.add(name)
        result.setdefault(tour, []).append(name)
        result["ALL"].append(name)
    return result


def _get_suggestions(odds_name: str, candidates: list, n: int = 5, cutoff: float = 0.40) -> list:
    """Devuelve [(nombre, similitud)] ordenado por similitud descendente."""
    norm_q   = normalize(odds_name)
    norm_map = {normalize(c): c for c in candidates}
    matches  = difflib.get_close_matches(norm_q, norm_map.keys(), n=n, cutoff=cutoff)
    result   = []
    for m in matches:
        score = difflib.SequenceMatcher(None, norm_q, m).ratio()
        result.append((norm_map[m], score))
    return sorted(result, key=lambda x: x[1], reverse=True)


def cmd_list():
    """Muestra todos los alias guardados."""
    aliases = get_aliases()
    if not aliases:
        print("\n  No hay aliases guardados todavía.")
        return
    print(f"\n  {'Nombre Odds API':35s} → {'Nombre canónico':35s}")
    print(f"  {'-'*75}")
    for odds, canonical in sorted(aliases.items()):
        canon_str = canonical if canonical is not None else "(nuevo jugador — sin datos)"
        print(f"  {odds:35s} → {canon_str}")
    print(f"\n  Total: {len(aliases)} alias")


def cmd_resolve():
    """Revisa nombres sin resolver de forma interactiva."""
    names = get_unresolved_names()

    if not names:
        print("\n✅ No hay nombres sin resolver.")
        return

    known  = _load_known_players()
    total  = len(names)
    saved  = 0

    print(f"\n{'='*62}")
    print(f"  {total} NOMBRE(S) SIN RESOLVER")
    print(f"{'='*62}")
    print("  Opciones: número · nombre completo · [n]uevo jugador · [s]altar\n")

    for i, row in enumerate(names, 1):
        odds_name = row["odds_name"]
        tour      = row["tour"] or "?"
        seen      = row["seen_count"]

        # Buscar en el pool del tour correspondiente primero, luego en ALL
        tour_pool = known.get(tour if tour in known else "ALL", [])
        all_pool  = [p for p in known["ALL"] if p not in tour_pool]
        suggestions = _get_suggestions(odds_name, tour_pool + all_pool)

        print(f"[{i}/{total}] '{odds_name}' — {tour} — visto {seen}x")
        for j, (name, score) in enumerate(suggestions, 1):
            print(f"  {j}. {name}  ({score:.0%})")

        while True:
            raw = input("  > ").strip()

            if raw.lower() == "s":
                print("  → Saltado")
                break

            elif raw.lower() == "n":
                save_alias(odds_name, canonical=None, tour=tour)
                invalidate_alias_cache()
                saved += 1
                print("  → Marcado como jugador nuevo (sin datos históricos)")
                break

            elif raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(suggestions):
                    canonical = suggestions[idx][0]
                    save_alias(odds_name, canonical=canonical, tour=tour)
                    invalidate_alias_cache()
                    saved += 1
                    print(f"  → '{odds_name}' → '{canonical}'")
                    break
                else:
                    print(f"  Número fuera de rango (1-{len(suggestions)}). Inténtalo de nuevo.")

            elif raw:
                save_alias(odds_name, canonical=raw, tour=tour)
                invalidate_alias_cache()
                saved += 1
                print(f"  → '{odds_name}' → '{raw}'")
                break

            else:
                print("  Escribe un número, un nombre, [n] o [s].")

        print()

    print(f"{'='*62}")
    print(f"  {saved}/{total} alias guardados en esta sesión.")
    print(f"{'='*62}")


def run():
    parser = argparse.ArgumentParser(
        description="Normaliza nombres de jugadores (the-odds-api ↔ JeffSackmann)"
    )
    parser.add_argument(
        "--list", action="store_true",
        help="Muestra los aliases ya guardados y sale",
    )
    args = parser.parse_args()

    init_db()

    if args.list:
        cmd_list()
    else:
        cmd_resolve()


if __name__ == "__main__":
    run()
