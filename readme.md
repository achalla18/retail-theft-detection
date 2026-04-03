# AerialGuard Drone Tracking System

AerialGuard is a lightweight, dependency-minimal drone tracking project.

It includes:
- A centroid-based multi-object tracker.
- Full per-track drone statistics (time in frame, path, pixel speed, range estimate, 3D position, 3D speed, hover duration, closest approach).
- A runnable CLI (`src/main.py`) for replaying detection frames.
- Unit tests validating key metrics and lifecycle behavior.

## Implemented statistics

For each object track:

1. **Time in frame**
   - `time_in_frame_s = (last_seen_frame - first_seen_frame + 1) / fps`
2. **Flight path**
   - Ordered centroids over time (`trail`)
3. **Pixel speed**
   - `||c_t - c_(t-1)|| * fps` in pixels/second
4. **Approximate range from camera** *(optional with intrinsics)*
   - `z ≈ (fx * W_real) / w_pixels`
5. **Approximate 3D camera-frame position** *(optional)*
   - `X = ((u - cx) * z) / fx`
   - `Y = ((v - cy) * z) / fy`
   - `Z = z`
6. **Approximate real-world speed** *(optional)*
   - `||P_t - P_(t-1)|| / Δt` in m/s
7. **Hover duration** *(optional)*
   - Speed below threshold and inside spatial radius for at least a minimum time
8. **Closest approach** *(optional)*
   - `min(Z_t)` while the track exists

## Project layout

- `src/tracker.py` — tracking + statistics core
- `src/main.py` — runnable CLI
- `tests/test_tracker.py` — unit tests
- `sample_detections.json` — sample input frames

## Quick start

```bash
python -m py_compile src/tracker.py src/main.py
python src/main.py --input-json sample_detections.json --fps 30
```

With intrinsics enabled:

```bash
python src/main.py \
  --input-json sample_detections.json \
  --fps 30 \
  --fx 1200 --fy 1200 --cx 640 --cy 360 \
  --assumed-width-m 0.35
```

## Run tests

```bash
python -m unittest tests/test_tracker.py -v
```

## Notes

- Pixel-domain statistics work without calibration.
- Meter-domain statistics are approximations and improve with calibrated camera intrinsics and realistic drone-size assumptions.
