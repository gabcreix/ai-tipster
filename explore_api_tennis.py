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

    # Fixtures — parámetros correctos: date_start / date_stop
    show("get_fixtures (ATP, ayer→hoy)",
         call("get_fixtures", event_type="ATP", date_start=yesterday, date_stop=today))

    show("get_fixtures (ATP, semana)",
         call("get_fixtures", event_type="ATP", date_start=week_ago, date_stop=today))

    show("get_fixtures (WTA, semana)",
         call("get_fixtures", event_type="WTA", date_start=week_ago, date_stop=today))

    # Livescore (singular)
    show("get_livescore (ATP)", call("get_livescore", event_type="ATP"))
    show("get_livescore (WTA)", call("get_livescore", event_type="WTA"))

    # Endpoints nuevos descubiertos en el error 404
    show("get_events (ATP)",       call("get_events",      event_type="ATP"))
    show("get_tournaments (ATP)",  call("get_tournaments", event_type="ATP"))
