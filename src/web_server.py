"""
web_server.py — AerialGuard Flask web server (v2).

Serves the 5-page SOC dashboard and exposes the REST + SSE API.

Routes
──────
GET  /                           → index.html (5-page SPA)
GET  /video_feed                 → MJPEG stream (25 fps cap)
GET  /api/events                 → SSE: stats, alert, incident_start/end
GET  /api/status                 → JSON: fps, risk score, object count, uptime
GET  /api/objects                → JSON: tracked objects + behavior + threat
GET  /api/zones                  → JSON: zone definitions + breach status
GET  /api/tactical               → JSON: track trails + positions for canvas map
GET  /api/incidents              → JSON: paginated closed incident list
GET  /api/incidents/<id>         → JSON: incident detail + track_points + alerts
GET  /api/incidents/<id>/clip    → MP4 video clip
GET  /api/incidents/<id>/thumb   → JPEG thumbnail
GET  /api/analytics/tracks       → JSON: recent track_points for live charts
GET  /api/analytics/object/<tid> → JSON: full track history for Flight Analytics
GET  /api/alerts/queue           → JSON: SOC alert queue (newest first)
POST /api/alerts/<id>/status     → Update alert status
"""

import json
import logging
import os
import queue
import threading
import time
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np
from flask import (
    Flask, Response, jsonify, render_template,
    request, send_file
)

_ROOT      = Path(__file__).parent.parent
_CLIPS_DIR = _ROOT / "data" / "clips"

# Max trail positions kept per track for the tactical map
_TRAIL_LEN = 120


# ── Shared state ──────────────────────────────────────────────────────────────

class SharedState:
    """Thread-safe container bridging the CV pipeline and Flask."""

    def __init__(self):
        self._lock  = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._status: dict = {
            "fps": 0.0, "object_count": 0, "risk_score": 0,
            "status": "starting", "uptime": 0.0,
        }
        self._objects: Dict[int, dict] = {}
        self._zones:   List[dict]      = []
        self._start    = time.time()

        # Per-track position trails {track_id: deque(maxlen=_TRAIL_LEN)}
        self._trails: Dict[int, deque] = {}

        # SSE queues
        self._eq_lock = threading.Lock()
        self._eq:  List[queue.Queue] = []

    # Frame
    def update_frame(self, frame: np.ndarray):
        with self._lock:
            self._frame = frame.copy()

    def get_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    # System status
    def update_status(
        self, fps: float, object_count: int, risk_score: int,
        status: str = "running"
    ):
        with self._lock:
            self._status = {
                "fps":          round(fps, 1),
                "object_count": object_count,
                "risk_score":   risk_score,
                "status":       status,
                "uptime":       round(time.time() - self._start),
            }

    def get_status(self) -> dict:
        with self._lock:
            return dict(self._status)

    # Active objects (enriched with behavior + threat score)
    def update_objects(self, objects: Dict[int, dict]):
        with self._lock:
            self._objects = dict(objects)
            # Update trails
            active = set(objects.keys())
            for tid in list(self._trails):
                if tid not in active:
                    del self._trails[tid]
            for tid, obj in objects.items():
                if tid not in self._trails:
                    self._trails[tid] = deque(maxlen=_TRAIL_LEN)
                cx, cy = obj.get("centroid", (0, 0))
                self._trails[tid].append((cx, cy))

    def get_objects(self) -> List[dict]:
        with self._lock:
            return list(self._objects.values())

    # Zones
    def update_zones(self, zones: List[dict]):
        with self._lock:
            self._zones = list(zones)

    def get_zones(self) -> List[dict]:
        with self._lock:
            return list(self._zones)

    # Tactical map data
    def get_tactical(self) -> dict:
        with self._lock:
            tracks = []
            for tid, obj in self._objects.items():
                trail = list(self._trails.get(tid, []))
                tracks.append({
                    "track_id":       tid,
                    "centroid":       obj.get("centroid", (0, 0)),
                    "trail":          trail,
                    "threat_level":   obj.get("threat_level", "low"),
                    "threat_color":   obj.get("threat_color", "#00e87a"),
                    "threat_score":   obj.get("threat_score", 0),
                    "behavior_label": obj.get("behavior_label", "unknown"),
                    "behavior_display": obj.get("behavior_display", "Unknown"),
                    "speed":          obj.get("speed", 0),
                    "hovering":       obj.get("hovering", False),
                    "circling":       obj.get("circling", False),
                    "confidence":     obj.get("confidence", 0),
                    "time_in_frame":  obj.get("time_in_frame", 0),
                })
            return {
                "tracks":    tracks,
                "zones":     list(self._zones),
                "timestamp": time.time(),
            }

    # SSE
    def push_event(self, event: dict):
        payload = json.dumps(event)
        with self._eq_lock:
            dead = []
            for q in self._eq:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._eq.remove(q)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=128)
        with self._eq_lock:
            self._eq.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._eq_lock:
            try:
                self._eq.remove(q)
            except ValueError:
                pass


