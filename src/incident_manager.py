"""
incident_manager.py — Incident lifecycle manager for AerialGuard.

An "incident" covers the entire flight lifecycle of one tracked object:
from first detection to when the track disappears for > timeout seconds.

Enhanced v2 tracking:
  hover_duration    — total time the object spent hovering
  path_length       — cumulative pixel path across the incident
  closest_approach  — minimum distance to the reference point (frame centre)
  zone_crossings    — number of zone boundary-entry events
  behavior_tag      — most-recently-seen behavior label
  threat_score      — peak threat score during the incident
"""

import json
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

import cv2
import numpy as np


_CLIPS_DIR = Path(__file__).parent.parent / "data" / "clips"


class _ActiveIncident:
    """Runtime state for one open incident."""

    def __init__(
        self,
        incident_id: int,
        track_id: int,
        start_time: float,
        frame_size: tuple,
        fps: float,
        save_clips: bool,
    ):
        self.incident_id = incident_id
        self.track_id    = track_id
        self.start_time  = start_time
        self.last_seen   = start_time

        # Accumulated speed analytics
        self.max_speed   = 0.0
        self._speed_sum  = 0.0
        self._speed_cnt  = 0
        self.frame_count = 0

        # Zone / rule tracking
        self.zones_entered:    List[str] = []
        self.triggered_rules:  List[str] = []
        self.zone_crossings:   int       = 0
        self.thumb_saved:      bool      = False

        # Path & proximity
        self.entry_point: Optional[tuple] = None
        self.exit_point:  Optional[tuple] = None
        self.path_length:       float = 0.0
        self.closest_approach:  float = float("inf")

        # Hover
        self.hover_duration: float = 0.0

        # Behavior & threat
        self.behavior_tag:  str = ""
        self.threat_score:  int = 0

        # Video writer
        self._writer: Optional[cv2.VideoWriter] = None
        if save_clips:
            _CLIPS_DIR.mkdir(parents=True, exist_ok=True)
            clip_path = _CLIPS_DIR / f"inc_{incident_id:05d}.mp4"
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            self._writer = cv2.VideoWriter(
                str(clip_path), fourcc, fps, frame_size
            )

    def update(self, info: dict, alerts: List[dict], frame: np.ndarray):
        """Feed one frame of analytics data into this incident."""
        self.last_seen    = time.time()
        self.frame_count += 1

        spd = info.get("speed", 0.0)
        self.max_speed   = max(self.max_speed, spd)
        self._speed_sum += spd
        self._speed_cnt += 1

        self.exit_point = info["centroid"]
        if self.entry_point is None:
            self.entry_point = info["centroid"]

        # Path length & closest approach (carried directly from analytics)
        self.path_length      = max(self.path_length, info.get("path_length", 0.0))
        closest = info.get("closest_approach", float("inf"))
        self.closest_approach = min(self.closest_approach, closest)

        # Hover duration (take the latest value; it resets when not hovering)
        if info.get("hovering"):
            self.hover_duration = max(
                self.hover_duration, info.get("hover_duration", 0.0)
            )

        # Behavior & threat
        if info.get("behavior_label"):
            self.behavior_tag = info["behavior_label"]
        if info.get("threat_score", 0) > self.threat_score:
            self.threat_score = info["threat_score"]

        # Zone tracking (unique zones; count every entry event from alerts)
        for z in info.get("current_zones", []):
            if z not in self.zones_entered:
                self.zones_entered.append(z)

        # Rule tracking + zone crossing count + thumbnail
        for a in alerts:
            if a["track_id"] != self.track_id:
                continue
            rule = a["rule"]
            if rule not in self.triggered_rules:
                self.triggered_rules.append(rule)
            if rule == "zone_entry":
                self.zone_crossings += 1
            if not self.thumb_saved:
                thumb_path = _CLIPS_DIR / f"inc_{self.incident_id:05d}_thumb.jpg"
                cv2.imwrite(str(thumb_path), frame)
                self.thumb_saved = True

        if self._writer is not None:
            self._writer.write(frame)

    @property
    def avg_speed(self) -> float:
        return self._speed_sum / max(self._speed_cnt, 1)

    def close(self, final_frame: Optional[np.ndarray] = None):
        if self._writer is not None:
            if final_frame is not None:
                self._writer.write(final_frame)
            self._writer.release()
            self._writer = None

    def has_clip(self) -> bool:
        path = _CLIPS_DIR / f"inc_{self.incident_id:05d}.mp4"
        return path.exists() and path.stat().st_size > 0

    def has_thumb(self) -> bool:
        return (_CLIPS_DIR / f"inc_{self.incident_id:05d}_thumb.jpg").exists()


