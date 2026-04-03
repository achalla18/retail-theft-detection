"""
web_server.py — Flask web dashboard for the AI Surveillance system.

Provides:
  GET  /              — Main dashboard HTML
  GET  /video_feed    — MJPEG live video stream
  GET  /api/stats     — JSON: FPS, people count, alert counts
  GET  /api/alerts    — JSON: alert history (query params: limit, since)
  GET  /api/zones     — JSON: zone definitions
  GET  /api/events    — Server-Sent Events stream for real-time updates

The CV pipeline (main.py) shares data with this module via SharedState.
Thread-safe by design; no external dependencies beyond Flask.
"""

import json
import logging
import queue
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template, request

# ── Shared state ──────────────────────────────────────────────────────────────

class SharedState:
    """Thread-safe container for data shared between the CV pipeline and Flask."""

    def __init__(self):
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._stats: dict = {
            "fps": 0.0,
            "people_count": 0,
            "uptime": 0.0,
            "status": "starting",
        }
        self._zones: list = []
        self._start_time = time.time()

        # SSE subscriber queues
        self._eq_lock = threading.Lock()
        self._event_queues: list[queue.Queue] = []

    # ── Frame sharing ─────────────────────────────────────────────────

    def update_frame(self, frame: np.ndarray):
        with self._lock:
            self._frame = frame.copy()

    def get_frame(self) -> np.ndarray | None:
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    # ── Stats ─────────────────────────────────────────────────────────

    def update_stats(self, fps: float, people_count: int, status: str = "running"):
        with self._lock:
            self._stats = {
                "fps": round(fps, 1),
                "people_count": people_count,
                "uptime": round(time.time() - self._start_time),
                "status": status,
            }

    def get_stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    # ── Zones ─────────────────────────────────────────────────────────

    def update_zones(self, zones: list):
        with self._lock:
            self._zones = list(zones)

    def get_zones(self) -> list:
        with self._lock:
            return list(self._zones)

    # ── SSE event broadcasting ─────────────────────────────────────────

    def push_event(self, event: dict):
        """Broadcast an event dict to all connected SSE clients."""
        payload = json.dumps(event)
        with self._eq_lock:
            dead = []
            for q in self._event_queues:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._event_queues.remove(q)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=64)
        with self._eq_lock:
            self._event_queues.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._eq_lock:
            try:
                self._event_queues.remove(q)
            except ValueError:
                pass


# Module-level singleton — imported and used by main.py
shared_state = SharedState()


# ── Flask application ─────────────────────────────────────────────────────────

_ROOT = Path(__file__).parent.parent  # project root

app = Flask(
    __name__,
    template_folder=str(_ROOT / "templates"),
    static_folder=str(_ROOT / "static"),
)


@app.route("/")
def index():
    return render_template("index.html")


# ── Video stream ──────────────────────────────────────────────────────────────

_PLACEHOLDER: np.ndarray | None = None

def _get_placeholder() -> np.ndarray:
    global _PLACEHOLDER
    if _PLACEHOLDER is None:
        img = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(img, "Waiting for video feed...", (130, 240),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (60, 60, 60), 2)
        _PLACEHOLDER = img
    return _PLACEHOLDER


def _mjpeg_generator():
    while True:
        frame = shared_state.get_frame()
        if frame is None:
            frame = _get_placeholder()

        ok, buf = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82]
        )
        if not ok:
            time.sleep(0.04)
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + buf.tobytes()
            + b"\r\n"
        )
        time.sleep(1 / 25)  # ~25 FPS cap


@app.route("/video_feed")
def video_feed():
    return Response(
        _mjpeg_generator(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


# ── REST API ──────────────────────────────────────────────────────────────────

@app.route("/api/stats")
def api_stats():
    from database import get_stats as db_stats
    stats = shared_state.get_stats()
    try:
        db = db_stats()
    except Exception:
        db = {"total": 0, "today": 0, "last_hour": 0, "top_zones": []}
    return jsonify({**stats, **db})


@app.route("/api/alerts")
def api_alerts():
    from database import get_recent_alerts
    limit = request.args.get("limit", 100, type=int)
    since = request.args.get("since", None, type=float)
    try:
        alerts = get_recent_alerts(limit=limit, since=since)
    except Exception:
        alerts = []
    return jsonify(alerts)


@app.route("/api/zones")
def api_zones():
    return jsonify(shared_state.get_zones())


# ── Server-Sent Events ────────────────────────────────────────────────────────

@app.route("/api/events")
def api_events():
    """Long-lived SSE connection; pushes stats keepalives + alert events."""
    q = shared_state.subscribe()

    def generate():
        # Send an immediate stats snapshot so the dashboard populates instantly
        try:
            from database import get_stats as db_stats
            db = db_stats()
        except Exception:
            db = {}
        init_payload = {
            "type": "stats",
            "data": {**shared_state.get_stats(), **db},
        }
        yield f"data: {json.dumps(init_payload)}\n\n"

        try:
            while True:
                try:
                    raw = q.get(timeout=15)
                    yield f"data: {raw}\n\n"
                except queue.Empty:
                    # Keepalive — also refreshes stats counters
                    try:
                        from database import get_stats as db_stats
                        db = db_stats()
                    except Exception:
                        db = {}
                    ping = {
                        "type": "stats",
                        "data": {**shared_state.get_stats(), **db},
                    }
                    yield f"data: {json.dumps(ping)}\n\n"
        except GeneratorExit:
            shared_state.unsubscribe(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Server launcher ───────────────────────────────────────────────────────────

def start(host: str = "0.0.0.0", port: int = 5000) -> threading.Thread:
    """Start Flask in a daemon thread. Returns the thread."""
    # Silence Flask's per-request logs so they don't clutter the CV console
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    t = threading.Thread(
        target=lambda: app.run(
            host=host,
            port=port,
            debug=False,
            use_reloader=False,
            threaded=True,
        ),
        daemon=True,
        name="flask-server",
    )
    t.start()
    return t
