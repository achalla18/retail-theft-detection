"""
behavior_classifier.py — Behavior classification for AerialGuard.

Classifies each tracked object into one of several behavioral categories
based on motion analytics computed by FlightAnalytics.

Behaviors (priority order):
  circling      — Circular path around a point
  hover_near_asset — Sustained low-speed loiter
  perimeter_probing — Short repeated movements near boundary
  rapid_approach — Closing fast toward facility
  retreat        — Moving away rapidly
  stop_and_go    — Alternating movement and pause
  loitering      — Slow drift in area
  straight_transit — Clean transiting flight
  unknown        — Insufficient data
"""

import math
from typing import List, Tuple

TRANSIT     = "straight_transit"
HOVERING    = "hover_near_asset"
CIRCLING    = "circular_pathing"
PROBING     = "perimeter_probing"
APPROACH    = "rapid_approach"
RETREAT     = "retreat"
STOP_AND_GO = "stop_and_go"
LOITERING   = "loitering"
UNKNOWN     = "unknown"

BEHAVIOR_DISPLAY = {
    TRANSIT:     "Straight Transit",
    HOVERING:    "Hover Near Asset",
    CIRCLING:    "Circular Pathing",
    PROBING:     "Perimeter Probing",
    APPROACH:    "Rapid Approach",
    RETREAT:     "Retreat",
    STOP_AND_GO: "Stop & Go",
    LOITERING:   "Loitering",
    UNKNOWN:     "Unknown",
}

BEHAVIOR_SEVERITY = {
    TRANSIT:     "low",
    HOVERING:    "high",
    CIRCLING:    "high",
    PROBING:     "medium",
    APPROACH:    "critical",
    RETREAT:     "low",
    STOP_AND_GO: "medium",
    LOITERING:   "medium",
    UNKNOWN:     "low",
}


class BehaviorClassifier:
    """
    Classifies aerial object behavior from flight analytics + position history.

    Call classify() each frame; returns a behavior dict that can be merged into
    the main analytics result.
    """

    def classify(self, analytics: dict, position_history: list) -> dict:
        """
        Args:
            analytics:        analytics dict from FlightAnalytics.update()
            position_history: list of (timestamp, cx, cy) tuples

        Returns:
            {
              behavior_label:   str,
              behavior_display: str  (human-readable),
              behavior_severity:str  (low/medium/high/critical),
              hover_score:      float 0-1,
              circling_score:   float 0-1,
              probing_score:    float 0-1,
              approach_score:   float 0-1,
            }
        """
        hover_score    = self._hover_score(analytics)
        circling_score = self._circling_score(analytics)
        probing_score  = self._probing_score(analytics, position_history)
        approach_score, is_approach = self._approach_info(analytics, position_history)

        # Priority ordering
        if circling_score > 0.55:
            label = CIRCLING
        elif hover_score > 0.65:
            label = HOVERING
        elif probing_score > 0.50:
            label = PROBING
        elif approach_score > 0.55:
            label = APPROACH if is_approach else RETREAT
        elif (analytics.get("heading_volatility", 0) > 0.6
              and analytics.get("avg_speed", 0) > 0.3):
            label = STOP_AND_GO
        elif (analytics.get("avg_speed", 0) < 0.5
              and analytics.get("time_in_frame", 0) > 5):
            label = LOITERING
        elif analytics.get("path_straightness", 0.5) > 0.70:
            label = TRANSIT
        else:
            label = UNKNOWN

        return {
            "behavior_label":    label,
            "behavior_display":  BEHAVIOR_DISPLAY.get(label, label),
            "behavior_severity": BEHAVIOR_SEVERITY.get(label, "low"),
            "hover_score":       round(hover_score, 2),
            "circling_score":    round(circling_score, 2),
            "probing_score":     round(probing_score, 2),
            "approach_score":    round(approach_score, 2),
        }

    # ── Score helpers ─────────────────────────────────────────────────────────

    def _hover_score(self, a: dict) -> float:
        score = 0.0
        if a.get("hovering"):
            score += 0.50
            score += min(a.get("hover_duration", 0) / 30.0, 0.40)
        if a.get("speed", 0) < 0.5:
            score += 0.10
        return min(score, 1.0)

    def _circling_score(self, a: dict) -> float:
        score = 0.0
        if a.get("circling"):
            score += 0.55
        score += min(a.get("heading_volatility", 0) * 0.25, 0.25)
        if a.get("loiter_radius", 0) > 20:
            score += 0.20
        return min(score, 1.0)

    def _probing_score(self, a: dict, positions: list) -> float:
        """Detect repeated short reversals near a boundary."""
        if len(positions) < 10:
            return 0.0
        pts = [(p[1], p[2]) for p in positions[-30:]]
        reversals = 0
        for i in range(2, len(pts)):
            dx1 = pts[i - 1][0] - pts[i - 2][0]
            dx2 = pts[i][0]     - pts[i - 1][0]
            dy1 = pts[i - 1][1] - pts[i - 2][1]
            dy2 = pts[i][1]     - pts[i - 1][1]
            if (dx1 * dx2 + dy1 * dy2) < -40:
                reversals += 1
        return min(reversals / 5.0, 1.0)

    def _approach_info(
        self, a: dict, positions: list
    ) -> Tuple[float, bool]:
        """Return (approach_score, is_approaching_toward_center)."""
        score = min(a.get("approach_aggressiveness", 0.0), 1.0)
        is_approach = True
        if len(positions) >= 6:
            ref_x, ref_y = 320, 240  # proxy for facility center
            d_start = math.hypot(
                positions[-6][1] - ref_x, positions[-6][2] - ref_y
            )
            d_end = math.hypot(
                positions[-1][1] - ref_x, positions[-1][2] - ref_y
            )
            is_approach = d_end < d_start
        return score, is_approach
