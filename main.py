from loguru import logger

from config import SPORTS_TENNIS, TOURNAMENT_SURFACE, TOURNAMENT_TOUR, BANKROLL
from src.data.odds_client import get_odds
from src.data.tennis_data import (
    download_atp_matches, download_wta_matches,
    calculate_player_stats, get_current_rankings,
    calculate_recent_form, calculate_h2h,
)
from src.data.database import init_db, save_match, save_pick, get_roi_summary
from src.models.tennis_engine import analyze_tennis_match
from src.notifications.telegram import send_picks, send_roi_summary


def run():
    logger.info("=== AI Tipster — Iniciando análisis ===")

    init_db()

    logger.info("Descargando datos históricos ATP...")
    atp_df = download_atp_matches()

    logger.info("Descargando datos históricos WTA...")
    wta_df = download_wta_matches()

    if atp_df.empty and wta_df.empty:
        logger.error("Sin datos ATP ni WTA. Abortando.")
        return []

    surfaces = ["Clay", "Hard", "Grass"]
    atp_cache    = {s: calculate_player_stats(atp_df, surface=s) for s in surfaces}
    wta_cache    = {s: calculate_player_stats(wta_df, surface=s) for s in surfaces}
    atp_rankings = get_current_rankings("atp")
    wta_rankings = get_current_rankings("wta")

    logger.info("Calculando forma reciente y H2H ATP...")
    atp_form = {s: calculate_recent_form(atp_df, surface=s) for s in surfaces}
    atp_h2h  = calculate_h2h(atp_df)

    logger.info("Calculando forma reciente y H2H WTA...")
    wta_form = {s: calculate_recent_form(wta_df, surface=s) for s in surfaces}
    wta_h2h  = calculate_h2h(wta_df)

    all_picks = []

    for sport in SPORTS_TENNIS:
        surface     = TOURNAMENT_SURFACE.get(sport, "Hard")
        tour        = TOURNAMENT_TOUR.get(sport, "ATP")
        stats_df    = (atp_cache    if tour == "ATP" else wta_cache)[surface]
        rankings    = (atp_rankings if tour == "ATP" else wta_rankings)
        recent_form = (atp_form     if tour == "ATP" else wta_form)[surface]
        h2h_data    = (atp_h2h      if tour == "ATP" else wta_h2h)

        logger.info(f"\n--- {sport} | {tour} | Superficie: {surface} ---")
        matches = get_odds(sport)

        if not matches:
            logger.info("Sin partidos disponibles.")
            continue

        logger.info(f"{len(matches)} partido(s) encontrado(s)")

        for match in matches:
            picks = analyze_tennis_match(
                match, stats_df,
                bankroll=BANKROLL, surface=surface, tour=tour,
                rankings=rankings, tournament=sport,
                recent_form=recent_form, h2h_data=h2h_data,
            )

            if not picks:
                continue

            # Guardar partido y picks en la DB
            first = picks[0]
            match_id = save_match({
                "tournament":    sport,
                "surface":       surface,
                "tour":          tour,
                "player1":       first["player1"],
                "player2":       first["player2"],
                "serve_pct_p1":  first["serve_pct_p1"],
                "serve_pct_p2":  first["serve_pct_p2"],
                "rank_pts_p1":   first["rank_pts_p1"],
                "rank_pts_p2":   first["rank_pts_p2"],
                "prob_serve":    first["prob_serve"],
                "prob_rank":     first["prob_rank"],
                "prob_final_p1": first["prob_final_p1"],
            })

            new_picks = 0
            for pick in picks:
                if save_pick(match_id, pick):
                    new_picks += 1

            if new_picks:
                logger.info(f"  {new_picks} pick(s) nuevos guardados en DB")

            all_picks.extend(picks)

    # Resumen en consola
    if all_picks:
        all_picks.sort(key=lambda x: x["ev"], reverse=True)
        logger.info(f"\n{'='*50}")
        logger.info(f"  {len(all_picks)} PICK(S) CON VALOR POSITIVO")
        logger.info(f"{'='*50}")
        for pick in all_picks:
            logger.info(
                f"PICK | {pick['outcome']} ({pick['match']}) | "
                f"{pick['bookmaker']} @ {pick['odd']} | "
                f"EV: {pick['ev']:+.2%} | Stake: €{pick['stake_eur']}"
            )
    else:
        logger.info("\n=== Sin valor detectado en los mercados actuales ===")

    # Enviar por Telegram (picks + ROI si hay historial)
    send_picks(all_picks)

    roi = get_roi_summary()
    if roi:
        send_roi_summary(roi)

    return all_picks


if __name__ == "__main__":
    run()
