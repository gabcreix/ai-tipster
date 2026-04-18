import pandas as pd
from math import comb
from loguru import logger
from config import MIN_EV_THRESHOLD, KELLY_FRACTION, MAX_STAKE_EUR, RANK_WEIGHT
from src.data.name_mapper import map_name

DEFAULT_SERVE_WIN_PCT_ATP = 0.62
DEFAULT_SERVE_WIN_PCT_WTA = 0.55
DEFAULT_SERVE_WIN_PCT = DEFAULT_SERVE_WIN_PCT_ATP  # fallback

DEFAULT_RANK_POINTS = 100  # puntos asignados a jugadores sin ranking conocido


def prob_win_by_ranking(points_p1: int, points_p2: int) -> float:
    """Bradley-Terry: prob de que P1 gane basado en puntos de ranking."""
    total = points_p1 + points_p2
    if total == 0:
        return 0.5
    return round(points_p1 / total, 4)


def prob_win_game(p: float) -> float:
    """
    Probabilidad exacta de ganar un game al servicio.
    Basada en cadenas de Markov (fórmula cerrada).
    p = prob de ganar un punto al servicio.
    """
    q = 1 - p
    # Sin deuce: ganar 4-0, 4-1, 4-2
    no_deuce = (
        p**4 * (
            1
            + 4 * q
            + 10 * q**2
        )
    )
    # Con deuce: llegar a 3-3 y ganar
    deuce = comb(6, 3) * (p * q)**3 * (p**2 / (p**2 + q**2))
    return no_deuce + deuce


def prob_win_tiebreak(p1_serve: float, p2_serve: float) -> float:
    """
    Probabilidad de que P1 gane el tiebreak.
    p1_serve = prob P1 gana punto en su servicio
    p2_serve = prob P2 gana punto en su servicio
    En el tiebreak los puntos se alternan cada 2.
    Usamos prob media ponderada.
    """
    p = (p1_serve + (1 - p2_serve)) / 2
    q = 1 - p
    # Llegar a 6-6 y ganar con 2 de diferencia
    no_tb_extra = sum(
        comb(6 + k, k) * p**(7) * q**k
        for k in range(6)
    )
    tb_extra = comb(12, 6) * (p * q)**6 * (p**2 / (p**2 + q**2))
    return min(no_tb_extra + tb_extra, 1.0)


def prob_win_set(p1_serve: float, p2_serve: float) -> float:
    """
    Probabilidad de que P1 gane un set.
    Iteramos sobre todos los marcadores posibles de set.
    """
    g1 = prob_win_game(p1_serve)   # P1 gana game al servicio
    g2 = prob_win_game(p2_serve)   # P2 gana game al servicio
    r1 = 1 - g2                    # P1 gana game al resto
    r2 = 1 - g1                    # P2 gana game al resto

    # En cada game, el servicio alterna. Simplificamos:
    # P1 gana cualquier game = media de hold y break
    p_game_p1 = (g1 + r1) / 2
    p_game_p2 = (g2 + r2) / 2

    # Re-normalizar para que sumen 1
    total = p_game_p1 + p_game_p2
    p = p_game_p1 / total

    q = 1 - p

    prob = 0.0

    # P1 gana 6-k para k in 0..4
    for k in range(5):
        prob += comb(5 + k, k) * p**6 * q**k

    # P1 gana 7-5
    prob += comb(10, 5) * p**6 * q**5 * p

    # P1 gana tiebreak (llegan a 6-6)
    # 2*C(10,5) excluye caminos donde uno ya ganó 6 antes de llegar a 6-6
    p_66 = 2 * comb(10, 5) * p**6 * q**6
    prob += p_66 * prob_win_tiebreak(p1_serve, p2_serve)

    return min(max(prob, 0), 1)


def prob_win_match(
    serve_p1: float,
    serve_p2: float,
    best_of_5: bool = False
) -> float:
    """
    Probabilidad de que P1 gane el partido.
    Modelo jerárquico: punto → game → set → partido.
    """
    p_set = prob_win_set(serve_p1, serve_p2)
    q_set = 1 - p_set
    sets_needed = 3 if best_of_5 else 2

    prob = 0.0
    for k in range(sets_needed):
        # Ganar sets_needed sets perdiendo k
        prob += comb(sets_needed + k - 1, k) * p_set**sets_needed * q_set**k

    return round(min(max(prob, 0), 1), 4)


def calculate_ev(prob: float, odd: float) -> float:
    return round((prob * odd) - 1, 4)


def kelly_stake(prob: float, odd: float, bankroll: float) -> float:
    q = 1 - prob
    b = odd - 1
    if b <= 0:
        return 0
    kelly = (b * prob - q) / b
    stake = kelly * KELLY_FRACTION * bankroll
    return round(min(max(stake, 0), MAX_STAKE_EUR), 2)


