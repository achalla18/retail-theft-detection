"""
analytics.py — Per-frame flight analytics for AerialGuard.

Computes, for every tracked object every frame:
  speed / avg_speed / max_speed / acceleration
  altitude proxy        — from bounding-box apparent size
  hover state           — sustained low-speed loiter
  circling state        — angular displacement over a time window
  time in frame         — seconds since first detection
  entry point           — (cx, cy) of first detection
  path_length           — cumulative pixel path
  path_straightness     — Euclidean / cumulative ratio (1 = straight line)
  heading               — current direction of motion (degrees)
  heading_volatility    — normalised variance of heading changes (0–1)
  closest_approach      — minimum distance to frame-centre reference (px)
  loiter_radius         — max displacement from hover centre (px)
  approach_aggressiveness — rate of closing distance to reference (0–1)
  uncertainty_score     — confidence instability (0–1)

Also computes a 0–100 system-wide risk score each frame.

All estimates are approximate and depend on a rough camera calibration
stored in config/settings.json under "analytics.calibration".
"""

import math
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

# Frame-centre reference point (proxy for the protected facility).
# Override by setting FlightAnalytics.ref_x / ref_y after construction.
_DEFAULT_REF_X = 320
_DEFAULT_REF_Y = 240


# ── Per-track mutable state ───────────────────────────────────────────────────

class _TrackState:
    SPEED_WINDOW  = 90     # samples (~3 s at 30 fps) for rolling speed average
    MAX_POSITIONS = 600    # position history cap (20 s at 30 fps)

    def __init__(
        self,
        track_id: int,
        centroid: Tuple[int, int],
        bbox: List[int],
        timestamp: float,
        ref_x: int = _DEFAULT_REF_X,
        ref_y: int = _DEFAULT_REF_Y,
    ):
        self.track_id    = track_id
        self.entry_point = centroid
        self.first_seen  = timestamp
        self.last_seen   = timestamp
        self._ref_x      = ref_x
        self._ref_y      = ref_y

        # Position & bbox history
        self.positions: deque = deque(maxlen=self.MAX_POSITIONS)
        self.positions.append((timestamp, centroid[0], centroid[1]))
        self.bbox_history: deque = deque(maxlen=120)
        self.bbox_history.append(bbox)

        # Speed
        self.current_speed = 0.0
        self.max_speed     = 0.0
        self._speed_buf: deque = deque(maxlen=self.SPEED_WINDOW)
        self._prev_speed   = 0.0

        # Acceleration
        self.acceleration = 0.0

        # Hover
        self.hovering            = False
        self.hover_duration      = 0.0
        self._hover_start:    Optional[float] = None
        self._low_speed_since: Optional[float] = None
        self._hover_center: Optional[Tuple[float, float]] = None

        # Circling
        self.circling = False

        # Heading
        self.heading              = 0.0   # radians
        self._heading_buf: deque  = deque(maxlen=60)
        self.heading_volatility   = 0.0

        # Path
        self.path_length     = 0.0
        self.path_straightness = 1.0

        # Approach
        init_dist = math.hypot(centroid[0] - ref_x, centroid[1] - ref_y)
        self.closest_approach        = init_dist
        self.loiter_radius           = 0.0
        self.approach_aggressiveness = 0.0
        self._dist_buf: deque = deque(maxlen=30)
        self._dist_buf.append(init_dist)

        # Uncertainty
        self._conf_buf: deque  = deque(maxlen=30)
        self.uncertainty_score = 0.0

        # Zone membership (updated by ZoneManager)
        self.current_zones: List[str] = []


# ── Main analytics engine ─────────────────────────────────────────────────────

