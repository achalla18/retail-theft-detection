"""
database.py — SQLite persistence layer for AerialGuard.

Schema
──────
incidents      One row per track lifecycle (open → closed).
track_points   Sampled position + analytics per frame.
alerts         Every triggered alert with operator-managed status.

New in v2:
  incidents  — hover_duration, path_length, behavior_tag,
               closest_approach, zone_crossings, threat_score
  alerts     — status (active/monitoring/escalated/resolved/archived),
               severity
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional

_DB_PATH = Path(__file__).parent.parent / "data" / "surveillance.db"


# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS incidents (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id         INTEGER NOT NULL,
    start_time       REAL    NOT NULL,
    end_time         REAL,
    duration         REAL,
    max_speed        REAL,
    avg_speed        REAL,
    frame_count      INTEGER,
    zones_entered    TEXT,           -- JSON array
    triggered_rules  TEXT,           -- JSON array
    entry_x          INTEGER,
    entry_y          INTEGER,
    exit_x           INTEGER,
    exit_y           INTEGER,
    has_clip         INTEGER DEFAULT 0,
    has_thumb        INTEGER DEFAULT 0,
    summary          TEXT,
    hover_duration   REAL    DEFAULT 0,
    path_length      REAL    DEFAULT 0,
    behavior_tag     TEXT,
    closest_approach REAL    DEFAULT 0,
    zone_crossings   INTEGER DEFAULT 0,
    threat_score     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS track_points (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id    INTEGER REFERENCES incidents(id),
    track_id       INTEGER NOT NULL,
    timestamp      REAL    NOT NULL,
    cx             INTEGER,
    cy             INTEGER,
    bbox_w         INTEGER,
    bbox_h         INTEGER,
    confidence     REAL,
    speed          REAL,
    avg_speed      REAL,
    altitude_proxy REAL,
    in_zones       TEXT,   -- JSON array
    acceleration   REAL    DEFAULT 0,
    heading        REAL    DEFAULT 0,
    path_length    REAL    DEFAULT 0,
    closest_approach REAL  DEFAULT 0,
    behavior_label TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER REFERENCES incidents(id),
    track_id    INTEGER NOT NULL,
    timestamp   REAL    NOT NULL,
    rule        TEXT    NOT NULL,
    zone        TEXT,
    details     TEXT,   -- JSON
    status      TEXT    DEFAULT 'active',
    severity    TEXT    DEFAULT 'medium'
);

CREATE INDEX IF NOT EXISTS idx_inc_track   ON incidents(track_id);
CREATE INDEX IF NOT EXISTS idx_inc_start   ON incidents(start_time);
CREATE INDEX IF NOT EXISTS idx_tp_incident ON track_points(incident_id);
CREATE INDEX IF NOT EXISTS idx_tp_ts       ON track_points(timestamp);
CREATE INDEX IF NOT EXISTS idx_al_ts       ON alerts(timestamp);
CREATE INDEX IF NOT EXISTS idx_al_status   ON alerts(status);
"""

# Columns to add when migrating an existing database
_MIGRATIONS = [
    ("incidents",    "hover_duration",   "REAL    DEFAULT 0"),
    ("incidents",    "path_length",      "REAL    DEFAULT 0"),
    ("incidents",    "behavior_tag",     "TEXT"),
    ("incidents",    "closest_approach", "REAL    DEFAULT 0"),
    ("incidents",    "zone_crossings",   "INTEGER DEFAULT 0"),
    ("incidents",    "threat_score",     "INTEGER DEFAULT 0"),
    ("track_points", "acceleration",     "REAL    DEFAULT 0"),
    ("track_points", "heading",          "REAL    DEFAULT 0"),
    ("track_points", "path_length",      "REAL    DEFAULT 0"),
    ("track_points", "closest_approach", "REAL    DEFAULT 0"),
    ("track_points", "behavior_label",   "TEXT"),
    ("alerts",       "status",           "TEXT    DEFAULT 'active'"),
    ("alerts",       "severity",         "TEXT    DEFAULT 'medium'"),
]


# ── Connection helper ─────────────────────────────────────────────────────────

