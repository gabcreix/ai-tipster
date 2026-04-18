"""
CLI interactivo para marcar resultados de picks.
Uso: python results.py
"""
from loguru import logger
from src.data.database import get_pending_picks, update_result, get_roi_summary

COMMANDS = {"w": "won", "l": "lost", "v": "void", "s": None}


def _fmt_tournament(t: str) -> str:
    return t.replace("tennis_", "").replace("_", " ").title()


def run():
    picks = get_pending_picks()

    if not picks:
        print("\n✅ No hay picks pendientes de resultado.")
        return

    print(f"\n{'='*60}")
    print(f"  {len(picks)} PICK(S) PENDIENTE(S) DE RESULTADO")
    print(f"{'='*60}")

    marked = 0
    won_total = 0.0
    lost_total = 0.0

    for i, p in enumerate(picks, 1):
        tournament = _fmt_tournament(p["tournament"])
        match      = f"{p['player1']} vs {p['player2']}"
        outcome    = p["outcome"]
        bookmaker  = p["bookmaker"]
        odds       = p["odds"]
        stake      = p["stake_eur"]
        ev         = p["ev"]
        date       = p["created_at"][:10]

        print(f"\n[{i}/{len(picks)}] {date} | {tournament}")
        print(f"  {outcome} ({match})")
        print(f"  {bookmaker} @ {odds}  |  Stake: €{stake}  |  EV: {ev:+.1%}")
        print(f"  Posible ganancia: +€{round(stake*(odds-1),2)}  |  Pérdida: -€{stake}")

        while True:
            raw = input("  Resultado [w]on / [l]ost / [v]oid / [s]kip: ").strip().lower()
            if raw in COMMANDS:
                break
            print("  Escribe w, l, v o s.")

        result = COMMANDS[raw]
        if result is None:
            print("  → Skipped")
            continue

        update_result(p["id"], result)
        marked += 1

        if result == "won":
            profit = round(stake * (odds - 1), 2)
            won_total += profit
            print(f"  → ✅ WON  |  P&L: +€{profit}")
        elif result == "lost":
            lost_total += stake
            print(f"  → ❌ LOST  |  P&L: -€{stake}")
        else:
            print("  → ⚪ VOID  |  P&L: €0.00")

    # Resumen de la sesión
    if marked:
        net = won_total - lost_total
        print(f"\n{'='*60}")
        print(f"  {marked} pick(s) marcados esta sesión")
        print(f"  Ganado:  +€{won_total:.2f}")
        print(f"  Perdido: -€{lost_total:.2f}")
        print(f"  Neto:    {'+'if net>=0 else ''}€{net:.2f}")
        print(f"{'='*60}")

    # ROI acumulado
    roi = get_roi_summary()
    if roi:
        print("\n📊 ROI acumulado:")
        for r in roi:
            pct = r["roi_pct"] or 0
            icon = "🟢" if pct > 0 else "🔴"
            print(
                f"  {icon} {r['tour']} {r['surface']}  "
                f"{r['wins']}W/{r['losses']}L  |  "
                f"Staked €{r['staked']}  |  "
                f"P&L €{r['profit']:+.2f}  |  ROI {pct:+.1f}%"
            )


if __name__ == "__main__":
    run()
