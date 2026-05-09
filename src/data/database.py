import sqlite3
from pathlib import Path
from loguru import logger

DB_PATH = Path("data/tipster.db")


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_db():
    """Aplica migraciones de esquema incremental a tablas existentes."""
    with get_connection() as conn:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(picks)").fetchall()}
        if "placed" not in existing:
            conn.execute("ALTER TABLE picks ADD COLUMN placed INTEGER NOT NULL DEFAULT 0")
            logger.info("Migración: columna 'placed' añadida a picks")


def init_db():
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS matches (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                analyzed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                tournament    TEXT NOT NULL,
                surface       TEXT NOT NULL,
                tour          TEXT NOT NULL,
                player1       TEXT NOT NULL,
                player2       TEXT NOT NULL,
                serve_pct_p1  REAL,
                serve_pct_p2  REAL,
                rank_pts_p1   INTEGER,
                rank_pts_p2   INTEGER,
                prob_serve    REAL,
                prob_rank     REAL,
                prob_final_p1 REAL,
                winner        TEXT,
                result_date   DATE
            );

            CREATE TABLE IF NOT EXISTS picks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id     INTEGER NOT NULL REFERENCES matches(id),
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                bookmaker    TEXT NOT NULL,
                outcome      TEXT NOT NULL,
                odds         REAL NOT NULL,
                our_prob     REAL NOT NULL,
                market_prob  REAL NOT NULL,
                ev           REAL NOT NULL,
                stake_eur    REAL NOT NULL,
                placed       INTEGER NOT NULL DEFAULT 0,
                result       TEXT,
                profit_loss  REAL,
                UNIQUE(match_id, bookmaker, outcome)
            );
        """)
    _migrate_db()
    init_match_history()
    init_aliases()
    logger.info(f"DB lista: {DB_PATH.resolve()}")


def save_match(data: dict) -> int:
    """Inserta un partido analizado. Si ya existe hoy, devuelve su ID."""
    with get_connection() as conn:
        existing = conn.execute("""
            SELECT id FROM matches
            WHERE player1 = ? AND player2 = ? AND tournament = ?
              AND DATE(analyzed_at) = DATE('now', 'localtime')
        """, (data["player1"], data["player2"], data["tournament"])).fetchone()

        if existing:
            return existing["id"]

        cursor = conn.execute("""
            INSERT INTO matches
                (tournament, surface, tour, player1, player2,
                 serve_pct_p1, serve_pct_p2, rank_pts_p1, rank_pts_p2,
                 prob_serve, prob_rank, prob_final_p1)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["tournament"], data["surface"], data["tour"],
            data["player1"],    data["player2"],
            data.get("serve_pct_p1"), data.get("serve_pct_p2"),
            data.get("rank_pts_p1"),  data.get("rank_pts_p2"),
            data.get("prob_serve"),   data.get("prob_rank"),
            data.get("prob_final_p1"),
        ))
        return cursor.lastrowid


