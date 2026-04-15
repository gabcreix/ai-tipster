import pandas as pd
import requests
import os
from io import StringIO
from loguru import logger

# Repositorio JeffSackmann ATP
BASE_URL = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master"

YEARS = [2022, 2023, 2024, 2025]

SURFACE_MAP = {
    "Hard": "hard",
    "Clay": "clay",
    "Grass": "grass",
    "Carpet": "carpet",
}


def download_atp_matches(years: list = YEARS) -> pd.DataFrame:
    """Descarga histórico de partidos ATP de JeffSackmann."""
    dfs = []

    for year in years:
        url = f"{BASE_URL}/atp_matches_{year}.csv"
        logger.info(f"Descargando ATP {year}...")

        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                df = pd.read_csv(StringIO(response.text))
                dfs.append(df)
                logger.info(f"  {len(df)} partidos descargados")
            else:
                logger.warning(f"  Error {response.status_code} para {year}")
        except Exception as e:
            logger.error(f"  Fallo descargando {year}: {e}")

    if not dfs:
        return pd.DataFrame()

    full = pd.concat(dfs, ignore_index=True)
    logger.info(f"Total: {len(full)} partidos ATP ({years[0]}-{years[-1]})")
    return full


def calculate_player_stats(df: pd.DataFrame, surface: str = None) -> pd.DataFrame:
    """
    Calcula estadísticas de servicio por jugador.
    Devuelve serve_win_pct por jugador y superficie.
    """
    if df.empty:
        return pd.DataFrame()

    cols_needed = [
        "winner_name", "loser_name", "surface",
        "w_svpt", "w_1stIn", "w_1stWon", "w_2ndWon",
        "l_svpt", "l_1stIn", "l_1stWon", "l_2ndWon",
    ]

    df = df[cols_needed].dropna()

    if surface:
        surface_key = SURFACE_MAP.get(surface, surface)
        df = df[df["surface"].str.lower() == surface_key.lower()]

    records = []

    for _, row in df.iterrows():
        # Ganador
        if row["w_svpt"] > 0:
            records.append({
                "player": row["winner_name"],
                "surface": row["surface"],
                "serve_pts": row["w_svpt"],
                "serve_won": row["w_1stWon"] + row["w_2ndWon"],
            })
        # Perdedor
        if row["l_svpt"] > 0:
            records.append({
                "player": row["loser_name"],
                "surface": row["surface"],
                "serve_pts": row["l_svpt"],
                "serve_won": row["l_1stWon"] + row["l_2ndWon"],
            })

    stats = pd.DataFrame(records)
    if stats.empty:
        return pd.DataFrame()

    result = (
        stats.groupby(["player", "surface"])
        .agg(
            total_serve_pts=("serve_pts", "sum"),
            total_serve_won=("serve_won", "sum"),
        )
        .reset_index()
    )

    result["serve_win_pct"] = (
        result["total_serve_won"] / result["total_serve_pts"]
    ).round(4)

    result = result[result["total_serve_pts"] >= 500]

    return result.sort_values("serve_win_pct", ascending=False)


if __name__ == "__main__":
    df = download_atp_matches()

    if not df.empty:
        logger.info(f"Columnas disponibles: {list(df.columns)}")
        stats = calculate_player_stats(df, surface="Clay")
        logger.info(f"\nTop 10 servidores en tierra batida:")
        print(stats.head(10).to_string(index=False))