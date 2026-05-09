"""
Backtest del modelo de tenis usando datos históricos JeffSackmann.

Metodología: split por año (train / test)
  - Para cada partido del año test, calcula la probabilidad del ganador usando
    estadísticas construidas únicamente con datos de entrenamiento.
  - Usa los 4 factores del modelo real: servicio · forma · ranking · H2H.
  - winner_rank_points del propio partido como proxy de ranking contemporáneo
    (sin lookahead: ese dato ya era público cuando se jugó el partido).

Métricas:
  - Accuracy: % de partidos donde el favorito del modelo ganó
  - Brier score: calibración de probabilidades (0.25 = aleatorio)
  - Calibración por tramos de confianza
  - Comparación vs baseline (solo ranking Bradley-Terry)

Uso:
  python backtest.py                           # ATP+WTA, test 2024, train 2018-2023
  python backtest.py --test-year 2023          # test 2023, train 2022
  python backtest.py --surface Clay            # solo tierra batida
  python backtest.py --tour ATP                # solo ATP
  python backtest.py --train 2021 2022 2023    # años de entrenamiento a medida
"""
import argparse
import pandas as pd
from loguru import logger

from src.data.database import init_db
from src.data.tennis_data import (
    download_atp_matches, download_wta_matches,
    calculate_player_stats, calculate_recent_form, calculate_h2h,
)
from src.models.tennis_engine import (
    prob_win_match, prob_win_by_ranking, _weighted_blend,
    DEFAULT_SERVE_WIN_PCT_ATP, DEFAULT_SERVE_WIN_PCT_WTA, DEFAULT_RANK_POINTS,
)

SURFACES = ["Clay", "Hard", "Grass"]


# ── Predicción ────────────────────────────────────────────────────────────────

def _predict(
    winner: str, loser: str, surface: str, tour: str,
    serve_stats: dict, form: dict, h2h: dict,
    rk_pts_w, rk_pts_l,
) -> tuple:
    """
    Devuelve (prob_modelo, prob_baseline) para que el ganador real gane.
    prob_baseline = solo Bradley-Terry con ranking.
    """
    default_serve = DEFAULT_SERVE_WIN_PCT_WTA if tour == "WTA" else DEFAULT_SERVE_WIN_PCT_ATP

    serve_w = serve_stats.get(winner, default_serve)
    serve_l = serve_stats.get(loser,  default_serve)

    pts_w = float(rk_pts_w) if rk_pts_w and not pd.isna(rk_pts_w) else DEFAULT_RANK_POINTS
    pts_l = float(rk_pts_l) if rk_pts_l and not pd.isna(rk_pts_l) else DEFAULT_RANK_POINTS

    form_w = form.get(winner, 0.5)
    form_l = form.get(loser,  0.5)
    total  = form_w + form_l
    prob_form = form_w / total if total > 0 else 0.5

    prob_serve = prob_win_match(serve_w, serve_l)
    prob_rank  = prob_win_by_ranking(pts_w, pts_l)
    prob_h2h   = h2h.get(winner, {}).get(loser, 0.5)

    prob_model = round(
        _weighted_blend(
            prob_serve, prob_form, prob_rank, prob_h2h,
            serve_is_default = (serve_w == default_serve and serve_l == default_serve),
            form_is_default  = (form_w == 0.5 and form_l == 0.5),
            h2h_is_default   = (prob_h2h == 0.5),
        ),
        4,
    )
    return prob_model, round(prob_rank, 4)


# ── Backtest principal ────────────────────────────────────────────────────────

def run_backtest(
    train_df: pd.DataFrame,
    test_df:  pd.DataFrame,
    tour:     str,
    surface_filter: str = None,
) -> list:
    """
    Evalúa el modelo en test_df entrenado con train_df.
    Devuelve lista de dicts con {prob, baseline, correct, surface}.
    """
    surfaces = [surface_filter] if surface_filter else SURFACES

    # Construir estadísticas de entrenamiento por superficie
    serve_by_surf = {}
    form_by_surf  = {}
    for s in surfaces:
        stats = calculate_player_stats(train_df, surface=s)
        serve_by_surf[s] = (
            dict(zip(stats["player"], stats["serve_win_pct"]))
            if not stats.empty else {}
        )
        form_by_surf[s] = calculate_recent_form(train_df, surface=s)

    h2h = calculate_h2h(train_df)

    # Filtrar test
    needed = ["winner_name", "loser_name", "surface"]
    sub = test_df.dropna(subset=needed)
    if surface_filter:
        sub = sub[sub["surface"].str.lower() == surface_filter.lower()]

    results = []
    for _, row in sub.iterrows():
        surf = row["surface"].strip().title()
        if surf not in serve_by_surf:
            continue

        winner = row["winner_name"]
        loser  = row["loser_name"]
        rk_w   = row.get("winner_rank_points")
        rk_l   = row.get("loser_rank_points")

        prob, baseline = _predict(
            winner, loser, surf, tour,
            serve_by_surf[surf], form_by_surf[surf], h2h,
            rk_w, rk_l,
        )
        results.append({
            "prob":     prob,
            "baseline": baseline,
            "correct":  int(prob > 0.5),
            "correct_base": int(baseline > 0.5),
            "surface":  surf,
        })

    return results


