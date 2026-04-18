import sqlite3
from pathlib import Path
from loguru import logger

DB_PATH = Path("data/tipster.db")


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
                result       TEXT,
                profit_loss  REAL,
                UNIQUE(match_id, bookmaker, outcome)
            );
        """)
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


def save_pick(match_id: int, pick: dict) -> bool:
    """Inserta un pick. Devuelve True si es nuevo, False si ya existía."""
    with get_connection() as conn:
        try:
            conn.execute("""
                INSERT INTO picks
                    (match_id, bookmaker, outcome, odds,
                     our_prob, market_prob, ev, stake_eur)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                match_id,
                pick["bookmaker"], pick["outcome"], pick["odd"],
                pick["our_prob"],  pick["market_prob"],
                pick["ev"],        pick["stake_eur"],
            ))
            return True
        except sqlite3.IntegrityError:
            return False


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


def get_pending_picks() -> list:
    """Picks sin resultado para introducir manualmente."""
    with get_connection() as conn:
        return conn.execute("""
            SELECT p.id, m.player1, m.player2, m.tournament, m.surface, m.tour,
                   p.outcome, p.bookmaker, p.odds, p.our_prob, p.ev, p.stake_eur,
                   p.created_at
            FROM picks p
            JOIN matches m ON p.match_id = m.id
            WHERE p.result IS NULL
            ORDER BY p.created_at DESC
        """).fetchall()


def get_roi_summary() -> list:
    """ROI acumulado agrupado por tour y superficie."""
    with get_connection() as conn:
        return conn.execute("""
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
            WHERE p.result IS NOT NULL
            GROUP BY m.tour, m.surface
            ORDER BY roi_pct DESC
        """).fetchall()