def _generate_summary(inc: _ActiveIncident) -> str:
    duration = inc.last_seen - inc.start_time
    parts: List[str] = [
        f"Track #{inc.track_id} was detected at "
        f"{time.strftime('%H:%M:%S', time.localtime(inc.start_time))}."
    ]
    if duration >= 1:
        m, s = divmod(int(duration), 60)
        parts.append(
            f"The object was tracked for {f'{m}m {s}s' if m else f'{s}s'}."
        )
    if inc.max_speed >= 1.0:
        parts.append(f"Maximum speed: {inc.max_speed:.1f} m/s.")
    if inc.hover_duration >= 3.0:
        parts.append(f"Object hovered for {inc.hover_duration:.0f}s.")
    if inc.zones_entered:
        parts.append(
            f"Entered restricted zone(s): {', '.join(inc.zones_entered)}."
        )
    if inc.zone_crossings > 1:
        parts.append(f"Zone boundary crossed {inc.zone_crossings} time(s).")
    if "hover" in inc.triggered_rules:
        parts.append("Object was detected hovering near the facility.")
    if "circling" in inc.triggered_rules:
        parts.append("Object performed circling manoeuvres.")
    if inc.behavior_tag:
        from behavior_classifier import BEHAVIOR_DISPLAY
        display = BEHAVIOR_DISPLAY.get(inc.behavior_tag, inc.behavior_tag)
        parts.append(f"Classified behavior: {display}.")
    if not inc.triggered_rules:
        parts.append("No alert rules were triggered during this incident.")
    return " ".join(parts)