class FlightAnalytics:
    """
    Stateful analytics engine — call update() once per frame.

    Configuration keys (all optional, under "analytics.calibration"):
      pixels_per_meter      float
      reference_bbox_height float
      reference_altitude_m  float
      hover_speed_mps       float
      hover_confirm_s       float
      circle_window_s       float
      circle_min_rad        float
    """

    _D_PX_PER_M  = 80.0
    _D_REF_BBH   = 40.0
    _D_REF_ALT   = 30.0
    _D_HOVER_SPD = 0.8
    _D_HOVER_CFM = 3.0
    _D_CIRC_WIN  = 6.0
    _D_CIRC_RAD  = 1.8   # ≈ 103°

    def __init__(
        self,
        fps: float = 30.0,
        calibration: dict | None = None,
        ref_x: int = _DEFAULT_REF_X,
        ref_y: int = _DEFAULT_REF_Y,
    ):
        self.fps   = max(fps, 1.0)
        self.ref_x = ref_x
        self.ref_y = ref_y
        cal = calibration or {}

        self._px_per_m  = cal.get("pixels_per_meter",      self._D_PX_PER_M)
        self._ref_bbh   = cal.get("reference_bbox_height",  self._D_REF_BBH)
        self._ref_alt   = cal.get("reference_altitude_m",   self._D_REF_ALT)
        self._hover_spd = cal.get("hover_speed_mps",        self._D_HOVER_SPD)
        self._hover_cfm = cal.get("hover_confirm_s",        self._D_HOVER_CFM)
        self._circ_win  = cal.get("circle_window_s",        self._D_CIRC_WIN)
        self._circ_rad  = cal.get("circle_min_rad",         self._D_CIRC_RAD)

        self._tracks: Dict[int, _TrackState] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def update(
        self, detections: List[dict], timestamp: float
    ) -> Dict[int, dict]:
        """
        Process one frame's detections.
        Returns {track_id: analytics_dict} with all metrics.
        """
        active_ids = {d["track_id"] for d in detections}

        # Remove stale tracks
        for tid in list(self._tracks):
            if tid not in active_ids:
                del self._tracks[tid]

        result: Dict[int, dict] = {}

        for det in detections:
            tid    = det["track_id"]
            cx, cy = det["centroid"]
            bbox   = det["bbox"]
            conf   = det["confidence"]

            if tid not in self._tracks:
                self._tracks[tid] = _TrackState(
                    tid, (cx, cy), bbox, timestamp, self.ref_x, self.ref_y
                )

            state = self._tracks[tid]
            speed = self._compute_speed(state, cx, cy, timestamp)

            # Acceleration
            dt = max(timestamp - state.last_seen, 1e-6)
            state.acceleration = (speed - state._prev_speed) / dt
            state._prev_speed  = speed

            state.last_seen = timestamp
            state.positions.append((timestamp, cx, cy))
            state.bbox_history.append(bbox)
            state._speed_buf.append(speed)
            state.current_speed = speed
            state.max_speed     = max(state.max_speed, speed)

            # Path length
            if len(state.positions) >= 2:
                prev = state.positions[-2]
                state.path_length += math.hypot(cx - prev[1], cy - prev[2])

            # Heading
            self._update_heading(state, cx, cy)

            # Closest approach & approach aggressiveness
            dist = math.hypot(cx - self.ref_x, cy - self.ref_y)
            state.closest_approach = min(state.closest_approach, dist)
            state._dist_buf.append(dist)
            if len(state._dist_buf) >= 6:
                recent = list(state._dist_buf)[-6:]
                delta  = recent[0] - recent[-1]   # positive = approaching
                state.approach_aggressiveness = max(
                    0.0, min(1.0, delta / (50.0 * len(recent)))
                )

            # Path straightness (Euclidean / cumulative)
            ep = state.entry_point
            straight = math.hypot(cx - ep[0], cy - ep[1])
            if state.path_length > 10:
                state.path_straightness = min(
                    straight / state.path_length, 1.0
                )

            # Loiter radius when hovering
            if state.hovering and state._hover_center:
                hx, hy = state._hover_center
                state.loiter_radius = max(
                    state.loiter_radius, math.hypot(cx - hx, cy - hy)
                )

            # Confidence & uncertainty
            state._conf_buf.append(conf)
            if len(state._conf_buf) >= 5:
                mean_c = sum(state._conf_buf) / len(state._conf_buf)
                var_c  = sum(
                    (c - mean_c) ** 2 for c in state._conf_buf
                ) / len(state._conf_buf)
                state.uncertainty_score = min(math.sqrt(var_c) * 5.0, 1.0)

            # Hover & circling
            self._update_hover(state, speed, timestamp, cx, cy)
            state.circling = self._detect_circling(state, timestamp)

            # Altitude proxy from bbox height
            bbox_h   = max(bbox[3] - bbox[1], 1)
            altitude = (self._ref_alt * self._ref_bbh) / bbox_h
            avg_speed = (
                sum(state._speed_buf) / len(state._speed_buf)
                if state._speed_buf else 0.0
            )

            result[tid] = {
                # Identity
                "track_id":               tid,
                "centroid":               (cx, cy),
                "bbox":                   bbox,
                "confidence":             round(conf, 3),
                # Speed
                "speed":                  round(speed, 2),
                "avg_speed":              round(avg_speed, 2),
                "max_speed":              round(state.max_speed, 2),
                "acceleration":           round(state.acceleration, 3),
                # Position / altitude
                "altitude_proxy":         round(altitude, 1),
                "distance_proxy":         round(altitude, 1),
                # Time
                "time_in_frame":          round(timestamp - state.first_seen, 1),
                "entry_point":            state.entry_point,
                # Hover
                "hovering":               state.hovering,
                "hover_duration":         round(state.hover_duration, 1),
                # Circling
                "circling":               state.circling,
                # Path metrics
                "path_length":            round(state.path_length, 1),
                "path_straightness":      round(state.path_straightness, 3),
                "closest_approach":       round(state.closest_approach, 1),
                "loiter_radius":          round(state.loiter_radius, 1),
                "approach_aggressiveness": round(state.approach_aggressiveness, 3),
                # Heading
                "heading":                round(math.degrees(state.heading), 1),
                "heading_volatility":     round(state.heading_volatility, 3),
                # Uncertainty
                "uncertainty_score":      round(state.uncertainty_score, 3),
                # Zones
                "current_zones":          list(state.current_zones),
            }

        return result

    def compute_risk_score(
        self,
        analytics: Dict[int, dict],
        active_alerts: List[dict],
    ) -> int:
        """
        0–100 system-wide risk score.

        Weights:
          Objects present        → up to 20 pts
          Zone-breach alerts     → up to 35 pts
          Hover alerts           → up to 25 pts
          Circling alerts        → up to 20 pts
          Direct hover/circle    → 4 pts each
        """
        score = 0
        score += min(len(analytics) * 7, 20)
        rules  = [a.get("rule", "") for a in active_alerts]
        score += min(rules.count("zone_entry") * 18, 35)
        score += min(rules.count("hover")      * 12, 25)
        score += min(rules.count("circling")   * 10, 20)
        for a in analytics.values():
            if a.get("hovering"):  score += 4
            if a.get("circling"):  score += 4
        return min(score, 100)

    def get_positions(self, track_id: int) -> List[Tuple]:
        """Return full (timestamp, cx, cy) history for one track."""
        state = self._tracks.get(track_id)
        return list(state.positions) if state else []

    def set_zones(self, track_id: int, zone_names: List[str]):
        """Let ZoneManager push the current zone membership for a track."""
        state = self._tracks.get(track_id)
        if state:
            state.current_zones = zone_names

    def cleanup(self, track_id: int):
        self._tracks.pop(track_id, None)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _compute_speed(
        self, state: _TrackState, cx: int, cy: int, timestamp: float
    ) -> float:
        if not state.positions:
            return 0.0
        prev_ts, prev_cx, prev_cy = state.positions[-1]
        dt      = max(timestamp - prev_ts, 1e-6)
        px_dist = math.hypot(cx - prev_cx, cy - prev_cy)
        return (px_dist / self._px_per_m) / dt

    def _update_heading(
        self, state: _TrackState, cx: int, cy: int
    ):
        if len(state.positions) < 2:
            return
        prev = state.positions[-1]
        dx, dy = cx - prev[1], cy - prev[2]
        if abs(dx) < 1 and abs(dy) < 1:
            return
        heading = math.atan2(dy, dx)
        state.heading = heading
        state._heading_buf.append(heading)

        # Heading volatility — mean absolute heading change normalised to π
        if len(state._heading_buf) >= 5:
            angles = list(state._heading_buf)
            deltas = []
            for i in range(1, len(angles)):
                d = angles[i] - angles[i - 1]
                if d >  math.pi: d -= 2 * math.pi
                elif d < -math.pi: d += 2 * math.pi
                deltas.append(abs(d))
            if deltas:
                state.heading_volatility = min(
                    sum(deltas) / len(deltas) / math.pi, 1.0
                )

    def _update_hover(
        self,
        state: _TrackState,
        speed: float,
        timestamp: float,
        cx: int,
        cy: int,
    ):
        if speed <= self._hover_spd:
            if state._low_speed_since is None:
                state._low_speed_since = timestamp
            elapsed = timestamp - state._low_speed_since
            if elapsed >= self._hover_cfm:
                if not state.hovering:
                    state.hovering      = True
                    state._hover_start  = state._low_speed_since
                    state._hover_center = (cx, cy)
                state.hover_duration = timestamp - state._hover_start
        else:
            state._low_speed_since = None
            state.hovering         = False

    def _detect_circling(
        self, state: _TrackState, timestamp: float
    ) -> bool:
        """True when total angular displacement ≥ circle_min_rad in window."""
        cutoff = timestamp - self._circ_win
        pts = [(x, y) for t, x, y in state.positions if t >= cutoff]
        if len(pts) < 12:
            return False
        mean_x = sum(p[0] for p in pts) / len(pts)
        mean_y = sum(p[1] for p in pts) / len(pts)
        max_r  = max(math.hypot(x - mean_x, y - mean_y) for x, y in pts)
        if max_r < 25:
            return False
        angles = [math.atan2(y - mean_y, x - mean_x) for x, y in pts]
        total = 0.0
        for i in range(1, len(angles)):
            d = angles[i] - angles[i - 1]
            if d >  math.pi: d -= 2 * math.pi
            elif d < -math.pi: d += 2 * math.pi
            total += abs(d)
        return total >= self._circ_rad