shared_state = SharedState()


# ── Flask app ─────────────────────────────────────────────────────────────────

app = Flask(
    __name__,
    template_folder=str(_ROOT / "templates"),
    static_folder=str(_ROOT / "static"),
)


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── Video stream ──────────────────────────────────────────────────────────────

_PLACEHOLDER: Optional[np.ndarray] = None


def _placeholder() -> np.ndarray:
    global _PLACEHOLDER
    if _PLACEHOLDER is None:
        img = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(
            img, "AerialGuard — Waiting for feed",
            (80, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (40, 80, 40), 2
        )
        _PLACEHOLDER = img
    return _PLACEHOLDER


def _mjpeg_stream():
    while True:
        frame = shared_state.get_frame() or _placeholder()
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if ok:
            yield (
                b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                + buf.tobytes()
                + b"\r\n"
            )
        time.sleep(1 / 25)


@app.route("/video_feed")
def video_feed():
    return Response(
        _mjpeg_stream(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


# ── SSE stream ────────────────────────────────────────────────────────────────

@app.route("/api/events")
def api_events():
    q = shared_state.subscribe()

    def generate():
        try:
            from database import get_stats as _db_stats
            db = _db_stats()
        except Exception:
            db = {}
        yield (
            f"data: {json.dumps({'type':'status','data':{**shared_state.get_status(),**db}})}\n\n"
        )
        try:
            while True:
                try:
                    yield f"data: {q.get(timeout=15)}\n\n"
                except queue.Empty:
                    try:
                        from database import get_stats as _db_stats
                        db = _db_stats()
                    except Exception:
                        db = {}
                    yield (
                        f"data: {json.dumps({'type':'status','data':{**shared_state.get_status(),**db}})}\n\n"
                    )
        except GeneratorExit:
            shared_state.unsubscribe(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )


# ── REST — live data ──────────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    try:
        from database import get_stats as _db_stats
        db = _db_stats()
    except Exception:
        db = {}
    return jsonify({**shared_state.get_status(), **db})


@app.route("/api/objects")
def api_objects():
    return jsonify(shared_state.get_objects())


@app.route("/api/zones")
def api_zones():
    return jsonify(shared_state.get_zones())


@app.route("/api/tactical")
def api_tactical():
    return jsonify(shared_state.get_tactical())


# ── REST — incidents ──────────────────────────────────────────────────────────

@app.route("/api/incidents")
def api_incidents():
    limit  = request.args.get("limit",  50, type=int)
    offset = request.args.get("offset",  0, type=int)
    try:
        from database import get_incidents
        data = get_incidents(limit=limit, offset=offset)
    except Exception:
        data = []
    for inc in data:
        inc_id = inc["id"]
        inc["has_clip"]  = (_CLIPS_DIR / f"inc_{inc_id:05d}.mp4").exists()
        inc["has_thumb"] = (_CLIPS_DIR / f"inc_{inc_id:05d}_thumb.jpg").exists()
    return jsonify(data)


@app.route("/api/incidents/<int:inc_id>")
def api_incident_detail(inc_id: int):
    try:
        from database import get_incident, get_track_points, get_incident_alerts
        inc    = get_incident(inc_id)
        points = get_track_points(inc_id)
        alerts = get_incident_alerts(inc_id)
    except Exception:
        return jsonify({"error": "not found"}), 404
    if inc is None:
        return jsonify({"error": "not found"}), 404
    inc["track_points"] = points
    inc["alerts"]       = alerts
    inc["has_clip"]     = (_CLIPS_DIR / f"inc_{inc_id:05d}.mp4").exists()
    inc["has_thumb"]    = (_CLIPS_DIR / f"inc_{inc_id:05d}_thumb.jpg").exists()
    return jsonify(inc)


@app.route("/api/incidents/<int:inc_id>/clip")
def api_incident_clip(inc_id: int):
    path = _CLIPS_DIR / f"inc_{inc_id:05d}.mp4"
    if not path.exists():
        return jsonify({"error": "clip not found"}), 404
    return send_file(str(path), mimetype="video/mp4", conditional=True)


@app.route("/api/incidents/<int:inc_id>/thumb")
def api_incident_thumb(inc_id: int):
    path = _CLIPS_DIR / f"inc_{inc_id:05d}_thumb.jpg"
    if not path.exists():
        return jsonify({"error": "thumb not found"}), 404
    return send_file(str(path), mimetype="image/jpeg")


# ── REST — analytics charts ───────────────────────────────────────────────────

@app.route("/api/analytics/tracks")
def api_analytics_tracks():
    since = request.args.get("since", time.time() - 60, type=float)
    limit = request.args.get("limit", 500, type=int)
    tids  = [obj["track_id"] for obj in shared_state.get_objects()]
    try:
        from database import get_live_track_points
        points = get_live_track_points(tids, since=since, limit=limit)
    except Exception:
        points = []
    return jsonify(points)


@app.route("/api/analytics/object/<int:track_id>")
def api_analytics_object(track_id: int):
    """Full track history for one object (Flight Analytics page)."""
    since = request.args.get("since", time.time() - 3600, type=float)
    limit = request.args.get("limit", 600, type=int)
    try:
        from database import get_object_track_points
        points = get_object_track_points(track_id, since=since, limit=limit)
    except Exception:
        points = []
    return jsonify(points)


# ── REST — alert queue ────────────────────────────────────────────────────────

@app.route("/api/alerts/queue")
def api_alert_queue():
    limit  = request.args.get("limit",  100, type=int)
    offset = request.args.get("offset",   0, type=int)
    status = request.args.get("status",  None)
    try:
        from database import get_alert_queue
        data = get_alert_queue(limit=limit, offset=offset, status_filter=status)
    except Exception:
        data = []
    return jsonify(data)


@app.route("/api/alerts/<int:alert_id>/status", methods=["POST"])
def api_alert_status(alert_id: int):
    body = request.get_json(silent=True) or {}
    status = body.get("status", "")
    try:
        from database import update_alert_status
        ok = update_alert_status(alert_id, status)
    except Exception:
        ok = False
    if not ok:
        return jsonify({"error": "invalid status or alert not found"}), 400
    return jsonify({"ok": True, "alert_id": alert_id, "status": status})


# ── Server launcher ───────────────────────────────────────────────────────────

def start(host: str = "0.0.0.0", port: int = 5000) -> threading.Thread:
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    t = threading.Thread(
        target=lambda: app.run(
            host=host, port=port,
            debug=False, use_reloader=False, threaded=True,
        ),
        daemon=True,
        name="flask-aerialguard",
    )
    t.start()
    return t