def save_pick(match_id: int, pick: dict, placed: bool = False) -> bool:
    """Inserta un pick. Devuelve True si es nuevo, False si ya existía."""
    with get_connection() as conn:
        try:
            conn.execute("""
                INSERT INTO picks
                    (match_id, bookmaker, outcome, odds,
                     our_prob, market_prob, ev, stake_eur, placed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                match_id,
                pick["bookmaker"], pick["outcome"], pick["odd"],
                pick["our_prob"],  pick["market_prob"],
                pick["ev"],        pick["stake_eur"],
                int(placed),
            ))
            return True
        except sqlite3.IntegrityError:
            return False


def mark_pick_placed(pick_id: int, placed: bool = True):
    """Marca un pick como apostado (placed=True) o no (placed=False)."""
    with get_connection() as conn:
        conn.execute("UPDATE picks SET placed = ? WHERE id = ?", (int(placed), pick_id))
        logger.info(f"Pick {pick_id} → placed={placed}")


def update_result(pick_id: int, result: str):
    """Marca un pick como 'won' o 'lost' y calcula P&L."""
    if result not in ("won", "lost", "void"):
        raise ValueError(f"result debe ser 'won', 'lost' o 'void', no '{result}'")

    with get_connection() as conn:
        row = conn.execute(
            "SELECT odds, stake_eur FROM picks WHERE id = ?", (pick_id,)
        ).fetchone()
        if not row:
            logger.error(f"Pick {pick_id} no encontrado")
            return

        if result == "won":
            profit_loss = round(row["stake_eur"] * (row["odds"] - 1), 2)
        elif result == "lost":
            profit_loss = round(-row["stake_eur"], 2)
        else:
            profit_loss = 0.0

        conn.execute(
            "UPDATE picks SET result = ?, profit_loss = ? WHERE id = ?",
            (result, profit_loss, pick_id),
        )
        logger.info(f"Pick {pick_id} → {result} | P&L: €{profit_loss:+.2f}")


def mark_match_winner(match_id: int, winner: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE matches SET winner = ?, result_date = DATE('now','localtime') WHERE id = ?",
            (winner, match_id),
        )


def get_pending_picks(placed_only: bool = True) -> list:
    """Picks sin resultado. Por defecto solo picks apostados (placed=1)."""
    placed_filter = "AND p.placed = 1" if placed_only else ""
    with get_connection() as conn:
        return conn.execute(f"""
            SELECT p.id, m.player1, m.player2, m.tournament, m.surface, m.tour,
                   p.outcome, p.bookmaker, p.odds, p.our_prob, p.ev, p.stake_eur,
                   p.placed, p.created_at
            FROM picks p
            JOIN matches m ON p.match_id = m.id
            WHERE p.result IS NULL {placed_filter}
            ORDER BY p.created_at DESC
        """).fetchall()


def init_aliases():
    """Crea las tablas player_aliases y unresolved_names si no existen."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS player_aliases (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                odds_name  TEXT NOT NULL UNIQUE,
                canonical  TEXT,
                tour       TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_aliases_odds
                ON player_aliases (odds_name);

            CREATE TABLE IF NOT EXISTS unresolved_names (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                odds_name  TEXT NOT NULL UNIQUE,
                tour       TEXT,
                seen_count INTEGER DEFAULT 1
            );
        """)


def get_aliases() -> dict:
    """Devuelve {odds_name: canonical} para todos los alias guardados.
    canonical=None indica jugador nuevo sin datos históricos.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT odds_name, canonical FROM player_aliases"
        ).fetchall()
    return {r["odds_name"]: r["canonical"] for r in rows}


def save_alias(odds_name: str, canonical, tour: str = None):
    """Guarda un alias y elimina el nombre de unresolved_names."""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO player_aliases (odds_name, canonical, tour)
            VALUES (?, ?, ?)
            ON CONFLICT(odds_name) DO UPDATE SET
                canonical = excluded.canonical,
                tour      = excluded.tour
        """, [odds_name, canonical, tour])
        conn.execute(
            "DELETE FROM unresolved_names WHERE odds_name = ?", [odds_name]
        )


def log_unresolved(odds_name: str, tour: str = None):
    """Registra un nombre sin resolver o incrementa su contador."""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO unresolved_names (odds_name, tour, seen_count)
            VALUES (?, ?, 1)
            ON CONFLICT(odds_name) DO UPDATE SET
                seen_count = seen_count + 1
        """, [odds_name, tour])


def get_unresolved_names() -> list:
    """Devuelve nombres sin resolver, ordenados por frecuencia."""
    with get_connection() as conn:
        return conn.execute("""
            SELECT odds_name, tour, seen_count, first_seen
            FROM unresolved_names
            ORDER BY seen_count DESC, first_seen
        """).fetchall()


def init_match_history():
    """Crea la tabla match_history si no existe."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS match_history (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                synced_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source              TEXT NOT NULL,
                tour                TEXT NOT NULL DEFAULT 'ATP',
                tourney_id          TEXT NOT NULL,
                match_num           INTEGER NOT NULL,
                tourney_date        INTEGER,
                tourney_name        TEXT,
                surface             TEXT,
                winner_name         TEXT,
                loser_name          TEXT,
                winner_rank         REAL,
                loser_rank          REAL,
                winner_rank_points  REAL,
                loser_rank_points   REAL,
                w_svpt              REAL,
                w_1stIn             REAL,
                w_1stWon            REAL,
                w_2ndWon            REAL,
                l_svpt              REAL,
                l_1stIn             REAL,
                l_1stWon            REAL,
                l_2ndWon            REAL,
                score               TEXT,
                UNIQUE(tourney_id, match_num)
            );
            CREATE INDEX IF NOT EXISTS idx_mh_tour_date
                ON match_history (tour, tourney_date);
        """)


