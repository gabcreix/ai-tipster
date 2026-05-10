"""
CLI interactivo para marcar resultados de picks.
Uso: python results.py
"""
from loguru import logger
from src.data.database import get_pending_picks, update_result, get_roi_summary

COMMANDS = {"w": "won", "l": "lost", "v": "void", "s": None}


def auto_mark_from_history() -> int:
    """
    Intenta marcar picks pendientes automáticamente cotejando con match_history.
    Devuelve el número de picks marcados.
    """
    from datetime import datetime
    from src.data.database import load_matches_from_db
    from src.data.name_mapper import map_name, normalize

    picks = get_pending_picks()
    if not picks:
        return 0

    current_year = datetime.now().year
    results_df = load_matches_from_db(years=[current_year - 1, current_year])
    if results_df.empty:
        return 0

    known_names = (
        results_df["winner_name"].dropna().tolist()
        + results_df["loser_name"].dropna().tolist()
    )

    marked = 0
    for pick in picks:
        p1      = pick["player1"]
        p2      = pick["player2"]
        outcome = pick["outcome"]
        pick_date = int(str(pick["created_at"])[:10].replace("-", ""))

        # Solo partidos jugados después de crear el pick
        candidates = results_df[results_df["tourney_date"] >= pick_date]
        if candidates.empty:
            continue

        mp1 = map_name(p1, known_names) or p1
        mp2 = map_name(p2, known_names) or p2

        match_rows = candidates[
            ((candidates["winner_name"] == mp1) | (candidates["loser_name"] == mp1)) &
            ((candidates["winner_name"] == mp2) | (candidates["loser_name"] == mp2))
        ]
        if match_rows.empty:
            continue

        row    = match_rows.sort_values("tourney_date").iloc[-1]
        winner = row["winner_name"]
        loser  = row["loser_name"]

        m_outcome = map_name(outcome, [winner, loser]) or outcome
        if normalize(m_outcome) == normalize(str(winner)):
            update_result(pick["id"], "won")
            marked += 1
            logger.info(f"  Auto: WON — {outcome} ({p1} vs {p2})")
        elif normalize(m_outcome) == normalize(str(loser)):
            update_result(pick["id"], "lost")
            marked += 1
            logger.info(f"  Auto: LOST — {outcome} ({p1} vs {p2})")

    return marked


def _fmt_tournament(t: str) -> str:
    return t.replace("tennis_", "").replace("_", " ").title()


def run():
    auto = auto_mark_from_history()
    if auto:
        print(f"\n  {auto} pick(s) marcados automáticamente desde historial.")

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
    import argparse
    parser = argparse.ArgumentParser(description="Marcar resultados de picks")
    parser.add_argument("--auto", action="store_true", help="Solo marcado automático, sin interacción")
    args = parser.parse_args()

    if args.auto:
        from src.data.database import init_db
        init_db()
        n = auto_mark_from_history()
        if n:
            logger.info(f"Auto-marcado: {n} pick(s) cerrados")
        else:
            logger.info("Auto-marcado: sin picks nuevos para cerrar")
    else:
        run()
