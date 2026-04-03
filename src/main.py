"""
main.py — AerialGuard: AI Drone Intrusion Detection

Entry point that wires all subsystems into a real-time pipeline:
  detection → analytics → zone monitoring → alerts → incidents → dashboard

Usage
─────
  python src/main.py                       # webcam + web dashboard
  python src/main.py --source video.mp4    # video file
  python src/main.py --no-gui              # headless (web only)
  python src/main.py --port 8080           # custom web port
  python src/main.py --config config/settings.json
"""

import argparse
import json
import sys
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from analytics import FlightAnalytics
from detector import AerialDetector
from zone_manager import AirspaceZoneManager
from alert_manager import AlertManager
from incident_manager import IncidentManager


# ── Drawing helpers ───────────────────────────────────────────────────────────

def _id_color(track_id: int):
    hue = (track_id * 61) % 180
    return cv2.cvtColor(
        np.array([[[hue, 210, 255]]], dtype=np.uint8), cv2.COLOR_HSV2BGR
    )[0][0].tolist()


def draw_object(frame, info: dict, trail: deque, alert_active: bool):
    x1, y1, x2, y2 = info["bbox"]
    cx, cy = info["centroid"]
    color  = _id_color(info["track_id"])

    # Trail
    pts = list(trail)
    for i in range(1, len(pts)):
        a  = i / len(pts)
        t1 = (int(pts[i - 1][0]), int(pts[i - 1][1]))
        t2 = (int(pts[i][0]),     int(pts[i][1]))
        cv2.line(frame, t1, t2, color, max(1, int(a * 2)))

    # Box — thicker + red glow when alert active
    thick = 3 if alert_active else 2
    if alert_active:
        cv2.rectangle(frame, (x1 - 3, y1 - 3), (x2 + 3, y2 + 3), (0, 0, 200), 1)
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, thick)

    # Label
    label = f"T{info['track_id']}  {info['confidence']:.0%}"
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
    cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 6, y1), color, -1)
    cv2.putText(frame, label, (x1 + 3, y1 - 3),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1)

    # Stats below box
    y_off = y2 + 14
    cv2.putText(frame, f"{info['speed']:.1f} m/s",
                (x1, y_off), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 230, 255), 1)
    y_off += 14
    cv2.putText(frame, f"~{info['altitude_proxy']:.0f} m alt",
                (x1, y_off), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 230, 255), 1)

    # Status badges
    y_off += 14
    if info.get("hovering"):
        cv2.putText(frame, "[HOVER]", (x1, y_off),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 165, 255), 1)
        y_off += 13
    if info.get("circling"):
        cv2.putText(frame, "[CIRCLING]", (x1, y_off),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 165, 255), 1)
        y_off += 13
    for z in info.get("current_zones", []):
        cv2.putText(frame, f"[{z}]", (x1, y_off),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 60, 220), 1)
        y_off += 13


def draw_hud(frame, fps: float, obj_count: int, risk: int,
             active_alerts: list, panel_w: int = 290):
    """Semi-transparent HUD panel on the right edge."""
    h, w = frame.shape[:2]
    x0 = w - panel_w

    ov = frame.copy()
    cv2.rectangle(ov, (x0, 0), (w, h), (10, 18, 28), -1)
    cv2.addWeighted(ov, 0.80, frame, 0.20, 0, frame)

    # Brand
    cv2.putText(frame, "AERIALGUARD", (x0 + 10, 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 210, 140), 2)
    cv2.line(frame, (x0 + 10, 34), (w - 10, 34), (30, 50, 60), 1)

    # Risk score
    risk_color = (
        (0, 200, 80)   if risk < 30 else
        (0, 165, 255)  if risk < 60 else
        (0, 80, 220)
    )
    cv2.putText(frame, f"RISK  {risk:3d}/100", (x0 + 10, 58),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, risk_color, 1)
    bar_w = int((w - 20 - x0) * risk / 100)
    cv2.rectangle(frame, (x0 + 10, 64), (w - 10, 72), (30, 50, 60), -1)
    cv2.rectangle(frame, (x0 + 10, 64), (x0 + 10 + bar_w, 72), risk_color, -1)

    # Stats
    cv2.putText(frame, f"Objects: {obj_count}", (x0 + 10, 92),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 200, 210), 1)
    cv2.putText(frame, f"FPS:     {fps:.1f}",   (x0 + 10, 110),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 200, 210), 1)

    # Recent alerts
    cv2.putText(frame, "ALERTS", (x0 + 10, 136),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (60, 80, 220), 2)
    cv2.line(frame, (x0 + 10, 143), (w - 10, 143), (30, 50, 60), 1)

    if not active_alerts:
        cv2.putText(frame, "None", (x0 + 10, 162),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (60, 80, 80), 1)
    else:
        y = 162
        for a in active_alerts[:5]:
            age = int(time.time() - a["timestamp"])
            text = f"T{a['track_id']} {a['rule']} ({age}s)"
            cv2.putText(frame, text, (x0 + 10, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, (100, 130, 255), 1)
            y += 20


def draw_alert_flash(frame, alert: dict):
    h, w = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (w - 1, h - 1), (0, 0, 200), 4)
    msg = f"ALERT  T{alert['track_id']}  {alert['rule'].upper()}"
    if alert.get("zone"):
        msg += f"  [{alert['zone']}]"
    (tw, _), _ = cv2.getTextSize(msg, cv2.FONT_HERSHEY_SIMPLEX, 0.75, 2)
    cv2.putText(frame, msg, ((w - tw) // 2, 38),
                cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 220), 2)


