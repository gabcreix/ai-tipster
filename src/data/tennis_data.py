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


def calculate_recent_form(df: pd.DataFrame, surface: str = None, n_matches: int = 20) -> dict:
    """
    Devuelve {player: weighted_win_rate} con decay exponencial.
    Si surface es especificado y el jugador tiene >= 5 partidos en esa superficie,
    usa solo esos partidos; si no, usa todos.
    """
    if df.empty:
        return {}
    req_cols = ["winner_name", "loser_name", "surface", "tourney_date"]
    if not all(c in df.columns for c in req_cols):
        return {}

    df = df.copy()
    df["tourney_date"] = pd.to_datetime(
        df["tourney_date"].astype(str), format="%Y%m%d", errors="coerce"
    )
    df = df.dropna(subset=["tourney_date"]).sort_values("tourney_date")

    surface_key = SURFACE_MAP.get(surface, surface).lower() if surface else None
    players = set(df["winner_name"]) | set(df["loser_name"])
    result = {}

    for player in players:
        player_df = df[(df["winner_name"] == player) | (df["loser_name"] == player)]
        if surface_key:
            surf_df = player_df[player_df["surface"].str.lower() == surface_key]
            use_df = surf_df if len(surf_df) >= 5 else player_df
        else:
            use_df = player_df

        use_df = use_df.tail(n_matches)
        if use_df.empty:
            continue

        n = len(use_df)
        weights = [0.9 ** (n - 1 - i) for i in range(n)]
        wins    = [1.0 if row["winner_name"] == player else 0.0
                   for _, row in use_df.iterrows()]
        result[player] = round(
            sum(w * v for w, v in zip(weights, wins)) / sum(weights), 4
        )

    logger.info(f"Forma reciente calculada: {len(result)} jugadores (superficie: {surface or 'todas'})")
    return result


def calculate_h2h(df: pd.DataFrame) -> dict:
    """Devuelve {p1: {p2: win_rate_p1}} para pares con >= 2 enfrentamientos."""
    if df.empty:
        return {}
    if "winner_name" not in df.columns or "loser_name" not in df.columns:
        return {}

    from collections import defaultdict
    wins: dict = defaultdict(int)

    for _, row in df.iterrows():
        wins[(row["winner_name"], row["loser_name"])] += 1

    result: dict = {}
    seen: set = set()

    for (p1, p2) in list(wins.keys()):
        pair = frozenset([p1, p2])
        if pair in seen:
            continue
        seen.add(pair)

        p1_wins = wins.get((p1, p2), 0)
        p2_wins = wins.get((p2, p1), 0)
        total   = p1_wins + p2_wins

        if total < 2:
            continue

        result.setdefault(p1, {})[p2] = round(p1_wins / total, 4)
        result.setdefault(p2, {})[p1] = round(p2_wins / total, 4)

    h2h_pairs = sum(len(v) for v in result.values()) // 2
    logger.info(f"H2H calculado: {h2h_pairs} pares con >= 2 enfrentamientos")
    return result


if __name__ == "__main__":
    df = download_atp_matches()

    if not df.empty:
        logger.info(f"Columnas disponibles: {list(df.columns)}")
        stats = calculate_player_stats(df, surface="Clay")
        logger.info(f"\nTop 10 servidores en tierra batida:")
        print(stats.head(10).to_string(index=False))