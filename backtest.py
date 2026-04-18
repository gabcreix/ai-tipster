"""
Backtesting del modelo de tenis sobre datos JeffSackmann 2024.

Split limpio:
  - Train (serve stats): partidos ATP/WTA 2022-2023
  - Test:                partidos ATP/WTA 2024
  - Rankings:            winner_rank / loser_rank de cada partido
                         (datos contemporáneos, sin lookahead)

Solo se evalúan los componentes disponibles históricamente:
  serve_model + ranking_model  (sin form ni H2H).
"""
import pandas as pd
from loguru import logger

from src.data.tennis_data import (
    _download_matches, calculate_player_stats,
    BASE_URL_ATP, BASE_URL_WTA,
)
from src.models.tennis_engine import prob_win_match, prob_win_by_ranking
from config import SERVE_WEIGHT, RANK_WEIGHT, MIN_EV_THRESHOLD, MAX_EV

SURFACES = ["Clay", "Hard", "Grass"]
DEFAULT_SERVE_ATP = 0.62
DEFAULT_SERVE_WTA = 0.55


# ── helpers ──────────────────────────────────────────────────────────────────

def _rank_to_pts(rank) -> float:
    """Convierte posición de ranking en 'puntos' para Bradley-Terry.
    Rank 1 → 10000 pts, rank 100 → 100 pts, etc.
    """
    try:
        r = float(rank)
        return 10_000 / r if r > 0 else 0
    except (ValueError, TypeError):
        return 0


def _get_serve(player: str, stats: pd.DataFrame, default: float) -> float:
    if stats.empty:
        return default
    row = stats[stats["player"] == player]
    return float(row.iloc[0]["serve_win_pct"]) if not row.empty else default


def _surface_key(surface: str) -> str:
    """Normaliza la superficie al formato de calculate_player_stats."""
    return surface.strip().capitalize()


# ── backtest por circuito ─────────────────────────────────────────────────────

def backtest_tour(base_url: str, prefix: str, default_serve: float):
    logger.info(f"\n{'='*55}")
    logger.info(f"  BACKTEST {prefix.upper()} — train 2022-23 / test 2024")
    logger.info(f"{'='*55}")

    train_df = _download_matches(base_url, prefix, [2022, 2023])
    test_df  = _download_matches(base_url, prefix, [2024])

    if train_df.empty or test_df.empty:
        logger.error("Sin datos suficientes para el backtest")
        return

    # Estadísticas de servicio por superficie (solo datos de entrenamiento)
    serve_stats = {s: calculate_player_stats(train_df, surface=s) for s in SURFACES}

    # Filtrar filas con datos mínimos necesarios
    needed = ["winner_name", "loser_name", "surface", "winner_rank", "loser_rank"]
    test_df = test_df.dropna(subset=needed)
    test_df = test_df[test_df["winner_rank"].astype(float) > 0]
    test_df = test_df[test_df["loser_rank"].astype(float) > 0]

    if test_df.empty:
        logger.error("Sin filas válidas en test (winner_rank/loser_rank ausentes)")
        return

    logger.info(f"  {len(test_df)} partidos de test con ranking disponible")

    records = []
    for _, row in test_df.iterrows():
        p1     = row["winner_name"]   # p1 = ganador real
        p2     = row["loser_name"]
        surf   = _surface_key(row["surface"])
        if surf not in serve_stats:
            continue

        stats = serve_stats[surf]
        serve_p1 = _get_serve(p1, stats, default_serve)
        serve_p2 = _get_serve(p2, stats, default_serve)

        pts_p1 = _rank_to_pts(row["winner_rank"])
        pts_p2 = _rank_to_pts(row["loser_rank"])

        prob_serve = prob_win_match(serve_p1, serve_p2)
        prob_rank  = prob_win_by_ranking(pts_p1, pts_p2)

        # Blend normalizado (solo serve + rank, sin form ni H2H)
        w_total = SERVE_WEIGHT + RANK_WEIGHT
        prob_model   = (SERVE_WEIGHT * prob_serve + RANK_WEIGHT * prob_rank) / w_total
        prob_baseline = prob_rank  # solo ranking, como referencia

        records.append({
            "surface":        surf,
            "prob_model":     round(prob_model,    4),
            "prob_baseline":  round(prob_baseline, 4),
            "prob_serve":     round(prob_serve,    4),
        })

    df = pd.DataFrame(records)
    _print_metrics(df, prefix.upper())
    _simulate_roi(df)
    return df


# ── métricas ──────────────────────────────────────────────────────────────────

