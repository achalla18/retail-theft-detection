"""
threat_scorer.py — Layered threat scoring for AerialGuard.

Produces a 0–100 numerical threat score for each active aerial object.
The score feeds the operator priority ranking and alert severity tagging.

Score breakdown (max 100 pts):
  Confidence           → 0–15   (detection certainty)
  Duration             → 0–20   (time near site)
  Hover behavior       → 0–15   (hover score contribution)
  Circling / probing   → 0–10   (orbital or probing behavior)
  Proximity            → 0–15   (closeness to frame center / facility)
  Path irregularity    → 0–10   (deviates from straight-line transit)
  Alert count          → 0–10   (rules already triggered for this track)
  After-hours bonus    → 0–5    (detection outside facility open hours)
  Uncertainty penalty  → −10–0  (noisy / low-confidence classification)
"""

import time
from typing import List

LEVEL_LOW      = "low"
LEVEL_MEDIUM   = "medium"
LEVEL_HIGH     = "high"
LEVEL_CRITICAL = "critical"

_LEVEL_PRIORITY = {
    LEVEL_LOW:      "monitor",
    LEVEL_MEDIUM:   "review",
    LEVEL_HIGH:     "escalate",
    LEVEL_CRITICAL: "immediate",
}

_LEVEL_COLOR = {
    LEVEL_LOW:      "#00e87a",
    LEVEL_MEDIUM:   "#ff8c00",
    LEVEL_HIGH:     "#ff2244",
    LEVEL_CRITICAL: "#cc00ff",
}


class ThreatScorer:
    """
    Stateless scorer — call score() each frame.

    Args:
        open_hour:   Hour (0–23) the facility opens  (default 6 = 06:00)
        close_hour:  Hour (0–23) the facility closes (default 22 = 22:00)
    """

    def __init__(self, open_hour: int = 6, close_hour: int = 22):
        self.open_hour  = open_hour
        self.close_hour = close_hour

    def score(
        self,
        analytics: dict,
        behavior: dict,
        recent_alerts: List[dict],
    ) -> dict:
        """
        Returns:
            {
              threat_score:      int   0–100
              threat_level:      str   low/medium/high/critical
              threat_color:      str   CSS colour for level
              confidence_band:   str   low/medium/high
              operator_priority: str   monitor/review/escalate/immediate
              score_breakdown:   dict  {component: points}
            }
        """
        bd: dict = {}

        # 1. Confidence (0–15)
        conf = analytics.get("confidence", 0.5)
        bd["confidence"] = round(min(conf * 15.0, 15.0))

        # 2. Duration (0–20) — maxes at 60 s presence
        tif = analytics.get("time_in_frame", 0.0)
        bd["duration"] = round(min((tif / 60.0) * 20.0, 20.0))

        # 3. Hover behavior (0–15)
        bd["hover"] = round(behavior.get("hover_score", 0.0) * 15.0)

        # 4. Circling / probing (0–10)
        circ_probe = max(
            behavior.get("circling_score", 0.0),
            behavior.get("probing_score",  0.0),
        )
        bd["circling_probing"] = round(circ_probe * 10.0)

        # 5. Proximity (0–15) — closest_approach in pixels; 0 px = 15 pts
        closest = analytics.get("closest_approach", 500.0)
        bd["proximity"] = round(max(0.0, 15.0 - (closest / 40.0)))

        # 6. Path irregularity (0–10)
        bd["irregularity"] = round(
            (1.0 - analytics.get("path_straightness", 1.0)) * 10.0
        )

        # 7. Alert count bonus (0–10)
        tid       = analytics.get("track_id")
        my_alerts = [a for a in recent_alerts if a.get("track_id") == tid]
        bd["alerts"] = round(min(len(my_alerts) * 2.0, 10.0))

        # 8. After-hours bonus (0–5)
        h = time.localtime().tm_hour
        bd["after_hours"] = 5 if (h < self.open_hour or h >= self.close_hour) else 0

        # 9. Uncertainty penalty (−10 to 0)
        bd["uncertainty_penalty"] = round(
            -(analytics.get("uncertainty_score", 0.0) * 10.0)
        )

        total = max(0, min(100, round(sum(bd.values()))))

        # Threat level
        if total >= 75:
            level = LEVEL_CRITICAL
        elif total >= 50:
            level = LEVEL_HIGH
        elif total >= 25:
            level = LEVEL_MEDIUM
        else:
            level = LEVEL_LOW

        # Confidence band
        if conf > 0.75 and tif > 3.0:
            conf_band = "high"
        elif conf > 0.50 or tif > 1.0:
            conf_band = "medium"
        else:
            conf_band = "low"

        return {
            "threat_score":      total,
            "threat_level":      level,
            "threat_color":      _LEVEL_COLOR[level],
            "confidence_band":   conf_band,
            "operator_priority": _LEVEL_PRIORITY[level],
            "score_breakdown":   bd,
        }
