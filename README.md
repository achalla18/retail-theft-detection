# AerialGuard — AI Drone Intrusion Detection & Facility Monitoring

An AI-powered computer vision system that watches outdoor camera feeds around a facility, detects and tracks aerial objects (drones, UAVs), computes flight analytics, and raises alerts when an object enters a restricted airspace zone or exhibits suspicious behaviour such as hovering or circling.

---

## Features

| Capability | Details |
|---|---|
| **Aerial object detection** | YOLOv8 in `track()` mode — swap `yolov8n.pt` for a VisDrone-trained model for best accuracy |
| **Persistent tracking** | BoT-SORT or ByteTrack via Ultralytics — stable track IDs across frames |
| **Flight analytics** | Speed (m/s), altitude proxy, time in frame, entry/exit points |
| **Hover detection** | Flags objects loitering below speed threshold for ≥ N seconds |
| **Circling detection** | Measures angular displacement to flag patrol-pattern manoeuvres |
| **Airspace zones** | Polygon regions; breaches trigger instant alerts |
| **Incident lifecycle** | Each track becomes an incident with annotated MP4 clip + JPEG thumbnail |
| **Alert rules** | `zone_entry`, `hover`, `circling` — independent per-track cooldowns |
| **0–100 Risk score** | Composite of object count, zone breaches, hover, and circling activity |
| **Web dashboard** | 3-page browser UI: Live Monitoring, Incident Review, Flight Analytics |
| **SQLite persistence** | Incidents, track-point time series, and alerts survive restarts |

---

## Quick Start

```bash
# 1. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run with webcam (opens web dashboard at http://localhost:5000)
python src/main.py

# 4. Run with a video file
python src/main.py --source path/to/video.mp4

# 5. Headless mode (no OpenCV window — web only)
python src/main.py --no-gui

# 6. Custom web port
python src/main.py --port 8080
```

---

## Dashboard Pages

### Page 1 — Live Monitoring
- Annotated live video feed (bounding boxes, track IDs, trail, speed, altitude)
- Dynamic risk score badge (green / orange / red)
- Zone status cards showing CLEAR or BREACH per zone
- Active intrusions panel (hovering, circling, zone-breached tracks)
- Per-object stat row (speed, altitude, flags)

### Page 2 — Incident Review
- Card grid of all recorded incidents with thumbnail, duration, max speed, triggered rules
- Click any card to open the detail view:
  - Video clip replay
  - Trajectory scatter chart (full flight path)
  - Stats table (speed, duration, zones, entry/exit points)
  - Alert timeline
  - Auto-generated plain-English summary

### Page 3 — Flight Analytics
- Speed vs Time, Altitude vs Time, Confidence vs Time, Object Count vs Time (Chart.js)
- Zone transitions timeline
- Configurable time window (60 s / 5 min / 30 min)

---

## Project Structure

```
AerialGuard/
├── src/
│   ├── main.py              # Pipeline entry point
│   ├── detector.py          # YOLO track() wrapper (BoT-SORT / ByteTrack)
│   ├── analytics.py         # Speed, altitude, hover, circling, risk score
│   ├── zone_manager.py      # Airspace zones + dwell tracking
│   ├── alert_manager.py     # Alert rules with cooldowns
│   ├── incident_manager.py  # Incident lifecycle, clip recording, summaries
│   ├── database.py          # SQLite: incidents, track_points, alerts
│   └── web_server.py        # Flask: MJPEG stream, SSE, REST API
├── templates/
│   └── index.html           # 3-page single-page app
├── static/
│   ├── css/style.css        # Dark military/radar theme
│   └── js/app.js            # Real-time dashboard JS (Chart.js)
├── config/
│   └── settings.json        # All tunable parameters
├── data/                    # Created at runtime
│   ├── surveillance.db      # SQLite database
│   └── clips/               # Incident MP4 clips + JPEG thumbnails
├── requirements.txt
└── README.md
```

---

## Configuration (`config/settings.json`)

```jsonc
{
  "detector": {
    "model": "yolov8n.pt",           // swap for custom drone model
    "confidence_threshold": 0.35,
    "tracker": "bytetrack.yaml"       // or "botsort.yaml"
  },
  "analytics": {
    "calibration": {
      "pixels_per_meter": 80,         // tune for your camera
      "reference_bbox_height": 40,    // bbox height (px) at reference altitude
      "reference_altitude_m": 30,     // altitude (m) at reference_bbox_height
      "hover_speed_mps": 0.8,
      "hover_confirm_s": 3.0,
      "circle_window_s": 6.0,
      "circle_min_rad": 1.8           // radians of angular displacement
    }
  },
  "zones": [
    {
      "name": "Restricted Airspace",
      "color": [0, 0, 220],           // BGR
      "points": [[100,150],[500,150],[500,450],[100,450]],
      "alert_on_entry": true
    }
  ],
  "alerts": {
    "hover_threshold_seconds": 5,
    "clip_save_enabled": true
  },
  "web_server": { "port": 5000 }
}
```

### Tuning zone coordinates

Run with `--calibrate` (not yet exposed; use the browser dashboard to visually position zones, then update `settings.json`). Zone `points` are `[x, y]` pixel coordinates in the camera frame.

### Using a custom drone model

Download or train a model on the [VisDrone dataset](https://github.com/VisDrone/VisDrone-Dataset), then set `"model": "path/to/drone_yolov8.pt"` in `settings.json`.

---

## Alert Rules

| Rule | Trigger | Cooldown |
|---|---|---|
| `zone_entry` | Track centroid enters a zone with `alert_on_entry: true` | 10 s |
| `hover` | Track speed < 0.8 m/s for ≥ 5 s | 20 s |
| `circling` | Angular displacement ≥ 1.8 rad in a 6-second window | 30 s |

All cooldowns are configurable in `settings.json`.

---

## Dependencies

```
ultralytics>=8.0.0   # YOLOv8 + BoT-SORT/ByteTrack
opencv-python>=4.8.0
numpy>=1.24.0
flask>=3.0.0
```

---

## Ethical & Legal Notice

This system is intended for **authorised facility protection** only.
Monitoring airspace without proper authorisation may be subject to local aviation, privacy, and surveillance laws.
Always obtain necessary permits before deployment.
The system detects objects by motion and size — it does not identify individuals or store biometric data.