@contextmanager
def _conn():
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(_DB_PATH), timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


def init_db():
    with _conn() as con:
        con.executescript(_DDL)
        con.commit()
        _run_migrations(con)


def _run_migrations(con: sqlite3.Connection):
    """Safely add new columns to existing databases."""
    for table, col, col_def in _MIGRATIONS:
        try:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
            con.commit()
        except sqlite3.OperationalError:
            pass   # column already exists


# ── Incident CRUD ─────────────────────────────────────────────────────────────

def create_incident(track_id: int, start_time: float) -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO incidents (track_id, start_time) VALUES (?, ?)",
            (track_id, start_time),
        )
        con.commit()
        return cur.lastrowid


def close_incident(
    incident_id: int,
    end_time: float,
    duration: float,
    max_speed: float,
    avg_speed: float,
    frame_count: int,
    zones_entered: str,
    triggered_rules: str,
    entry_point: str,
    exit_point: str,
    has_clip: bool,
    has_thumb: bool,
    summary: str,
    hover_duration: float = 0.0,
    path_length: float = 0.0,
    behavior_tag: str = "",
    closest_approach: float = 0.0,
    zone_crossings: int = 0,
    threat_score: int = 0,
):
    """Finalise an incident after the track disappears."""
    ep = json.loads(entry_point) if entry_point else [None, None]
    xp = json.loads(exit_point)  if exit_point  else [None, None]

    with _conn() as con:
        con.execute(
            """UPDATE incidents SET
                end_time=?, duration=?, max_speed=?, avg_speed=?,
                frame_count=?, zones_entered=?, triggered_rules=?,
                entry_x=?, entry_y=?, exit_x=?, exit_y=?,
                has_clip=?, has_thumb=?, summary=?,
                hover_duration=?, path_length=?, behavior_tag=?,
                closest_approach=?, zone_crossings=?, threat_score=?
               WHERE id=?""",
            (
                end_time, duration, max_speed, avg_speed,
                frame_count, zones_entered, triggered_rules,
                ep[0], ep[1], xp[0], xp[1],
                int(has_clip), int(has_thumb), summary,
                hover_duration, path_length, behavior_tag,
                closest_approach, zone_crossings, threat_score,
                incident_id,
            ),
        )
        con.commit()


def get_incidents(limit: int = 50, offset: int = 0) -> List[dict]:
    """Return closed incidents newest-first."""
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM incidents WHERE end_time IS NOT NULL "
            "ORDER BY start_time DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def get_incident(incident_id: int) -> Optional[dict]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM incidents WHERE id=?", (incident_id,)
        ).fetchone()
    return dict(row) if row else None


# ── Track points ──────────────────────────────────────────────────────────────

