import pandas as pd
import requests
import os
from io import StringIO
from loguru import logger

# Repositorios JeffSackmann
BASE_URL_ATP = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master"
BASE_URL_WTA = "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master"
BASE_URL = BASE_URL_ATP  # compatibilidad

YEARS = [2022, 2023, 2024]  # 2025+ aún no disponible en JeffSackmann

SURFACE_MAP = {
    "Hard": "hard",
    "Clay": "clay",
    "Grass": "grass",
    "Carpet": "carpet",
}


def _download_matches(base_url: str, prefix: str, years: list) -> pd.DataFrame:
    dfs = []
    for year in years:
        url = f"{base_url}/{prefix}_matches_{year}.csv"
        logger.info(f"Descargando {prefix.upper()} {year}...")
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
    logger.info(f"Total: {len(full)} partidos {prefix.upper()} ({years[0]}-{years[-1]})")
    return full


def download_atp_matches(years: list = YEARS) -> pd.DataFrame:
    return _download_matches(BASE_URL_ATP, "atp", years)


def download_wta_matches(years: list = YEARS) -> pd.DataFrame:
    return _download_matches(BASE_URL_WTA, "wta", years)


def get_current_rankings(tour: str = "atp") -> dict:
    """
    Descarga rankings actuales de JeffSackmann usando los archivos
    *_rankings_current.csv + *_players.csv.
    Devuelve dict: {player_name: {"rank": int, "points": int}}
    """
    base = BASE_URL_ATP if tour == "atp" else BASE_URL_WTA
    tour_upper = tour.upper()

    try:
        rankings_resp = requests.get(f"{base}/{tour}_rankings_current.csv", timeout=10)
        players_resp  = requests.get(f"{base}/{tour}_players.csv",          timeout=10)

        if rankings_resp.status_code != 200 or players_resp.status_code != 200:
            logger.warning(f"No se pudieron descargar rankings {tour_upper}")
            return {}

        rankings_df = pd.read_csv(StringIO(rankings_resp.text))
        players_df  = pd.read_csv(StringIO(players_resp.text), low_memory=False)

        # Quedarse con la semana más reciente
        latest = rankings_df["ranking_date"].max()
        rankings_df = rankings_df[rankings_df["ranking_date"] == latest]

        # Construir nombre completo en el mismo formato que los partidos
        players_df["full_name"] = (
            players_df["name_first"].str.strip()
            + " "
            + players_df["name_last"].str.strip()
        )

        merged = rankings_df.merge(
            players_df[["player_id", "full_name"]],
            left_on="player", right_on="player_id",
            how="left",
        )

        result = {}
        for _, row in merged.iterrows():
            name = row.get("full_name")
            if pd.isna(name) or not name.strip():
                continue
            result[name] = {
                "rank":   int(row["rank"]),
                "points": int(row["points"]) if pd.notna(row["points"]) else 0,
            }

        logger.info(f"Rankings {tour_upper} descargados: {len(result)} jugadores (semana {latest})")
        return result

    except Exception as e:
        logger.error(f"Error descargando rankings {tour_upper}: {e}")
        return {}


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