class IncidentManager:
    """
    Manages the full lifecycle of aerial intrusion incidents.

    Args:
        fps:          Video FPS (used for clip recording).
        frame_size:   (width, height) of video frames.
        disappear_s:  Seconds after last detection before closing an incident.
        save_clips:   Whether to record MP4 clips (default True).
        event_cb:     Optional callback(event_dict) called on lifecycle events.
    """

    def __init__(
        self,
        fps: float = 30.0,
        frame_size: tuple = (640, 480),
        disappear_s: float = 4.0,
        save_clips: bool = True,
        event_cb: Optional[Callable] = None,
    ):
        self.fps         = fps
        self.frame_size  = frame_size
        self.disappear_s = disappear_s
        self.save_clips  = save_clips
        self._event_cb   = event_cb
        self._active: Dict[int, _ActiveIncident] = {}

        self._db_ok = False
        try:
            import database as _db
            _db.init_db()
            self._db_ok = True
        except Exception:
            pass

    # ── Per-frame update ──────────────────────────────────────────────────────

    def update(
        self,
        analytics: Dict[int, dict],
        alerts: List[dict],
        frame: np.ndarray,
        timestamp: float,
    ) -> List[dict]:
        """
        Call once per frame.
        Returns list of lifecycle events (incident_start, incident_end).
        """
        now        = timestamp
        events:    List[dict] = []
        active_tids = set(analytics.keys())

        # Open new incidents
        for tid, info in analytics.items():
            if tid not in self._active:
                inc_id = self._db_create(tid, now) if self._db_ok else id(object())
                inc = _ActiveIncident(
                    incident_id=inc_id,
                    track_id=tid,
                    start_time=now,
                    frame_size=self.frame_size,
                    fps=self.fps,
                    save_clips=self.save_clips,
                )
                self._active[tid] = inc
                ev = {
                    "type":        "incident_start",
                    "incident_id": inc_id,
                    "track_id":    tid,
                    "timestamp":   now,
                }
                events.append(ev)
                if self._event_cb:
                    self._event_cb(ev)

        # Update active incidents
        for tid in list(self._active):
            if tid in analytics:
                self._active[tid].update(analytics[tid], alerts, frame)

        # Close disappeared incidents
        for tid in list(self._active):
            if tid not in active_tids:
                inc = self._active[tid]
                if now - inc.last_seen >= self.disappear_s:
                    inc.close()
                    summary = _generate_summary(inc)
                    if self._db_ok:
                        self._db_close(inc, now, summary)
                    ev = {
                        "type":            "incident_end",
                        "incident_id":     inc.incident_id,
                        "track_id":        tid,
                        "start_time":      inc.start_time,
                        "end_time":        now,
                        "duration":        round(now - inc.start_time, 1),
                        "max_speed":       round(inc.max_speed, 2),
                        "avg_speed":       round(inc.avg_speed, 2),
                        "hover_duration":  round(inc.hover_duration, 1),
                        "zone_crossings":  inc.zone_crossings,
                        "threat_score":    inc.threat_score,
                        "behavior_tag":    inc.behavior_tag,
                        "zones_entered":   inc.zones_entered,
                        "triggered_rules": inc.triggered_rules,
                        "summary":         summary,
                        "has_clip":        inc.has_clip(),
                        "has_thumb":       inc.has_thumb(),
                    }
                    events.append(ev)
                    if self._event_cb:
                        self._event_cb(ev)
                    del self._active[tid]

        return events

    def get_active_incidents(self) -> List[dict]:
        """Current open incidents for the web dashboard."""
        now = time.time()
        return [
            {
                "incident_id":     inc.incident_id,
                "track_id":        inc.track_id,
                "start_time":      inc.start_time,
                "duration":        round(now - inc.start_time, 1),
                "zones_entered":   inc.zones_entered,
                "triggered_rules": inc.triggered_rules,
                "max_speed":       round(inc.max_speed, 2),
                "hover_duration":  round(inc.hover_duration, 1),
                "zone_crossings":  inc.zone_crossings,
                "behavior_tag":    inc.behavior_tag,
                "threat_score":    inc.threat_score,
            }
            for inc in self._active.values()
        ]

    # ── Database helpers ──────────────────────────────────────────────────────

    def _db_create(self, track_id: int, start_time: float) -> int:
        try:
            import database as _db
            return _db.create_incident(track_id, start_time)
        except Exception:
            return 0

    def _db_close(self, inc: _ActiveIncident, end_time: float, summary: str):
        try:
            import database as _db
            _db.close_incident(
                incident_id=inc.incident_id,
                end_time=end_time,
                duration=round(end_time - inc.start_time, 1),
                max_speed=round(inc.max_speed, 2),
                avg_speed=round(inc.avg_speed, 2),
                frame_count=inc.frame_count,
                zones_entered=json.dumps(inc.zones_entered),
                triggered_rules=json.dumps(inc.triggered_rules),
                entry_point=json.dumps(list(inc.entry_point) if inc.entry_point else None),
                exit_point=json.dumps(list(inc.exit_point) if inc.exit_point else None),
                has_clip=inc.has_clip(),
                has_thumb=inc.has_thumb(),
                summary=summary,
                hover_duration=round(inc.hover_duration, 1),
                path_length=round(inc.path_length, 1),
                behavior_tag=inc.behavior_tag,
                closest_approach=round(
                    inc.closest_approach if inc.closest_approach != float("inf") else 0, 1
                ),
                zone_crossings=inc.zone_crossings,
                threat_score=inc.threat_score,
            )
        except Exception:
            pass
