"""
alert_manager.py — Alert rule engine for AerialGuard.

Three alert rules:
  zone_entry  — triggered when a track enters a zone with alert_on_entry=True
  hover       — triggered when a track has been hovering for >= threshold
  circling    — triggered when a circling pattern is detected

Each rule has an independent per-(track_id, rule) cooldown to prevent spam.
Valid alerts are written to a log file and the SQLite database.
"""

import logging
import time
from typing import Dict, List


class AlertManager:
    """
    Evaluates alert rules each frame and returns any newly-triggered alerts.

    An alert dict:
        {track_id, rule, zone (or None), timestamp, details: {…}}
    """

    def __init__(
        self,
        hover_threshold_s: float = 5.0,
        zone_cooldown_s:   float = 10.0,
        hover_cooldown_s:  float = 20.0,
        circle_cooldown_s: float = 30.0,
        log_file: str = "alerts.log",
    ):
        self.hover_threshold  = hover_threshold_s
        self._cooldowns: Dict[str, float] = {}  # key: "rule:track_id[:zone]"

        self._cd = {
            "zone_entry": zone_cooldown_s,
            "hover":      hover_cooldown_s,
            "circling":   circle_cooldown_s,
        }

        # In-memory display list (most-recent-first, max 10 entries)
        self._display: List[dict] = []

        # File logger
        self.logger = logging.getLogger("aerialguard.alerts")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            h = logging.FileHandler(log_file, encoding="utf-8")
            h.setFormatter(
                logging.Formatter("%(asctime)s | %(message)s", "%Y-%m-%d %H:%M:%S")
            )
            self.logger.addHandler(h)

        # DB integration (lazy)
        self._db_ok = False
        try:
            import database as _db
            _db.init_db()
            self._db_ok = True
        except Exception as exc:
            self.logger.warning(f"DB unavailable: {exc}")

    # ── Main evaluation ───────────────────────────────────────────────────────

    def process(
        self,
        analytics: Dict[int, dict],
        zone_events: List[dict],
    ) -> List[dict]:
        """
        Evaluate all rules for the current frame.

        Args:
            analytics:   {track_id: analytics_dict} from FlightAnalytics.update()
            zone_events: list of zone enter/exit events from ZoneManager.update()

        Returns:
            List of new alerts that passed cooldown.
        """
        now  = time.time()
        new: List[dict] = []

        # ── Rule 1: zone entry ────────────────────────────────────────
        for ev in zone_events:
            if ev["event"] != "enter" or not ev["alert_on_entry"]:
                continue
            tid  = ev["track_id"]
            zone = ev["zone"]
            key  = f"zone_entry:{tid}:{zone}"
            if self._on_cooldown(key, now, "zone_entry"):
                continue
            alert = {
                "track_id":  tid,
                "rule":      "zone_entry",
                "zone":      zone,
                "timestamp": now,
                "details":   {
                    "centroid": ev["centroid"],
                    "zone":     zone,
                },
            }
            new.append(self._record(alert, now))

        # ── Rule 2: hover ─────────────────────────────────────────────
        for tid, info in analytics.items():
            if not info.get("hovering"):
                continue
            if info.get("hover_duration", 0) < self.hover_threshold:
                continue
            key = f"hover:{tid}"
            if self._on_cooldown(key, now, "hover"):
                continue
            alert = {
                "track_id":  tid,
                "rule":      "hover",
                "zone":      info["current_zones"][0] if info["current_zones"] else None,
                "timestamp": now,
                "details":   {
                    "hover_duration": info["hover_duration"],
                    "altitude":       info["altitude_proxy"],
                    "centroid":       info["centroid"],
                },
            }
            new.append(self._record(alert, now))

        # ── Rule 3: circling ──────────────────────────────────────────
        for tid, info in analytics.items():
            if not info.get("circling"):
                continue
            key = f"circling:{tid}"
            if self._on_cooldown(key, now, "circling"):
                continue
            alert = {
                "track_id":  tid,
                "rule":      "circling",
                "zone":      info["current_zones"][0] if info["current_zones"] else None,
                "timestamp": now,
                "details":   {
                    "altitude": info["altitude_proxy"],
                    "centroid": info["centroid"],
                },
            }
            new.append(self._record(alert, now))

        # Prune stale cooldown entries
        self._cooldowns = {
            k: t for k, t in self._cooldowns.items() if now - t < 120
        }

        return new

    def get_display_alerts(self) -> List[dict]:
        """Return recent alerts (last 60 s) for overlay/dashboard."""
        now = time.time()
        self._display = [a for a in self._display if now - a["timestamp"] < 60]
        return list(reversed(self._display))

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _on_cooldown(self, key: str, now: float, rule: str) -> bool:
        last = self._cooldowns.get(key, 0)
        if now - last < self._cd.get(rule, 30):
            return True
        self._cooldowns[key] = now
        return False

    def _record(self, alert: dict, now: float) -> dict:
        rule = alert["rule"]
        tid  = alert["track_id"]
        zone = alert.get("zone") or "—"

        self.logger.info(
            f"ALERT | rule={rule} | track={tid} | zone={zone} | "
            f"details={alert['details']}"
        )

        if self._db_ok:
            try:
                import database as _db
                _db.insert_alert(alert)
            except Exception as exc:
                self.logger.warning(f"DB write failed: {exc}")

        self._display.append(alert)
        if len(self._display) > 10:
            self._display.pop(0)

        return alert