# ── Pipeline ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AerialGuard Drone Intrusion Detection")
    parser.add_argument("--source",    default="0",
                        help="Webcam index or path to video file")
    parser.add_argument("--config",    default="config/settings.json")
    parser.add_argument("--no-gui",    action="store_true",
                        help="Disable OpenCV window (web dashboard only)")
    parser.add_argument("--port",      type=int, default=None,
                        help="Web dashboard port (overrides config)")
    args = parser.parse_args()

    # ── Config ────────────────────────────────────────────────────────
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"Config not found: {cfg_path}")
        sys.exit(1)
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)

    source = int(args.source) if args.source.isdigit() else args.source

    # ── Init components ───────────────────────────────────────────────
    print("Initializing AerialGuard...")

    det_cfg = cfg["detector"]
    try:
        detector = AerialDetector(
            model_name=det_cfg["model"],
            confidence=det_cfg["confidence_threshold"],
            iou_threshold=det_cfg.get("iou_threshold", 0.5),
            tracker=det_cfg.get("tracker", "bytetrack.yaml"),
            target_classes=det_cfg.get("target_classes"),
        )
        print(f"  Detector : {det_cfg['model']} + {det_cfg.get('tracker','bytetrack')}")
    except Exception as e:
        print(f"  ERROR loading detector: {e}")
        sys.exit(1)

    analytics = FlightAnalytics(
        fps=cfg.get("video", {}).get("fps", 30),
        calibration=cfg.get("analytics", {}).get("calibration"),
    )

    zone_mgr = AirspaceZoneManager(cfg["zones"])
    print(f"  Zones    : {len(cfg['zones'])} airspace zones")

    al_cfg = cfg.get("alerts", {})
    alert_mgr = AlertManager(
        hover_threshold_s=al_cfg.get("hover_threshold_seconds", 5),
        zone_cooldown_s=al_cfg.get("zone_entry_cooldown_seconds", 10),
        hover_cooldown_s=al_cfg.get("hover_cooldown_seconds", 20),
        circle_cooldown_s=al_cfg.get("circling_cooldown_seconds", 30),
        log_file=al_cfg.get("log_file", "alerts.log"),
    )

    # Open video to get actual frame size before init-ing incident manager
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"  ERROR: Cannot open video source '{source}'")
        sys.exit(1)

    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))  or 640
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    inc_mgr = IncidentManager(
        fps=src_fps,
        frame_size=(frame_w, frame_h),
        disappear_s=al_cfg.get("incident_timeout_seconds", 4),
        save_clips=al_cfg.get("clip_save_enabled", True),
    )

    # ── Web dashboard ─────────────────────────────────────────────────
    web_cfg  = cfg.get("web_server", {})
    web_on   = web_cfg.get("enabled", True)
    web_host = web_cfg.get("host", "0.0.0.0")
    web_port = args.port if args.port is not None else web_cfg.get("port", 5000)

    if web_on:
        import web_server
        web_server.start(host=web_host, port=web_port)
        web_server.shared_state.update_zones(zone_mgr.get_zones_info())
        # Wire incident lifecycle events → SSE push
        inc_mgr._event_cb = web_server.shared_state.push_event
        print(f"  Dashboard: http://localhost:{web_port}")

    # ── Display settings ──────────────────────────────────────────────
    disp_cfg  = cfg.get("display", {})
    show_gui  = not args.no_gui
    win_name  = disp_cfg.get("window_name", "AerialGuard")
    panel_w   = disp_cfg.get("hud_panel_width", 290)

    print("\nSystem running.")
    print("  Press Ctrl+C to stop." if not show_gui else
          "  Press 'q' in the video window to stop.\n")

    # ── Runtime state ─────────────────────────────────────────────────
    # Trail history per track_id
    trails: dict = {}          # {track_id: deque(maxlen=60)}
    TRAIL_LEN = 60

    fps          = 0.0
    frame_count  = 0
    fps_timer    = time.time()
    flash_until  = 0.0
    last_alert   = None

    # Track-point sampling (persist every N frames)
    TP_EVERY     = cfg.get("analytics", {}).get("track_point_sample_frames", 5)
    frame_n      = 0

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                if isinstance(source, str):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                break

            now    = time.time()
            frame_n += 1

            # ── 1. Detect + track ─────────────────────────────────────
            try:
                detections = detector.track(frame)
            except Exception as exc:
                print(f"  Detection error: {exc}")
                continue

            # ── 2. Analytics ──────────────────────────────────────────
            analytics_map = analytics.update(detections, now)

            # ── 3. Zone monitoring ────────────────────────────────────
            zone_events = zone_mgr.update(analytics_map, now)

            # Push zone membership back into analytics
            for tid, info in analytics_map.items():
                zones = zone_mgr.get_current_zones(tid)
                analytics.set_zones(tid, zones)
                info["current_zones"] = zones

            # ── 4. Alert rules ────────────────────────────────────────
            new_alerts = alert_mgr.process(analytics_map, zone_events)
            if new_alerts:
                flash_until = now + 2.0
                last_alert  = new_alerts[-1]
                for a in new_alerts:
                    print(f"  >> ALERT T{a['track_id']} rule={a['rule']}"
                          f" zone={a.get('zone','—')}")

            # ── 5. Incidents ──────────────────────────────────────────
            inc_mgr.update(analytics_map, new_alerts, frame, now)

            # ── 6. Persist track points (sampled) ─────────────────────
            if frame_n % TP_EVERY == 0:
                active_incs = {i["track_id"]: i["incident_id"]
                               for i in inc_mgr.get_active_incidents()}
                for tid, info in analytics_map.items():
                    x1, y1, x2, y2 = info["bbox"]
                    try:
                        from database import insert_track_point
                        insert_track_point(
                            incident_id=active_incs.get(tid, 0),
                            track_id=tid,
                            timestamp=now,
                            cx=info["centroid"][0],
                            cy=info["centroid"][1],
                            bbox_w=x2 - x1,
                            bbox_h=y2 - y1,
                            confidence=info["confidence"],
                            speed=info["speed"],
                            avg_speed=info["avg_speed"],
                            altitude_proxy=info["altitude_proxy"],
                            in_zones=info["current_zones"],
                        )
                    except Exception:
                        pass

            # ── 7. Update trails ──────────────────────────────────────
            active_tids = set(analytics_map.keys())
            for tid in list(trails):
                if tid not in active_tids:
                    del trails[tid]
            for tid, info in analytics_map.items():
                if tid not in trails:
                    trails[tid] = deque(maxlen=TRAIL_LEN)
                trails[tid].append(info["centroid"])

            # ── 8. Compute risk score ─────────────────────────────────
            risk = analytics.compute_risk_score(
                analytics_map, alert_mgr.get_display_alerts()
            )

            # ── 9. Draw overlays ──────────────────────────────────────
            zone_mgr.draw_zones(frame)

            alert_tids = {a["track_id"] for a in alert_mgr.get_display_alerts()}
            for tid, info in analytics_map.items():
                draw_object(frame, info, trails.get(tid, deque()),
                            alert_active=(tid in alert_tids))

            draw_hud(frame, fps, len(analytics_map), risk,
                     alert_mgr.get_display_alerts(), panel_w)

            if now < flash_until and last_alert:
                draw_alert_flash(frame, last_alert)

            # ── 10. FPS ───────────────────────────────────────────────
            frame_count += 1
            elapsed = now - fps_timer
            if elapsed >= 1.0:
                fps       = frame_count / elapsed
                frame_count = 0
                fps_timer = now

            # ── 11. Share with web dashboard ──────────────────────────
            if web_on:
                web_server.shared_state.update_frame(frame)
                web_server.shared_state.update_status(fps, len(analytics_map), risk)
                web_server.shared_state.update_objects(analytics_map)
                web_server.shared_state.update_zones(zone_mgr.get_zone_statuses())

                # Push alert events to SSE
                for a in new_alerts:
                    web_server.shared_state.push_event({"type": "alert", "data": a})

            # ── 12. Local GUI ─────────────────────────────────────────
            if show_gui:
                cv2.imshow(win_name, frame)
                if (cv2.waitKey(1) & 0xFF) == ord("q"):
                    break

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        cap.release()
        if show_gui:
            cv2.destroyAllWindows()
        print("AerialGuard stopped.")


if __name__ == "__main__":
    main()
