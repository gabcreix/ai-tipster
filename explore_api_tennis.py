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

    # Buscar partidos ATP/WTA Singles finalizados esta semana
    all_fixtures = call("get_fixtures", event_type="ATP", date_start=week_ago, date_stop=today) or []

    atp_singles = [
        f for f in all_fixtures
        if f.get("event_status") == "Finished"
        and "Singles" in f.get("event_type_type", "")
        and "Atp" in f.get("event_type_type", "")
    ]
    wta_singles = [
        f for f in all_fixtures
        if f.get("event_status") == "Finished"
        and "Singles" in f.get("event_type_type", "")
        and "Wta" in f.get("event_type_type", "")
    ]

    print(f"\nATP Singles finalizados esta semana: {len(atp_singles)}")
    print(f"WTA Singles finalizados esta semana: {len(wta_singles)}")

    # Inspeccionar statistics, scores y pointbypoint de los primeros 3 ATP
    print("\n" + "="*60)
    print("  DETALLE statistics / scores / pointbypoint")
    print("="*60)

    for match in atp_singles[:3]:
        p1   = match["event_first_player"]
        p2   = match["event_second_player"]
        res  = match["event_final_result"]
        ttype = match["event_type_type"]
        print(f"\n--- {p1} vs {p2} ({res}) [{ttype}] ---")
        print(f"  scores:      {json.dumps(match.get('scores'), ensure_ascii=False)}")
        print(f"  statistics:  {json.dumps(match.get('statistics'), ensure_ascii=False)[:600]}")
        print(f"  pointbypoint:{json.dumps(match.get('pointbypoint'), ensure_ascii=False)[:300]}")

    # Tipos de eventos disponibles (para saber cómo filtrar)
    print("\n" + "="*60)
    print("  TIPOS DE EVENTOS (get_events)")
    print("="*60)
    events = call("get_events", event_type="ATP") or []
    for e in events:
        print(f"  key={e['event_type_key']:4d}  {e['event_type_type']}")
