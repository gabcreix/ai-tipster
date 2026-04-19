"""
Explora los endpoints disponibles de api-tennis.com.
Uso: python explore_api_tennis.py
"""
import json
import requests
from config import TENNIS_API_KEY

BASE = "https://api.api-tennis.com/tennis/"


def call(method: str, **params):
    p = {"method": method, "APIkey": TENNIS_API_KEY, **params}
    try:
        r = requests.get(BASE, params=p, timeout=15)
        data = r.json()
        if data.get("success") == 1:
            return data.get("result")
        print(f"  [{method}] success=0: {str(data)[:200]}")
    except Exception as e:
        print(f"  [{method}] error: {e}")
    return None


def show(method: str, result):
    if result is None:
        print(f"\n{method}: SIN RESPUESTA")
        return
    if isinstance(result, list):
        print(f"\n{method}: {len(result)} entradas")
        if result:
            print(f"  Campos: {list(result[0].keys())}")
            print(f"  Ejemplo: {json.dumps(result[0], ensure_ascii=False, indent=2)[:600]}")
    else:
        print(f"\n{method}: {type(result).__name__} → {str(result)[:300]}")


if __name__ == "__main__":
    from datetime import date, timedelta
    today     = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    week_ago  = (date.today() - timedelta(days=7)).isoformat()

    print("=" * 60)
    print("  EXPLORACIÓN api-tennis.com")
    print("=" * 60)

    # Endpoints básicos
    show("get_leagues",   call("get_leagues"))
    show("get_countries", call("get_countries"))

    # Fixtures / resultados
    show("get_fixtures (ATP, hoy)",
         call("get_fixtures", event_type="ATP", from_date=today, to_date=today))

    show("get_fixtures (ATP, ayer)",
         call("get_fixtures", event_type="ATP", from_date=yesterday, to_date=yesterday))

    show("get_fixtures (ATP, última semana)",
         call("get_fixtures", event_type="ATP", from_date=week_ago, to_date=today))

    show("get_fixtures (WTA, última semana)",
         call("get_fixtures", event_type="WTA", from_date=week_ago, to_date=today))

    # Livescores
    show("get_livescores (ATP)", call("get_livescores", event_type="ATP"))
    show("get_livescores (WTA)", call("get_livescores", event_type="WTA"))
