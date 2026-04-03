"""
alert_manager.py — Handle alerts with cooldowns, logging, and persistence.

Prevents alert spam by enforcing a cooldown period per person per zone.
Logs all alerts to a file, persists them to SQLite, and maintains a list
of recent alerts for on-screen display.
"""

import logging
import time
from pathlib import Path


class AlertManager:
    def __init__(self, cooldown_seconds: float = 30, log_file: str = "alerts.log"):
        """
        Args:
            cooldown_seconds: Minimum time between repeat alerts for the
                              same person in the same zone.
            log_file: Path to write alert logs.
        """
        self.cooldown = cooldown_seconds
        self.recent_alerts: list[dict] = []
        self.max_display = 5

        # Track last alert time per (person_id, zone) to prevent spam
        self.last_alert_time: dict = {}

        # Set up file logging
        self.logger = logging.getLogger("alerts")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.FileHandler(log_file, encoding="utf-8")
            handler.setFormatter(
                logging.Formatter("%(asctime)s | %(message)s", "%Y-%m-%d %H:%M:%S")
            )
            self.logger.addHandler(handler)

        # Lazy DB import — works even if database.py has no SQLite yet
        self._db_ok = False
        try:
            import database as _db  # noqa: F401
            _db.init_db()
            self._db_ok = True
        except Exception as exc:
            self.logger.warning(f"Database unavailable, alerts will not be persisted: {exc}")

    def process_alerts(self, zone_alerts: list[dict]) -> list[dict]:
        """
        Filter alerts through cooldown, log and persist valid ones.

        Args:
            zone_alerts: Raw alerts from ZoneManager.update()

        Returns:
            List of new alerts that passed the cooldown filter.
        """
        now = time.time()
        new_alerts: list[dict] = []

        for alert in zone_alerts:
            key = (alert["person_id"], alert["zone"])

            if key in self.last_alert_time:
                if now - self.last_alert_time[key] < self.cooldown:
                    continue

            self.last_alert_time[key] = now
            alert["timestamp"] = now

            # File log
            self.logger.info(
                f"ALERT | Person #{alert['person_id']} | "
                f"Zone: {alert['zone']} | "
                f"Dwell: {alert['dwell_seconds']}s "
                f"(threshold: {alert['threshold']}s)"
            )

            # Database persistence
            if self._db_ok:
                try:
                    import database as _db
                    _db.insert_alert(alert)
                except Exception as exc:
                    self.logger.warning(f"DB write failed: {exc}")

            self.recent_alerts.append(alert)
            if len(self.recent_alerts) > self.max_display:
                self.recent_alerts.pop(0)

            new_alerts.append(alert)

        # Prune stale cooldown entries (older than 2 minutes)
        stale = [k for k, t in self.last_alert_time.items() if now - t > 120]
        for k in stale:
            del self.last_alert_time[k]

        return new_alerts

    def get_display_alerts(self) -> list[dict]:
        """Get recent alerts for on-screen overlay (most recent first)."""
        now = time.time()
        self.recent_alerts = [
            a for a in self.recent_alerts if now - a["timestamp"] < 60
        ]
        return list(reversed(self.recent_alerts))