def _print_metrics(df: pd.DataFrame, label: str):
    n       = len(df)
    # p1 siempre es el ganador real → correcto si prob > 0.5
    correct       = (df["prob_model"]    > 0.5).sum()
    correct_base  = (df["prob_baseline"] > 0.5).sum()

    logger.info(f"\n  Accuracy global: {correct/n:.1%} ({correct}/{n})")
    logger.info(f"  Baseline (solo ranking): {correct_base/n:.1%} ({correct_base}/{n})")
    logger.info(f"  Ganancia vs baseline:  {(correct-correct_base)/n:+.1%}")

    # Por superficie
    logger.info(f"\n  Accuracy por superficie:")
    for surf in SURFACES:
        s = df[df["surface"] == surf]
        if s.empty:
            continue
        acc = (s["prob_model"] > 0.5).mean()
        logger.info(f"    {surf:6s}: {acc:.1%}  ({len(s)} partidos)")

    # Calibración correcta: crear pares (prob_predicha, outcome) para AMBOS jugadores
    # p1 = ganador real (outcome=1), p2 = perdedor (outcome=0)
    cal_rows = (
        [{"prob": p, "won": 1} for p in df["prob_model"]] +
        [{"prob": 1 - p, "won": 0} for p in df["prob_model"]]
    )
    cal = pd.DataFrame(cal_rows)

    logger.info(f"\n  Calibración (predicho vs real):")
    buckets = [(0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 1.01)]
    for lo, hi in buckets:
        b = cal[(cal["prob"] >= lo) & (cal["prob"] < hi)]
        if b.empty:
            continue
        predicted   = b["prob"].mean()
        actual      = b["won"].mean()
        diff        = actual - predicted
        flag        = "✅" if abs(diff) < 0.03 else ("⬆️" if diff > 0 else "⬇️")
        logger.info(
            f"    {lo:.0%}–{hi:.0%}: pred {predicted:.1%} → real {actual:.1%} "
            f"({diff:+.1%}) {flag}  [{len(b)//2} partidos]"
        )

    # Brier score (p1 siempre ganó → outcome=1)
    brier_model = ((df["prob_model"]    - 1) ** 2).mean()
    brier_base  = ((df["prob_baseline"] - 1) ** 2).mean()
    logger.info(f"\n  Brier score — modelo: {brier_model:.4f} | baseline: {brier_base:.4f}")
    logger.info(
        f"  {'✅ Modelo mejora baseline' if brier_model < brier_base else '⚠️  Baseline igual o mejor'}"
    )


def _simulate_roi(df: pd.DataFrame):
    """
    ROI simulado asumiendo que el mercado usa las odds del modelo de ranking
    con un margen del 5%. Apostamos Kelly (25%) cuando EV > MIN_EV_THRESHOLD.
    """
    from config import KELLY_FRACTION, MAX_STAKE_EUR, BANKROLL

    bankroll = float(BANKROLL)
    total_staked = 0.0
    total_profit = 0.0
    bets = 0

    for _, row in df.iterrows():
        prob_model = row["prob_model"]
        prob_base  = row["prob_baseline"]
        if prob_base <= 0:
            continue

        # Odds "de mercado" simuladas: inverso de prob baseline con 5% margen
        market_odds = (1 / prob_base) * 0.95

        ev = prob_model * market_odds - 1
        if ev < MIN_EV_THRESHOLD or ev > MAX_EV:
            continue

        b     = market_odds - 1
        kelly = (b * prob_model - (1 - prob_model)) / b if b > 0 else 0
        stake = min(max(kelly * KELLY_FRACTION * bankroll, 0), MAX_STAKE_EUR)
        if stake <= 0:
            continue

        # p1 siempre ganó → apuesta ganada
        profit = round(stake * (market_odds - 1), 2)
        total_staked += stake
        total_profit += profit
        bets += 1

    roi = (total_profit / total_staked * 100) if total_staked > 0 else 0
    logger.info(f"\n  Simulación ROI (vs odds ranking + 5% margen):")
    logger.info(f"    Apuestas con valor: {bets}")
    logger.info(f"    Staked total:  €{total_staked:.2f}")
    logger.info(f"    Profit total:  €{total_profit:+.2f}")
    logger.info(f"    ROI:           {roi:+.1f}%")
    logger.info(f"    ⚠️  Nota: ROI inflado (siempre apostamos al ganador real)")


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    backtest_tour(BASE_URL_ATP, "atp", DEFAULT_SERVE_ATP)
    backtest_tour(BASE_URL_WTA, "wta", DEFAULT_SERVE_WTA)