# ── Informe ───────────────────────────────────────────────────────────────────

def print_report(results: list, label: str):
    if not results:
        logger.warning(f"{label}: sin datos para evaluar.")
        return

    df = pd.DataFrame(results)
    n  = len(df)

    acc      = df["correct"].mean()
    acc_base = df["correct_base"].mean()
    brier    = ((df["prob"] - 1.0) ** 2).mean()
    brier_b  = ((df["baseline"] - 1.0) ** 2).mean()

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  Partidos evaluados : {n:,}")
    print(f"  Accuracy modelo    : {acc:.1%}  (baseline ranking: {acc_base:.1%})")
    print(f"  Ganancia vs base   : {acc - acc_base:+.1%}")
    print(f"  Brier score        : {brier:.4f}  (baseline: {brier_b:.4f}, aleatorio: 0.2500)")

    # Por superficie
    surfs = df["surface"].unique()
    if len(surfs) > 1:
        print(f"\n  Por superficie:")
        print(f"  {'Superficie':10s} {'Accuracy':>9s} {'Baseline':>9s} {'N':>6s}")
        print(f"  {'-'*38}")
        for surf in sorted(surfs):
            s = df[df["surface"] == surf]
            print(
                f"  {surf:10s} {s['correct'].mean():>9.1%} "
                f"{s['correct_base'].mean():>9.1%} {len(s):>6,}"
            )

    # Calibración: usamos ambas perspectivas (ganador y perdedor)
    cal_rows = (
        [{"prob": p, "won": 1} for p in df["prob"]] +
        [{"prob": 1 - p, "won": 0} for p in df["prob"]]
    )
    cal = pd.DataFrame(cal_rows)

    buckets = [
        (0.40, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 1.01)
    ]
    print(f"\n  Calibración (predicho vs real):")
    print(f"  {'Rango':12s} {'Predicha':>9s} {'Real':>7s} {'Diff':>7s}  N")
    print(f"  {'-'*48}")
    for lo, hi in buckets:
        b = cal[(cal["prob"] >= lo) & (cal["prob"] < hi)]
        if len(b) < 20:
            continue
        pred   = b["prob"].mean()
        actual = b["won"].mean()
        diff   = actual - pred
        flag   = "✓" if abs(diff) < 0.04 else ("▲" if diff > 0 else "▼")
        print(
            f"  {lo:.0%}–{hi if hi < 1 else 1:.0%}       "
            f"{pred:>9.1%} {actual:>7.1%} {diff:>+7.1%}  {len(b)//2}  {flag}"
        )

    print()


# ── Carga de datos ────────────────────────────────────────────────────────────

def _split(df: pd.DataFrame, train_years: list, test_year: int):
    """Divide un DataFrame por año de torney_date (YYYYMMDD)."""
    df = df.copy()
    df["_year"] = (df["tourney_date"].astype(str).str[:4].astype(int, errors="ignore"))
    train = df[df["_year"].isin(train_years)].drop(columns=["_year"])
    test  = df[df["_year"] == test_year].drop(columns=["_year"])
    return train, test


# ── CLI ───────────────────────────────────────────────────────────────────────

def run(
    test_year:     int,
    train_years:   list,
    surface:       str = None,
    tour_filter:   str = None,
):
    init_db()

    tours = [tour_filter] if tour_filter else ["ATP", "WTA"]
    all_years = sorted(set(train_years) | {test_year})

    for tour in tours:
        logger.info(f"Cargando datos {tour}...")
        df = download_atp_matches(years=all_years) if tour == "ATP" else download_wta_matches(years=all_years)
        if df.empty:
            logger.warning(f"Sin datos para {tour}")
            continue

        train_df, test_df = _split(df, train_years, test_year)

        if train_df.empty:
            logger.warning(f"Sin datos de entrenamiento {tour} para {train_years}")
            continue
        if test_df.empty:
            logger.warning(f"Sin datos de test {tour} para {test_year}")
            continue

        logger.info(
            f"{tour}: {len(train_df):,} partidos train ({train_years}) / "
            f"{len(test_df):,} partidos test ({test_year})"
        )

        results = run_backtest(train_df, test_df, tour=tour, surface_filter=surface)

        surf_label = f" | {surface}" if surface else ""
        label = (
            f"{tour} — test {test_year} | train {train_years[0]}–{train_years[-1]}"
            f"{surf_label}"
        )
        print_report(results, label)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backtest del modelo de tenis (4 factores) sobre datos históricos"
    )
    parser.add_argument(
        "--test-year", type=int, default=2024,
        help="Año de evaluación (default: 2024)",
    )
    parser.add_argument(
        "--train", nargs="+", type=int, default=[2018, 2019, 2020, 2021, 2022, 2023],
        metavar="YEAR",
        help="Años de entrenamiento (default: 2018-2023)",
    )
    parser.add_argument(
        "--surface", choices=["Clay", "Hard", "Grass"],
        help="Filtrar por superficie",
    )
    parser.add_argument(
        "--tour", choices=["ATP", "WTA"],
        help="Filtrar por circuito (default: ambos)",
    )
    args = parser.parse_args()

    run(
        test_year=args.test_year,
        train_years=args.train,
        surface=args.surface,
        tour_filter=args.tour,
    )
