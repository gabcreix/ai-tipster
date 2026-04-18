"""
Valida que los rankings de api-tennis.com coincidan con el orden ATP/WTA real.
Uso: python validate_rankings.py
"""
from config import TENNIS_API_KEY
from src.data.tennis_api_client import TennisAPIClient
from src.data.name_mapper import map_name

# Rankings ATP reales aproximados (abril 2026) para validación manual
EXPECTED_ATP_TOP10 = [
    "Jannik Sinner", "Carlos Alcaraz", "Alexander Zverev",
    "Novak Djokovic", "Casper Ruud", "Taylor Fritz",
    "Arthur Fils", "Andrey Rublev", "Daniil Medvedev", "Hubert Hurkacz",
]

EXPECTED_WTA_TOP10 = [
    "Aryna Sabalenka", "Iga Swiatek", "Coco Gauff",
    "Elena Rybakina", "Jasmine Paolini", "Mirra Andreeva",
    "Daria Kasatkina", "Emma Navarro", "Barbora Krejcikova", "Karolina Muchova",
]


def validate(tour: str, expected: list):
    print(f"\n{'='*60}")
    print(f"  VALIDACIÓN RANKINGS {tour}")
    print(f"{'='*60}")

    c = TennisAPIClient(TENNIS_API_KEY)
    rankings = c.get_rankings(tour)

    if not rankings:
        print("  ❌ No se pudieron obtener rankings")
        return

    sorted_players = sorted(rankings.items(), key=lambda x: x[1]["rank"])

    print(f"\n  Top 15 según api-tennis.com:")
    for name, data in sorted_players[:15]:
        print(f"    #{data['rank']:3d}  {name:35s}  {data['points']:6d} pts")

    print(f"\n  Comparación con ranking real estimado:")
    all_names = [n for n, _ in sorted_players]
    for i, expected_name in enumerate(expected, 1):
        mapped = map_name(expected_name, all_names)
        if mapped and mapped in rankings:
            api_rank = rankings[mapped]["rank"]
            api_pts  = rankings[mapped]["points"]
            diff     = api_rank - i
            flag     = "✅" if abs(diff) <= 2 else ("⬆️" if diff < 0 else "⬇️")
            print(f"    {flag} {expected_name:25s} → real ~#{i:2d}  api-tennis #{api_rank:3d}  ({diff:+d})  {api_pts} pts")
        else:
            print(f"    ❌ {expected_name:25s} → NO ENCONTRADO en api-tennis")


if __name__ == "__main__":
    validate("ATP", EXPECTED_ATP_TOP10)
    validate("WTA", EXPECTED_WTA_TOP10)