def insert_track_point(
    incident_id: int,
    track_id: int,
    timestamp: float,
    cx: int,
    cy: int,
    bbox_w: int,
    bbox_h: int,
    confidence: float,
    speed: float,
    avg_speed: float,
    altitude_proxy: float,
    in_zones: List[str],
    acceleration: float = 0.0,
    heading: float = 0.0,
    path_length: float = 0.0,
    closest_approach: float = 0.0,
    behavior_label: str = "",
):
    with _conn() as con:
        con.execute(
            """INSERT INTO track_points
               (incident_id, track_id, timestamp, cx, cy, bbox_w, bbox_h,
                confidence, speed, avg_speed, altitude_proxy, in_zones,
                acceleration, heading, path_length, closest_approach,
                behavior_label)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                incident_id, track_id, timestamp, cx, cy, bbox_w, bbox_h,
                round(confidence, 3), round(speed, 3), round(avg_speed, 3),
                round(altitude_proxy, 1), json.dumps(in_zones),
                round(acceleration, 3), round(heading, 1),
                round(path_length, 1), round(closest_approach, 1),
                behavior_label,
            ),
        )
        con.commit()


def get_track_points(incident_id: int) -> List[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM track_points WHERE incident_id=? ORDER BY timestamp",
            (incident_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_live_track_points(
    track_ids: List[int], since: float, limit: int = 300
) -> List[dict]:
    if not track_ids:
        return []
    placeholders = ",".join("?" * len(track_ids))
    with _conn() as con:
        rows = con.execute(
            f"SELECT * FROM track_points "
            f"WHERE track_id IN ({placeholders}) AND timestamp > ? "
            f"ORDER BY timestamp DESC LIMIT ?",
            (*track_ids, since, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_object_track_points(
    track_id: int, since: float = 0.0, limit: int = 600
) -> List[dict]:
    """Full history for one track (for Flight Analytics page)."""
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM track_points WHERE track_id=? AND timestamp > ? "
            "ORDER BY timestamp LIMIT ?",
            (track_id, since, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Alerts ────────────────────────────────────────────────────────────────────

def insert_alert(alert: dict, incident_id: int = 0) -> int:
    severity = _infer_severity(alert.get("rule", ""))
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO alerts (incident_id, track_id, timestamp, rule, zone, "
            "details, status, severity) VALUES (?,?,?,?,?,?,?,?)",
            (
                incident_id,
                alert["track_id"],
                alert["timestamp"],
                alert["rule"],
                alert.get("zone"),
                json.dumps(alert.get("details", {})),
                "active",
                severity,
            ),
        )
        con.commit()
        return cur.lastrowid


def get_alerts(limit: int = 100, since: float = None) -> List[dict]:
    with _conn() as con:
        if since is not None:
            rows = con.execute(
                "SELECT * FROM alerts WHERE timestamp > ? ORDER BY timestamp DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM alerts ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_incident_alerts(incident_id: int) -> List[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM alerts WHERE incident_id=? ORDER BY timestamp",
            (incident_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_alert_queue(
    limit: int = 100,
    offset: int = 0,
    status_filter: Optional[str] = None,
) -> List[dict]:
    """Return alerts for the SOC alert queue, newest first."""
    with _conn() as con:
        if status_filter:
            rows = con.execute(
                "SELECT a.*, i.summary as incident_summary FROM alerts a "
                "LEFT JOIN incidents i ON a.incident_id = i.id "
                "WHERE a.status = ? ORDER BY a.timestamp DESC LIMIT ? OFFSET ?",
                (status_filter, limit, offset),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT a.*, i.summary as incident_summary FROM alerts a "
                "LEFT JOIN incidents i ON a.incident_id = i.id "
                "ORDER BY a.timestamp DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
    return [dict(r) for r in rows]


def update_alert_status(alert_id: int, status: str) -> bool:
    """Set alert status. Returns True if a row was updated."""
    valid = {"active", "monitoring", "escalated", "resolved", "archived"}
    if status not in valid:
        return False
    with _conn() as con:
        cur = con.execute(
            "UPDATE alerts SET status=? WHERE id=?", (status, alert_id)
        )
        con.commit()
        return cur.rowcount > 0


# ── Statistics ────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    now       = time.time()
    today     = now - (now % 86400)
    last_hour = now - 3600

    with _conn() as con:
        total_incidents = con.execute(
            "SELECT COUNT(*) FROM incidents"
        ).fetchone()[0]
        today_incidents = con.execute(
            "SELECT COUNT(*) FROM incidents WHERE start_time > ?", (today,)
        ).fetchone()[0]
        total_alerts = con.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        hour_alerts  = con.execute(
            "SELECT COUNT(*) FROM alerts WHERE timestamp > ?", (last_hour,)
        ).fetchone()[0]
        top_zones = con.execute(
            "SELECT zone, COUNT(*) AS cnt FROM alerts "
            "WHERE zone IS NOT NULL AND timestamp > ? "
            "GROUP BY zone ORDER BY cnt DESC LIMIT 5",
            (today,),
        ).fetchall()

    return {
        "total_incidents": total_incidents,
        "today_incidents": today_incidents,
        "total_alerts":    total_alerts,
        "hour_alerts":     hour_alerts,
        "top_zones": [
            {"zone": r["zone"], "count": r["cnt"]} for r in top_zones
        ],
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _infer_severity(rule: str) -> str:
    if rule in ("zone_entry",):
        return "high"
    if rule in ("hover", "circling"):
        return "medium"
    return "low"
