# AerialGuard Drone Tracking System

AerialGuard is a lightweight computer-vision tracking core for drones. It performs centroid-based multi-object tracking and computes per-object movement statistics suitable for alerting, analytics, and downstream threat scoring.

## What is implemented now

The tracker now computes these statistics per tracked drone:

1. **Time in frame**
   - `time_in_frame_s = (last_seen_frame - first_seen_frame + 1) / fps`
2. **Flight path**
   - Ordered list of centroids (`trail`) over time
3. **Pixel speed**
   - `||c_t - c_(t-1)|| * fps` in pixels/second
4. **Approximate range from camera** (optional, requires intrinsics + assumed drone width)
   - `z ≈ (fx * W_real) / w_pixels`
5. **Approximate 3D camera-frame position** (optional)
   - `X = ((u - cx) * z) / fx`
   - `Y = ((v - cy) * z) / fy`
   - `Z = z`
6. **Approximate real-world speed** (optional)
   - `||P_t - P_(t-1)|| / Δt` in m/s
7. **Hover duration** (optional)
   - Speed below threshold while staying within a radius for at least minimum seconds
8. **Closest approach** (optional)
   - `min(Z_t)` over observed frames

> Notes:
> - Pixel-domain metrics always work.
> - Real-world (meter) metrics are approximations and depend on camera calibration quality and object size assumptions.

## Quick usage

```python
from src.tracker import CentroidTracker, CameraIntrinsics

tracker = CentroidTracker(
    fps=30.0,
    camera_intrinsics=CameraIntrinsics(fx=1200, fy=1200, cx=960, cy=540),
    assumed_drone_width_m=0.35,
)

# each frame
tracked = tracker.update([
    {"centroid": (810, 330), "bbox": (760, 300, 860, 360)},
])
```

Each object in `tracked` includes `bbox`, `centroid`, `trail`, and a `stats` dictionary with derived metrics.
