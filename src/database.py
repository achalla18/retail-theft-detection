"""
database.py — SQLite-backed alert persistence.

Stores every triggered alert to a local database so history survives
restarts and can be queried from the web dashboard.
"""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

# DB lives at <project_root>/data/surveillance.db
_DB_PATH = Path(__file__).parent.parent / "data" / "surveillance.db"


def init_db():
    """Create the database file and tables if they don't exist."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     REAL    NOT NULL,
                person_id     INTEGER NOT NULL,
                zone          TEXT    NOT NULL,
                dwell_seconds REAL    NOT NULL,
                threshold     REAL    NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ts ON alerts(timestamp)"
        )
        conn.commit()


@contextmanager
def _conn():
    conn = sqlite3.connect(str(_DB_PATH), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def insert_alert(alert: dict):
    """Persist a new alert record."""
    with _conn() as conn:
        conn.execute(
            "INSERT INTO alerts (timestamp, person_id, zone, dwell_seconds, threshold) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                alert["timestamp"],
                alert["person_id"],
                alert["zone"],
                alert["dwell_seconds"],
                alert["threshold"],
            ),
        )
        conn.commit()


def get_recent_alerts(limit: int = 100, since: float = None) -> list[dict]:
    """Return recent alerts newest-first, optionally filtered by timestamp."""
    with _conn() as conn:
        if since is not None:
            rows = conn.execute(
                "SELECT * FROM alerts WHERE timestamp > ? ORDER BY timestamp DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM alerts ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    """Return summary counts for the dashboard."""
    now = time.time()
    # Midnight UTC today
    today_start = now - (now % 86400)
    hour_start = now - 3600

    with _conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        today = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE timestamp > ?", (today_start,)
        ).fetchone()[0]
        last_hour = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE timestamp > ?", (hour_start,)
        ).fetchone()[0]
        top_zones = conn.execute(
            "SELECT zone, COUNT(*) AS cnt FROM alerts "
            "WHERE timestamp > ? GROUP BY zone ORDER BY cnt DESC",
            (today_start,),
        ).fetchall()

    return {
        "total": total,
        "today": today,
        "last_hour": last_hour,
        "top_zones": [{"zone": r["zone"], "count": r["cnt"]} for r in top_zones],
    }
