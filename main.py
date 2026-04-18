from loguru import logger

from config import SPORTS_TENNIS, TOURNAMENT_SURFACE, TOURNAMENT_TOUR, BANKROLL
from src.data.odds_client import get_odds
from src.data.tennis_data import download_atp_matches, download_wta_matches, calculate_player_stats, get_current_rankings
from src.models.tennis_engine import analyze_tennis_match


def run():
    logger.info("=== AI Tipster — Iniciando análisis ===")

    logger.info("Descargando datos históricos ATP...")
    atp_df = download_atp_matches()

    logger.info("Descargando datos históricos WTA...")
    wta_df = download_wta_matches()

    if atp_df.empty and wta_df.empty:
        logger.error("Sin datos ATP ni WTA. Abortando.")
        return []

    surfaces = ["Clay", "Hard", "Grass"]
    atp_cache = {s: calculate_player_stats(atp_df, surface=s) for s in surfaces}
    wta_cache = {s: calculate_player_stats(wta_df, surface=s) for s in surfaces}

    atp_rankings = get_current_rankings("atp")
    wta_rankings = get_current_rankings("wta")

    all_picks = []

    for sport in SPORTS_TENNIS:
        surface = TOURNAMENT_SURFACE.get(sport, "Hard")
        tour = TOURNAMENT_TOUR.get(sport, "ATP")
        stats_df  = (atp_cache    if tour == "ATP" else wta_cache)[surface]
        rankings  = atp_rankings if tour == "ATP" else wta_rankings

        logger.info(f"\n--- {sport} | {tour} | Superficie: {surface} ---")
        matches = get_odds(sport)

        if not matches:
            logger.info("Sin partidos disponibles.")
            continue

        logger.info(f"{len(matches)} partido(s) encontrado(s)")

        for match in matches:
            picks = analyze_tennis_match(
                match, stats_df, bankroll=BANKROLL,
                surface=surface, tour=tour, rankings=rankings,
            )
            all_picks.extend(picks)

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

    return all_picks


if __name__ == "__main__":
    run()
