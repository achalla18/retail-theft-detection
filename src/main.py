"""
main.py — Retail Anomaly Detection System

Entry point that ties together detection, tracking, zone monitoring,
alert management, and the web dashboard into a real-time pipeline.

Usage:
    python src/main.py                        # webcam + web dashboard
    python src/main.py --source video.mp4     # video file
    python src/main.py --no-gui               # headless (web only)
    python src/main.py --port 8080            # custom web port
    python src/main.py --calibrate            # zone coordinate helper
    python src/main.py --config config/settings.json
"""

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from detector import PersonDetector
from tracker import CentroidTracker
from zone_manager import ZoneManager
from alert_manager import AlertManager


# ── Drawing helpers ───────────────────────────────────────────────────────────

def draw_tracked_person(frame, person_id, obj_data, dwell_times,
                        show_trails=True, trail_length=40):
    """Draw bounding box, ID label, trail, and dwell info for one person."""
    x1, y1, x2, y2 = obj_data["bbox"]

    # Consistent HSV-derived color per ID
    hue = (person_id * 47) % 180
    color_bgr = cv2.cvtColor(
        np.array([[[hue, 200, 255]]], dtype=np.uint8), cv2.COLOR_HSV2BGR
    )[0][0].tolist()

    # Bounding box
    cv2.rectangle(frame, (x1, y1), (x2, y2), color_bgr, 2)

    # ID label
    label = f"Person #{person_id}"
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    cv2.rectangle(frame, (x1, y1 - th - 8), (x1 + tw + 6, y1), color_bgr, -1)
    cv2.putText(frame, label, (x1 + 3, y1 - 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

    # Dwell time annotations
    y_off = y2 + 18
    for zone_name, seconds in dwell_times.items():
        dwell_color = (0, 0, 255) if seconds > 10 else (200, 200, 200)
        cv2.putText(frame, f"{zone_name}: {seconds}s", (x1, y_off),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, dwell_color, 1)
        y_off += 16

    # Movement trail
    if show_trails and len(obj_data["trail"]) > 1:
        trail = obj_data["trail"][-trail_length:]
        for i in range(1, len(trail)):
            alpha = i / len(trail)
            pt1 = tuple(int(v) for v in trail[i - 1])
            pt2 = tuple(int(v) for v in trail[i])
            cv2.line(frame, pt1, pt2, color_bgr, max(1, int(alpha * 3)))


def draw_info_panel(frame, tracked_count, fps, alerts, panel_width=280):
    """Draw semi-transparent info panel on the right side of the frame."""
    h, w = frame.shape[:2]
    x0 = w - panel_width

    overlay = frame.copy()
    cv2.rectangle(overlay, (x0, 0), (w, h), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.78, frame, 0.22, 0, frame)

    cv2.putText(frame, "ANOMALY DETECTION", (x0 + 10, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 220, 180), 2)
    cv2.line(frame, (x0 + 10, 36), (w - 10, 36), (60, 60, 60), 1)

    cv2.putText(frame, f"People:  {tracked_count}", (x0 + 10, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.48, (190, 190, 190), 1)
    cv2.putText(frame, f"FPS:     {fps:.1f}", (x0 + 10, 80),
                cv2.FONT_HERSHEY_SIMPLEX, 0.48, (190, 190, 190), 1)

    cv2.putText(frame, "RECENT ALERTS", (x0 + 10, 112),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 80, 255), 2)
    cv2.line(frame, (x0 + 10, 120), (w - 10, 120), (60, 60, 60), 1)

    if not alerts:
        cv2.putText(frame, "No alerts", (x0 + 10, 144),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (80, 80, 80), 1)
    else:
        y = 144
        for alert in alerts[:5]:
            age = time.time() - alert["timestamp"]
            cv2.putText(frame, f"#{alert['person_id']} in {alert['zone']}",
                        (x0 + 10, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (60, 120, 255), 1)
            cv2.putText(frame, f"  {alert['dwell_seconds']}s  ({age:.0f}s ago)",
                        (x0 + 10, y + 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (120, 120, 120), 1)
            y += 38


def draw_flash_alert(frame, alert):
    """Flash a red border + banner when a new alert fires."""
    h, w = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (w - 1, h - 1), (0, 0, 220), 4)
    text = f"ALERT: Person #{alert['person_id']} in {alert['zone']} ({alert['dwell_seconds']}s)"
    (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
    cv2.putText(frame, text, ((w - tw) // 2, 34),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 220), 2)


# ── Zone calibration helper ───────────────────────────────────────────────────

def run_zone_calibration(source):
    """Interactive mode: click the video to print zone corner coordinates."""
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print("Error: Cannot open video source.")
        return

    ret, frame = cap.read()
    if not ret:
        print("Error: Cannot read from video source.")
        cap.release()
        return

    points: list = []

    def on_click(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            points.append([x, y])
            print(f"  Point {len(points)}: [{x}, {y}]")

    win = "Zone Calibration — click corners, 'r' reset, 'q' quit"
    cv2.namedWindow(win)
    cv2.setMouseCallback(win, on_click)

    print("\n=== ZONE CALIBRATION MODE ===")
    print("Click corners on the video frame to record coordinates.")
    print("Paste the printed points into config/settings.json.\n")

    while True:
        display = frame.copy()
        for i, pt in enumerate(points):
            cv2.circle(display, tuple(pt), 5, (0, 255, 0), -1)
            cv2.putText(display, f"({pt[0]},{pt[1]})", (pt[0] + 8, pt[1] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
            if i > 0:
                cv2.line(display, tuple(points[i - 1]), tuple(pt), (0, 255, 0), 2)
        if len(points) > 2:
            cv2.line(display, tuple(points[-1]), tuple(points[0]), (0, 255, 0), 1)
        cv2.putText(display, f"Points: {len(points)} | r=reset  q=quit",
                    (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.imshow(win, display)
        key = cv2.waitKey(30) & 0xFF
        if key == ord("q"):
            break
        if key == ord("r"):
            points.clear()
            print("  Points reset.")

    if points:
        print(f"\nFinal points:\n  {points}")
        print("Copy into config/settings.json  →  zones[*].points\n")

    cap.release()
    cv2.destroyAllWindows()


# ── Main pipeline ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Retail Anomaly Detection System")
    parser.add_argument("--source", default="0",
                        help="Webcam index (0) or path to video file")
    parser.add_argument("--config", default="config/settings.json",
                        help="Path to settings JSON")
    parser.add_argument("--calibrate", action="store_true",
                        help="Run interactive zone calibration mode")
    parser.add_argument("--no-gui", action="store_true",
                        help="Disable OpenCV window (web dashboard only)")
    parser.add_argument("--port", type=int, default=None,
                        help="Web dashboard port (overrides config)")
    args = parser.parse_args()

    # ── Load config ───────────────────────────────────────────────────
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: config not found at {config_path}")
        sys.exit(1)

    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    source = int(args.source) if args.source.isdigit() else args.source

    if args.calibrate:
        run_zone_calibration(source)
        return

    # ── Initialize components ─────────────────────────────────────────
    print("Initializing Retail Anomaly Detection System...")
    print(f"  Source : {source}")

    try:
        detector = PersonDetector(
            model_name=config["detector"]["model"],
            confidence=config["detector"]["confidence_threshold"],
        )
        print("  Detector : YOLOv8 loaded")
    except Exception as exc:
        print(f"  Error loading detector: {exc}")
        sys.exit(1)

    tracker = CentroidTracker(
        max_disappeared=config["tracker"]["max_disappeared_frames"],
        max_distance=config["tracker"]["max_match_distance"],
    )

    zone_mgr = ZoneManager(config["zones"])
    print(f"  Zones    : {len(config['zones'])} configured")

    alert_mgr = AlertManager(
        cooldown_seconds=config["alerts"]["cooldown_seconds"],
        log_file=config["alerts"]["log_file"],
    )

    display_cfg = config.get("display", {})
    show_trails = display_cfg.get("show_trails", True)
    trail_length = display_cfg.get("trail_length", 40)
    panel_width = display_cfg.get("info_panel_width", 280)
    window_name = display_cfg.get("window_name", "Retail Anomaly Detection")
    show_gui = not args.no_gui

    # ── Web dashboard ─────────────────────────────────────────────────
    web_cfg = config.get("web_server", {})
    web_enabled = web_cfg.get("enabled", True)
    web_host = web_cfg.get("host", "0.0.0.0")
    web_port = args.port if args.port is not None else web_cfg.get("port", 5000)

    if web_enabled:
        import web_server
        web_server.start(host=web_host, port=web_port)
        # Populate zone info for the web API
        web_server.shared_state.update_zones(zone_mgr.get_zones_info())
        print(f"  Dashboard: http://localhost:{web_port}")

    # ── Open video source ─────────────────────────────────────────────
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"Error: Cannot open video source '{source}'")
        sys.exit(1)

    print("\nSystem running.")
    if show_gui:
        print("  Press 'q' in the video window to stop.")
    else:
        print("  Press Ctrl+C to stop.\n")

    fps = 0.0
    frame_count = 0
    fps_start = time.time()
    flash_until = 0.0
    last_flash_alert = None

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                if isinstance(source, str):   # loop video files
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                break

            # 1. Detect
            try:
                detections = detector.detect(frame)
            except Exception as exc:
                print(f"Detection error: {exc}")
                continue

            # 2. Track
            tracked = tracker.update(detections)

            # 3. Zone dwell
            zone_alerts = zone_mgr.update(tracked)

            # 4. Alerts
            new_alerts = alert_mgr.process_alerts(zone_alerts)
            if new_alerts:
                flash_until = time.time() + 2.0
                last_flash_alert = new_alerts[-1]
                for a in new_alerts:
                    print(f"  >> ALERT: Person #{a['person_id']} in "
                          f"{a['zone']} for {a['dwell_seconds']}s")

                # Broadcast to web dashboard
                if web_enabled:
                    for a in new_alerts:
                        web_server.shared_state.push_event(
                            {"type": "alert", "data": a}
                        )

            # ── Draw overlays ─────────────────────────────────────────
            zone_mgr.draw_zones(frame)

            for pid, obj_data in tracked.items():
                draw_tracked_person(
                    frame, pid, obj_data,
                    zone_mgr.get_dwell_times(pid),
                    show_trails=show_trails,
                    trail_length=trail_length,
                )

            draw_info_panel(frame, len(tracked), fps,
                            alert_mgr.get_display_alerts(), panel_width)

            if time.time() < flash_until and last_flash_alert:
                draw_flash_alert(frame, last_flash_alert)

            # ── FPS ───────────────────────────────────────────────────
            frame_count += 1
            elapsed = time.time() - fps_start
            if elapsed >= 1.0:
                fps = frame_count / elapsed
                frame_count = 0
                fps_start = time.time()

            # ── Share annotated frame with web dashboard ──────────────
            if web_enabled:
                web_server.shared_state.update_stats(fps, len(tracked))
                web_server.shared_state.update_frame(frame)

            # ── Local GUI ─────────────────────────────────────────────
            if show_gui:
                cv2.imshow(window_name, frame)
                if (cv2.waitKey(1) & 0xFF) == ord("q"):
                    break

    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        cap.release()
        if show_gui:
            cv2.destroyAllWindows()
        print("System stopped.")


if __name__ == "__main__":
    main()
