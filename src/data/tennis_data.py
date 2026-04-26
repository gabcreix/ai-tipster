import time
import pandas as pd
import requests
import os
from io import StringIO
from loguru import logger
from src.data import cache

# Repositorios JeffSackmann
BASE_URL_ATP = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master"
BASE_URL_WTA = "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master"
BASE_URL = BASE_URL_ATP  # compatibilidad

# TML-Database: ATP 2025-2026 (actualizado diariamente)
BASE_URL_TML = "https://raw.githubusercontent.com/Tennismylife/TML-Database/master"

YEARS = [2018, 2019, 2020, 2021, 2022, 2023, 2024]  # 2025+ para ATP se descarga de TML-Database

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


def _download_tml(years: list) -> pd.DataFrame:
    """
    Descarga partidos ATP de TML-Database (2025+).
    Mismo formato que JeffSackmann. Reintentos con backoff exponencial.
    """
    dfs = []
    for year in years:
        url = f"{BASE_URL_TML}/{year}.csv"
        logger.info(f"Descargando TML-Database ATP {year}...")
        df_year = None
        for attempt in range(4):
            try:
                response = requests.get(url, timeout=15)
                if response.status_code == 200:
                    df_year = pd.read_csv(StringIO(response.text))
                    logger.info(f"  {len(df_year)} partidos descargados (TML {year})")
                    break
                else:
                    logger.warning(f"  Error {response.status_code} TML {year} (intento {attempt+1}/4)")
            except Exception as e:
                logger.error(f"  Fallo TML {year} (intento {attempt+1}/4): {e}")
            if attempt < 3:
                wait = 2 ** (attempt + 1)
                logger.info(f"  Reintentando en {wait}s...")
                time.sleep(wait)
        if df_year is not None:
            dfs.append(df_year)
    if not dfs:
        return pd.DataFrame()
    full = pd.concat(dfs, ignore_index=True)
    logger.info(f"TML-Database: {len(full)} partidos ATP ({years[0]}-{years[-1]})")
    return full


def _load_jeffsackmann_from_db(years: list, tour: str) -> tuple:
    """
    Lee partidos JeffSackmann de match_history para los años indicados.
    Devuelve (df_desde_db, años_no_encontrados_en_db).
    """
    try:
        from src.data.database import get_connection
        date_filters = " OR ".join(
            f"(tourney_date >= {y}0101 AND tourney_date <= {y}1231)"
            for y in years
        )
        with get_connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM match_history "
                f"WHERE source = 'jeffsackmann' AND tour = ? AND ({date_filters})",
                [tour],
            ).fetchall()

        if not rows:
            return pd.DataFrame(), list(years)

        df = pd.DataFrame([dict(r) for r in rows])
        years_in_db = {
            y for y in years
            if ((df["tourney_date"] >= y * 10000 + 101) &
                (df["tourney_date"] <= y * 10000 + 1231)).any()
        }
        years_missing = [y for y in years if y not in years_in_db]
        if years_in_db:
            logger.info(
                f"JeffSackmann {tour} {sorted(years_in_db)}: "
                f"{len(df)} partidos leídos de DB"
            )
        return df, years_missing

    except Exception as e:
        logger.warning(f"No se pudo leer JeffSackmann de DB: {e}")
        return pd.DataFrame(), list(years)


