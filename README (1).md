# Retail Anomaly Detection System

An AI-powered prototype that uses computer vision to detect anomalous customer behavior in retail environments. Uses YOLOv8 for person detection, centroid-based tracking, and zone-based dwell time analysis.

## Features
- **Real-time person detection** via YOLOv8
- **Multi-person tracking** with unique IDs
- **Zone monitoring** — define regions of interest (e.g., high-value merchandise areas)
- **Dwell time alerts** — flags when someone lingers unusually long in a zone
- **Movement pattern tracking** — logs paths for behavioral analysis
- **Visual dashboard overlay** — real-time bounding boxes, zones, timers, and alerts

## Setup

```bash
# 1. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run with webcam
python src/main.py

# 4. Run with a video file
python src/main.py --source path/to/video.mp4
```

## Configuration
Edit `config/settings.json` to customize:
- Detection confidence threshold
- Dwell time alert threshold (seconds)
- Zone definitions (coordinates)
- Tracking parameters

## Project Structure
```
retail-surveillance/
├── src/
│   ├── main.py              # Entry point — video loop + display
│   ├── detector.py           # YOLOv8 person detection
│   ├── tracker.py            # Centroid-based multi-person tracker
│   ├── zone_manager.py       # Zone definitions + dwell time logic
│   └── alert_manager.py      # Alert generation + logging
├── config/
│   └── settings.json         # All configurable parameters
├── requirements.txt
└── README.md
```

## Ethical Note
This system uses **anomaly detection** (statistical outliers from normal behavior), not profiling. It tracks *behavior patterns* (dwell time, movement), never identity or demographics. Any production deployment should include bias auditing and transparency measures.
