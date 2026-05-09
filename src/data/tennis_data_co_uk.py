"""
Cliente para tennis-data.co.uk — resultados ATP/WTA con cuotas de bookmakers.

Cobertura: 2000–presente. Actualización: días/semanas tras cada torneo.
Columnas útiles: Winner, Loser, WRank, LRank, WPts, LPts, Surface, Date,
                 Tournament, Round, PSW, PSL, B365W, B365L, MaxW, MaxL.
Limitación: NO incluye estadísticas de servicio (w_svpt, w_1stIn, etc.).
            Solo sirve para H2H y forma reciente.

URL structure:
  ATP:  http://www.tennis-data.co.uk/{year}/{tournament}.xls
  WTA:  http://www.tennis-data.co.uk/{year}w/{tournament}.xls
  Index: http://www.tennis-data.co.uk/alldata.php
"""

import hashlib
import re
import time
from datetime import datetime
from io import BytesIO

import pandas as pd
import requests
from loguru import logger

BASE_URL = "http://www.tennis-data.co.uk"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         f"{BASE_URL}/alldata.php",
}

# tennis-data.co.uk → match_history schema
_COL_MAP = {
    "Winner":     "winner_name",
    "Loser":      "loser_name",
    "WRank":      "winner_rank",
    "LRank":      "loser_rank",
    "WPts":       "winner_rank_points",
    "LPts":       "loser_rank_points",
    "Surface":    "surface",
    "Tournament": "tourney_name",
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(url: str, retries: int = 4) -> bytes | None:
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=_HEADERS, timeout=20)
            if r.status_code == 200:
                return r.content
            logger.warning(f"TDK HTTP {r.status_code}: {url} (intento {attempt+1}/{retries})")
        except Exception as e:
            logger.warning(f"TDK error: {e} (intento {attempt+1}/{retries})")
        if attempt < retries - 1:
            time.sleep(2 ** (attempt + 1))
    return None


def _fetch_file_links(year: int) -> list[str]:
    """
    Parsea alldata.php y extrae URLs de archivos .xls/.xlsx del año indicado.
    Detecta ATP (/{year}/) y WTA (/{year}w/) por el path.
    """
    html = _get(f"{BASE_URL}/alldata.php")
    if html is None:
        logger.error("TDK: no se pudo acceder a alldata.php")
        return []

    text = html.decode("utf-8", errors="replace")

    # Encuentra todos los href que contengan el año y terminen en .xls o .xlsx
    pattern = rf'href=["\']([^"\']*{year}[^"\']*\.xlsx?)["\']'
    found = re.findall(pattern, text, re.IGNORECASE)

    urls = []
    for href in found:
        url = href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('/')}"
        urls.append(url)

    # Deduplica preservando orden
    seen, unique = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)

    logger.info(f"TDK {year}: {len(unique)} archivos en alldata.php")
    return unique


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_date(d) -> int | None:
    """Convierte fecha TDK (DD/MM/YYYY o similar) → YYYYMMDD int."""
    if pd.isna(d):
        return None
    s = str(d).strip()
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return int(datetime.strptime(s[:10], fmt).strftime("%Y%m%d"))
        except ValueError:
            continue
    return None


def _make_score(row: pd.Series) -> str | None:
    """Reconstruye score desde columnas W1/L1 … W5/L5."""
    parts = []
    for i in range(1, 6):
        w = row.get(f"W{i}")
        l = row.get(f"L{i}")
        if pd.isna(w) or pd.isna(l):
            break
        parts.append(f"{int(w)}-{int(l)}")
    return " ".join(parts) or None


def _stable_match_num(winner: str, loser: str, round_: str, date: str) -> int:
    """
    Hash estable de (ganador, perdedor, ronda, fecha) → entero para match_num.
    Suficientemente único dentro de un torneo (28 bits, ~268M valores).
    """
    key = f"{winner}|{loser}|{round_}|{date}".encode()
    return int(hashlib.sha256(key).hexdigest()[:7], 16)


def _tourney_id(tour: str, year: int, name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return f"tdk_{tour.lower()}_{year}_{slug}"


def _read_excel(content: bytes) -> pd.DataFrame | None:
    """Intenta leer bytes de Excel probando openpyxl (.xlsx) luego xlrd (.xls)."""
    for engine in ("openpyxl", "xlrd"):
        try:
            return pd.read_excel(BytesIO(content), engine=engine)
        except Exception:
            continue
    return None


def _normalize(df_raw: pd.DataFrame, tour: str, year: int) -> pd.DataFrame:
    df = df_raw.copy()

    # Renombrar columnas conocidas
    df = df.rename(columns={c: v for c, v in _COL_MAP.items() if c in df.columns})

    # Fecha → YYYYMMDD
    df["tourney_date"] = df["Date"].apply(_parse_date) if "Date" in df.columns else None

    # Filtrar filas sin partido real
    df = df.dropna(subset=["winner_name", "loser_name"])
    df = df[df["winner_name"].astype(str).str.strip() != ""]
    df = df[df["loser_name"].astype(str).str.strip() != ""]

    # Identificadores sintéticos
    round_col = "Round" if "Round" in df.columns else None
    df["tourney_id"] = df.apply(
        lambda r: _tourney_id(tour, year, str(r.get("tourney_name", "unknown"))),
        axis=1,
    )
    df["match_num"] = df.apply(
        lambda r: _stable_match_num(
            str(r["winner_name"]),
            str(r["loser_name"]),
            str(r[round_col]) if round_col else "",
            str(r.get("tourney_date", "")),
        ),
        axis=1,
    )

    # Score
    df["score"] = df.apply(_make_score, axis=1)

    # Proyectar solo columnas de match_history
    keep = [
        "tourney_id", "match_num", "tourney_date", "tourney_name", "surface",
        "winner_name", "loser_name",
        "winner_rank", "loser_rank", "winner_rank_points", "loser_rank_points",
        "score",
    ]
    return df[[c for c in keep if c in df.columns]]


# ---------------------------------------------------------------------------
# Pública
# ---------------------------------------------------------------------------

def fetch_year(year: int, tours: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """
    Descarga y normaliza todos los archivos de TDK para el año indicado.
    Devuelve {"ATP": df, "WTA": df} (puede ser vacío si falla la red).
    """
    if tours is None:
        tours = ["ATP", "WTA"]

    links = _fetch_file_links(year)
    if not links:
        return {t: pd.DataFrame() for t in tours}

    atp_dfs, wta_dfs = [], []

    for url in links:
        # Detectar tour por el path (/{year}w/ = WTA)
        tour = "WTA" if f"/{year}w/" in url else "ATP"
        if tour not in tours:
            continue

        logger.info(f"  Descargando TDK {tour} {year}: {url.split('/')[-1]}")
        content = _get(url)
        if content is None:
            continue

        df_raw = _read_excel(content)
        if df_raw is None or df_raw.empty:
            logger.warning(f"  No se pudo parsear: {url}")
            continue

        df = _normalize(df_raw, tour, year)
        if df.empty:
            continue

        logger.info(f"    → {len(df)} partidos")
        (atp_dfs if tour == "ATP" else wta_dfs).append(df)

    result = {}
    if "ATP" in tours:
        result["ATP"] = pd.concat(atp_dfs, ignore_index=True) if atp_dfs else pd.DataFrame()
    if "WTA" in tours:
        result["WTA"] = pd.concat(wta_dfs, ignore_index=True) if wta_dfs else pd.DataFrame()

    for tour, df in result.items():
        if not df.empty:
            logger.info(f"TDK {year} {tour}: {len(df)} partidos en total")

    return result