def download_atp_matches(years: list = YEARS, include_recent: bool = True) -> pd.DataFrame:
    """
    Descarga partidos ATP.
    - DB local (jeffsackmann) si ya se hizo backfill, si no HTTP JeffSackmann
    - TML-Database para 2025-2026 (si include_recent=True)
    """
    recent_years = [y for y in [2025, 2026] if y not in years]
    key = f"atp_matches_{years[0]}_{years[-1]}{'_recent' if include_recent and recent_years else ''}"
    cached = cache.load(key)
    if cached is not None:
        return cached

    # 1. Años históricos: DB primero, HTTP sólo para los que falten
    db_df, years_to_fetch = _load_jeffsackmann_from_db(years, "ATP")
    if years_to_fetch:
        http_df = _download_matches(BASE_URL_ATP, "atp", years_to_fetch)
        if not db_df.empty and not http_df.empty:
            df = pd.concat([db_df, http_df], ignore_index=True)
        else:
            df = http_df if db_df.empty else db_df
    else:
        df = db_df

    # 2. Datos recientes TML-Database (2025+)
    if include_recent and recent_years:
        tml_df = download_tml_atp(recent_years)
        if not tml_df.empty:
            df = pd.concat([df, tml_df], ignore_index=True) if not df.empty else tml_df
            logger.info(f"ATP total con datos recientes: {len(df)} partidos")

    ttl = 24 if not years_to_fetch else 4
    if not df.empty:
        cache.save(key, df, ttl_hours=ttl)
    return df


def download_tml_atp(years: list = None) -> pd.DataFrame:
    """
    Devuelve partidos ATP de TML-Database para los años indicados.
    Prioridad: DB local (match_history) → caché disco → HTTP.
    """
    if years is None:
        years = [2025, 2026]

    # 1. Intentar leer de la BD local (persistente, sin TTL)
    try:
        from src.data.database import load_matches_from_db
        db_df = load_matches_from_db(years=years, tour="ATP")
        if not db_df.empty:
            return db_df
    except Exception as e:
        logger.warning(f"No se pudo leer match_history: {e}")

    # 2. Caché de disco (2h TTL)
    key = f"tml_atp_{'_'.join(str(y) for y in years)}"
    cached = cache.load(key)
    if cached is not None:
        return cached

    # 3. HTTP
    df = _download_tml(years)
    if not df.empty:
        cache.save(key, df, ttl_hours=2)
    return df


def download_wta_matches(years: list = YEARS) -> pd.DataFrame:
    key = f"wta_matches_{years[0]}_{years[-1]}"
    cached = cache.load(key)
    if cached is not None:
        return cached

    # 1. Años históricos: DB primero, HTTP sólo para los que falten
    db_df, years_to_fetch = _load_jeffsackmann_from_db(years, "WTA")
    if years_to_fetch:
        http_df = _download_matches(BASE_URL_WTA, "wta", years_to_fetch)
        if not db_df.empty and not http_df.empty:
            df = pd.concat([db_df, http_df], ignore_index=True)
        else:
            df = http_df if db_df.empty else db_df
    else:
        df = db_df

    # 2. Enriquecer con partidos WTA recientes de match_history (api-tennis 2025+)
    try:
        from src.data.database import load_matches_from_db
        wta_recent = load_matches_from_db(years=[2025, 2026], tour="WTA")
        if not wta_recent.empty:
            common = [c for c in wta_recent.columns if c in df.columns] if not df.empty else list(wta_recent.columns)
            wta_recent = wta_recent[common]
            df = pd.concat([df, wta_recent], ignore_index=True) if not df.empty else wta_recent
            logger.info(f"WTA: +{len(wta_recent)} partidos recientes (match_history)")
    except Exception:
        pass

    ttl = 24 if not years_to_fetch else 12
    if not df.empty:
        cache.save(key, df, ttl_hours=ttl)
    return df


def get_current_rankings(tour: str = "atp") -> dict:
    """
    Descarga rankings actuales de JeffSackmann usando los archivos
    *_rankings_current.csv + *_players.csv.
    Devuelve dict: {player_name: {"rank": int, "points": int}}
    """
    base = BASE_URL_ATP if tour == "atp" else BASE_URL_WTA
    tour_upper = tour.upper()

    key    = f"rankings_jeffsackmann_{tour}"
    cached = cache.load(key)
    if cached is not None:
        return cached

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
        cache.save(key, result, ttl_hours=24)
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
            if len(surf_df) >= 3:
                use_df = surf_df
            else:
                if len(surf_df) > 0:
                    logger.debug(f"  {player}: solo {len(surf_df)} partido(s) en {surface_key}, usando todas las superficies")
                use_df = player_df
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