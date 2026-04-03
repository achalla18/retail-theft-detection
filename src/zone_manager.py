"""
zone_manager.py — Airspace zone monitoring for AerialGuard.

Each zone is a polygon drawn over the camera frame.  When a tracked
object's centroid enters or exits a zone, a zone-event dict is emitted.
Dwell time inside each zone is accumulated per object.
"""

import cv2
import numpy as np
import time
from typing import Dict, List, Tuple


class AirspaceZone:
    """A single restricted-airspace polygon region."""

    def __init__(
        self,
        name: str,
        points: list,
        color: Tuple,
        alert_on_entry: bool = True,
    ):
        self.name           = name
        self.points         = np.array(points, dtype=np.int32)
        self.color          = tuple(color)          # BGR
        self.alert_on_entry = alert_on_entry

    def contains(self, point: Tuple[int, int]) -> bool:
        res = cv2.pointPolygonTest(self.points, (float(point[0]), float(point[1])), False)
        return res >= 0

    @property
    def color_hex(self) -> str:
        b, g, r = self.color
        return f"#{r:02x}{g:02x}{b:02x}"


class AirspaceZoneManager:
    """Manages all airspace zones and per-object dwell tracking."""

    def __init__(self, zone_configs: list):
        self.zones: List[AirspaceZone] = []
        for zc in zone_configs:
            self.zones.append(AirspaceZone(
                name=zc["name"],
                points=zc["points"],
                color=zc.get("color", [0, 0, 255]),
                alert_on_entry=zc.get("alert_on_entry", True),
            ))

        # {track_id: {zone_name: {"inside": bool, "enter_time": float, "total": float}}}
        self._dwell: Dict[int, Dict[str, dict]] = {}
        # {track_id: set(zone_names)} — for entry/exit event detection
        self._prev_membership: Dict[int, set] = {}

    # ── Per-frame update ──────────────────────────────────────────────────────

    def update(
        self, analytics: Dict[int, dict], timestamp: float | None = None
    ) -> List[dict]:
        """
        Check each tracked object against all zones.

        Args:
            analytics:  {track_id: analytics_dict} from FlightAnalytics.update()
            timestamp:  current time; defaults to time.time()

        Returns:
            List of zone-event dicts:
              {track_id, zone, event: 'enter'|'exit', timestamp, centroid,
               dwell_seconds, alert_on_entry}
        """
        now = timestamp or time.time()
        events: List[dict] = []
        active_ids = set(analytics.keys())

        # Clean up disappeared tracks
        for tid in list(self._dwell):
            if tid not in active_ids:
                del self._dwell[tid]
        for tid in list(self._prev_membership):
            if tid not in active_ids:
                del self._prev_membership[tid]

        for tid, info in analytics.items():
            cx, cy = info["centroid"]
            self._dwell.setdefault(tid, {})
            self._prev_membership.setdefault(tid, set())

            current_zones: set = set()

            for zone in self.zones:
                inside = zone.contains((cx, cy))
                zd = self._dwell[tid].setdefault(
                    zone.name,
                    {"inside": False, "enter_time": None, "total": 0.0},
                )

                was_inside = tid in self._prev_membership and zone.name in self._prev_membership[tid]

                if inside:
                    current_zones.add(zone.name)
                    if not was_inside:
                        # Entry event
                        zd["inside"]     = True
                        zd["enter_time"] = now
                        events.append({
                            "track_id":       tid,
                            "zone":           zone.name,
                            "event":          "enter",
                            "timestamp":      now,
                            "centroid":       (cx, cy),
                            "dwell_seconds":  0.0,
                            "alert_on_entry": zone.alert_on_entry,
                        })
                    else:
                        # Still inside — accumulate dwell
                        if zd["enter_time"] is not None:
                            zd["total"] = now - zd["enter_time"]
                else:
                    if was_inside:
                        # Exit event
                        dwell = zd["total"]
                        zd["inside"]     = False
                        zd["enter_time"] = None
                        events.append({
                            "track_id":      tid,
                            "zone":          zone.name,
                            "event":         "exit",
                            "timestamp":     now,
                            "centroid":      (cx, cy),
                            "dwell_seconds": round(dwell, 1),
                            "alert_on_entry": zone.alert_on_entry,
                        })

            self._prev_membership[tid] = current_zones

        return events

    # ── Accessors ─────────────────────────────────────────────────────────────

    def get_current_zones(self, track_id: int) -> List[str]:
        return list(self._prev_membership.get(track_id, set()))

    def get_dwell(self, track_id: int, zone_name: str) -> float:
        return self._dwell.get(track_id, {}).get(zone_name, {}).get("total", 0.0)

    def get_zone_statuses(self) -> List[dict]:
        """Return zone metadata + breach status for the web dashboard."""
        breached = set()
        for membership in self._prev_membership.values():
            breached.update(membership)

        return [
            {
                "name":           z.name,
                "color_hex":      z.color_hex,
                "alert_on_entry": z.alert_on_entry,
                "status":         "BREACH" if z.name in breached else "CLEAR",
                "points":         z.points.tolist(),
            }
            for z in self.zones
        ]

    def get_zones_info(self) -> List[dict]:
        """Alias used by web_server for the /api/zones endpoint."""
        return self.get_zone_statuses()

    # ── Drawing ───────────────────────────────────────────────────────────────

    def draw_zones(self, frame: np.ndarray):
        for zone in self.zones:
            overlay = frame.copy()
            cv2.fillPoly(overlay, [zone.points], zone.color)
            cv2.addWeighted(overlay, 0.12, frame, 0.88, 0, frame)
            cv2.polylines(frame, [zone.points], True, zone.color, 2)

            # Label at first vertex
            x, y = zone.points[0]
            cv2.putText(frame, zone.name, (x + 5, y - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, zone.color, 2)