def analyze_tennis_match(
    match: dict,
    stats_df: pd.DataFrame,
    bankroll: float = 1000,
    surface: str = "Clay",
    tour: str = "ATP",
    rankings: dict = None,
    tournament: str = "",
) -> list:
    picks = []
    p1 = match["home_team"]
    p2 = match["away_team"]
    default_pct = DEFAULT_SERVE_WIN_PCT_WTA if tour == "WTA" else DEFAULT_SERVE_WIN_PCT_ATP
    rankings = rankings or {}

    def get_serve_pct(player: str) -> float:
        known = stats_df["player"].tolist() if not stats_df.empty else []
        mapped = map_name(player, known) or player if known else player
        if not stats_df.empty:
            row = stats_df[
                (stats_df["player"] == mapped) &
                (stats_df["surface"].str.lower() == surface.lower())
            ]
            if not row.empty:
                return float(row.iloc[0]["serve_win_pct"])
        logger.warning(f"Sin datos de servicio para {player} en {surface}, usando media {tour}")
        return default_pct

    def get_rank_points(player: str) -> int:
        known_names = list(rankings.keys())
        mapped = map_name(player, known_names) or player if known_names else player
        data = rankings.get(mapped, {})
        pts = data.get("points", 0)
        if pts == 0:
            logger.warning(f"Sin ranking para {player}, usando {DEFAULT_RANK_POINTS} pts")
            return DEFAULT_RANK_POINTS
        return pts

    serve_p1 = get_serve_pct(p1)
    serve_p2 = get_serve_pct(p2)

    pts_p1 = get_rank_points(p1)
    pts_p2 = get_rank_points(p2)

    prob_serve = prob_win_match(serve_p1, serve_p2)
    prob_rank  = prob_win_by_ranking(pts_p1, pts_p2)

    # Blend: 50% modelo de servicio + 50% modelo de ranking
    prob_p1 = round(RANK_WEIGHT * prob_rank + (1 - RANK_WEIGHT) * prob_serve, 4)
    prob_p2 = round(1 - prob_p1, 4)

    logger.info(
        f"{p1} (serve {serve_p1:.1%}, {pts_p1}pts) vs "
        f"{p2} (serve {serve_p2:.1%}, {pts_p2}pts)"
    )
    logger.info(
        f"  Servicio: {prob_serve:.2%} | Ranking: {prob_rank:.2%} | "
        f"Final: {p1} {prob_p1:.2%} / {p2} {prob_p2:.2%}"
    )

    prob_map = {p1: prob_p1, p2: prob_p2}

    for bookmaker in match.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market["key"] != "h2h":
                continue
            for outcome in market["outcomes"]:
                name = outcome["name"]
                odd = outcome["price"]
                prob = prob_map.get(name)

                if prob is None:
                    continue

                ev = calculate_ev(prob, odd)
                market_prob = round(1 / odd, 4)

                logger.info(
                    f"  {name} | Odd: {odd} | "
                    f"Nuestra prob: {prob:.2%} | "
                    f"Mercado implica: {market_prob:.2%} | "
                    f"EV: {ev:+.2%}"
                )

                if ev >= MIN_EV_THRESHOLD:
                    stake = kelly_stake(prob, odd, bankroll)
                    picks.append({
                        # identificación
                        "match":         f"{p1} vs {p2}",
                        "player1":       p1,
                        "player2":       p2,
                        "tournament":    tournament,
                        "surface":       surface,
                        "tour":          tour,
                        # pick
                        "bookmaker":     bookmaker["title"],
                        "outcome":       name,
                        "odd":           odd,
                        "our_prob":      prob,
                        "market_prob":   market_prob,
                        "ev":            ev,
                        "stake_eur":     stake,
                        # features del modelo (para la DB)
                        "serve_pct_p1":  serve_p1,
                        "serve_pct_p2":  serve_p2,
                        "rank_pts_p1":   pts_p1,
                        "rank_pts_p2":   pts_p2,
                        "prob_serve":    prob_serve,
                        "prob_rank":     prob_rank,
                        "prob_final_p1": prob_p1,
                    })

    return picks


if __name__ == "__main__":
    test_match = {
        "home_team": "Stefanos Tsitsipas",
        "away_team": "Hubert Hurkacz",
        "bookmakers": [{
            "title": "Pinnacle",
            "markets": [{
                "key": "h2h",
                "outcomes": [
                    {"name": "Stefanos Tsitsipas", "price": 1.85},
                    {"name": "Hubert Hurkacz",      "price": 2.10},
                ]
            }]
        }]
    }

    from src.data.tennis_data import download_atp_matches, calculate_player_stats
    
    df = download_atp_matches()
    stats = calculate_player_stats(df, surface="Clay")

    picks = analyze_tennis_match(test_match, stats, bankroll=1000, surface="Clay")

    if picks:
        logger.info(f"\n→ {len(picks)} pick(s) con valor:")
        for p in picks:
            logger.info(f"  PICK: {p['outcome']} @ {p['odd']} | EV: {p['ev']:+.2%} | Stake: €{p['stake_eur']}")
    else:
        logger.info("\n→ Sin valor detectado en este partido")