def upsert_matches(df, source: str, tour: str = "ATP", replace: bool = False) -> int:
    """
    Inserta filas de un DataFrame de partidos en match_history.
    replace=False (default): INSERT OR IGNORE — nunca sobreescribe filas existentes.
    replace=True:            INSERT OR REPLACE — actualiza filas ya existentes
                             (usado para años vivos cuyo CSV se actualiza durante el año).
    Devuelve el número de filas insertadas/reemplazadas.
    """
    import pandas as pd

    cols = {
        "tourney_id":         "tourney_id",
        "match_num":          "match_num",
        "tourney_date":       "tourney_date",
        "tourney_name":       "tourney_name",
        "surface":            "surface",
        "winner_name":        "winner_name",
        "loser_name":         "loser_name",
        "winner_rank":        "winner_rank",
        "loser_rank":         "loser_rank",
        "winner_rank_points": "winner_rank_points",
        "loser_rank_points":  "loser_rank_points",
        "w_svpt":             "w_svpt",
        "w_1stIn":            "w_1stIn",
        "w_1stWon":           "w_1stWon",
        "w_2ndWon":           "w_2ndWon",
        "l_svpt":             "l_svpt",
        "l_1stIn":            "l_1stIn",
        "l_1stWon":           "l_1stWon",
        "l_2ndWon":           "l_2ndWon",
        "score":              "score",
    }

    present  = {k: v for k, v in cols.items() if k in df.columns}
    conflict = "REPLACE" if replace else "IGNORE"

    rows = []
    for _, row in df.iterrows():
        values = {db_col: row.get(src_col) for src_col, db_col in present.items()}
        values = {k: (None if (isinstance(v, float) and pd.isna(v)) else v)
                  for k, v in values.items()}
        values["source"] = source
        values["tour"]   = tour
        rows.append(values)

    if not rows:
        return 0

    col_names    = ", ".join(rows[0].keys())
    placeholders = ", ".join(f":{k}" for k in rows[0])
    sql          = f"INSERT OR {conflict} INTO match_history ({col_names}) VALUES ({placeholders})"

    with get_connection() as conn:
        before = conn.total_changes
        try:
            conn.executemany(sql, rows)
        except Exception as e:
            logger.warning(f"upsert_matches: error en batch — {e}")
        return conn.total_changes - before


def load_matches_from_db(years: list = None, tour: str = "ATP"):
    """
    Lee partidos de match_history como DataFrame.
    Filtra por años (tourney_date YYYYMMDD) y tour.
    """
    import pandas as pd

    with get_connection() as conn:
        if years:
            placeholders = ",".join("?" for _ in years)
            date_filters = " OR ".join(
                f"(tourney_date >= {y}0101 AND tourney_date <= {y}1231)"
                for y in years
            )
            rows = conn.execute(
                f"SELECT * FROM match_history WHERE tour = ? AND ({date_filters})",
                [tour],
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM match_history WHERE tour = ?", [tour]
            ).fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame([dict(r) for r in rows])
    logger.info(f"match_history: {len(df)} partidos {tour} leídos de DB")
    return df


def get_match_history_counts() -> list:
    """Resumen de cuántos partidos hay por source/tour/año."""
    with get_connection() as conn:
        return conn.execute("""
            SELECT
                source,
                tour,
                CAST(tourney_date / 10000 AS INTEGER) AS year,
                COUNT(*) AS matches
            FROM match_history
            WHERE tourney_date IS NOT NULL
            GROUP BY source, tour, year
            ORDER BY year DESC, tour
        """).fetchall()


def get_roi_summary(placed_only: bool = True) -> list:
    """ROI acumulado agrupado por tour y superficie. Por defecto solo picks apostados."""
    placed_filter = "AND p.placed = 1" if placed_only else ""
    with get_connection() as conn:
        return conn.execute(f"""
            SELECT
                m.tour,
                m.surface,
                COUNT(*)                                               AS picks,
                SUM(CASE WHEN p.result = 'won'  THEN 1 ELSE 0 END)   AS wins,
                SUM(CASE WHEN p.result = 'lost' THEN 1 ELSE 0 END)   AS losses,
                ROUND(SUM(p.stake_eur), 2)                            AS staked,
                ROUND(SUM(COALESCE(p.profit_loss, 0)), 2)             AS profit,
                ROUND(
                    SUM(COALESCE(p.profit_loss, 0))
                    / NULLIF(SUM(p.stake_eur), 0) * 100, 1
                )                                                      AS roi_pct
            FROM picks p
            JOIN matches m ON p.match_id = m.id
            WHERE p.result IS NOT NULL {placed_filter}
            GROUP BY m.tour, m.surface
            ORDER BY roi_pct DESC
        """).fetchall()


def has_source_data(source: str) -> bool:
    """Devuelve True si match_history tiene alguna fila para el source indicado."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT 1 FROM match_history WHERE source = ? LIMIT 1", [source]
        ).fetchone() is not None
