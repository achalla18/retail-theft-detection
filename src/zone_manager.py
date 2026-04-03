"""
zone_manager.py — Define monitoring zones and track dwell time.

Each zone is a polygon on the video frame. When a tracked person's
centroid is inside a zone, their dwell time for that zone accumulates.
If dwell time exceeds a threshold, an alert is triggered.
"""

import cv2
import numpy as np
import time


class Zone:
    """A single monitoring region."""

    def __init__(self, name: str, points: list, color: tuple,
                 alert_seconds: float = 20):
        """
        Args:
            name: Label for this zone (e.g., "Electronics").
            points: List of [x, y] polygon vertices.
            color: BGR color tuple for display.
            alert_seconds: Dwell time (seconds) before triggering alert.
        """
        self.name = name
        self.points = np.array(points, dtype=np.int32)
        self.color = tuple(color)
        self.alert_seconds = alert_seconds

    def contains(self, point: tuple) -> bool:
        """Check if a (cx, cy) point is inside this zone's polygon."""
        result = cv2.pointPolygonTest(self.points, point, measureDist=False)
        return result >= 0


class ZoneManager:
    """Manages all zones and per-person dwell time tracking."""

    def __init__(self, zone_configs: list):
        """
        Args:
            zone_configs: List of zone dicts from settings.json.
        """
        self.zones = []
        for zc in zone_configs:
            self.zones.append(Zone(
                name=zc["name"],
                points=zc["points"],
                color=zc.get("color", [0, 255, 0]),
                alert_seconds=zc.get("dwell_alert_seconds", 20),
            ))

        # Track dwell time per person per zone
        # Structure: {person_id: {zone_name: {"enter_time": float, "total": float}}}
        self.dwell_data = {}

    def update(self, tracked_objects: dict) -> list[dict]:
        """
        Check each tracked person against all zones. Update dwell times.

        Args:
            tracked_objects: Output from CentroidTracker.update()

        Returns:
            List of alert dicts for anyone exceeding dwell thresholds.
        """
        now = time.time()
        alerts = []
        active_ids = set(tracked_objects.keys())

        # Clean up data for people who left the scene
        stale_ids = [pid for pid in self.dwell_data if pid not in active_ids]
        for pid in stale_ids:
            del self.dwell_data[pid]

        for person_id, obj in tracked_objects.items():
            centroid = obj["centroid"]

            if person_id not in self.dwell_data:
                self.dwell_data[person_id] = {}

            for zone in self.zones:
                zname = zone.name
                inside = zone.contains(centroid)

                if zname not in self.dwell_data[person_id]:
                    self.dwell_data[person_id][zname] = {
                        "enter_time": None,
                        "total": 0.0,
                        "alerted": False,
                    }

                zd = self.dwell_data[person_id][zname]

                if inside:
                    if zd["enter_time"] is None:
                        # Just entered the zone
                        zd["enter_time"] = now
                    else:
                        # Still in the zone — accumulate time
                        zd["total"] = now - zd["enter_time"]

                    # Check if dwell time exceeds threshold
                    if zd["total"] >= zone.alert_seconds and not zd["alerted"]:
                        zd["alerted"] = True
                        alerts.append({
                            "person_id": person_id,
                            "zone": zname,
                            "dwell_seconds": round(zd["total"], 1),
                            "threshold": zone.alert_seconds,
                            "centroid": centroid,
                        })
                else:
                    # Person left the zone — reset
                    zd["enter_time"] = None
                    zd["total"] = 0.0
                    zd["alerted"] = False

        return alerts

    def get_dwell_times(self, person_id: int) -> dict:
        """Get current dwell times for a specific person across all zones."""
        if person_id not in self.dwell_data:
            return {}
        result = {}
        for zname, zd in self.dwell_data[person_id].items():
            if zd["total"] > 0:
                result[zname] = round(zd["total"], 1)
        return result

    def get_zones_info(self) -> list[dict]:
        """Return zone metadata for the web dashboard."""
        result = []
        for zone in self.zones:
            b, g, r = zone.color
            result.append({
                "name": zone.name,
                "alert_seconds": zone.alert_seconds,
                "color_hex": f"#{r:02x}{g:02x}{b:02x}",
                "points": zone.points.tolist(),
            })
        return result

    def draw_zones(self, frame: np.ndarray):
        """Draw all zone polygons on the frame."""
        for zone in self.zones:
            # Semi-transparent fill
            overlay = frame.copy()
            cv2.fillPoly(overlay, [zone.points], zone.color)
            cv2.addWeighted(overlay, 0.15, frame, 0.85, 0, frame)

            # Zone border
            cv2.polylines(frame, [zone.points], True, zone.color, 2)

            # Zone label
            x, y = zone.points[0]
            cv2.putText(frame, zone.name, (x + 5, y - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, zone.color, 2)
