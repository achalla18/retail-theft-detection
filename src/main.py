from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tracker import CameraIntrinsics, CentroidTracker


def load_frames(path: Path) -> list[list[dict[str, Any]]]:
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError("Input file must contain a JSON list of frames.")
    for frame in data:
        if not isinstance(frame, list):
            raise ValueError("Each frame must be a list of detections.")
    return data


def default_demo_frames() -> list[list[dict[str, Any]]]:
    return [
        [{"centroid": [640, 360], "bbox": [610, 330, 670, 390]}],
        [{"centroid": [646, 360], "bbox": [616, 330, 676, 390]}],
        [{"centroid": [652, 361], "bbox": [622, 331, 682, 391]}],
        [{"centroid": [658, 361], "bbox": [628, 331, 688, 391]}],
        [],
        [],
        [],
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AerialGuard drone stats tracker runner")
    parser.add_argument("--input-json", type=Path, help="JSON file with frames of detections")
    parser.add_argument("--fps", type=float, default=30.0, help="Frame rate")
    parser.add_argument("--max-disappeared", type=int, default=2)
    parser.add_argument("--max-distance", type=float, default=100.0)

    parser.add_argument("--fx", type=float)
    parser.add_argument("--fy", type=float)
    parser.add_argument("--cx", type=float)
    parser.add_argument("--cy", type=float)
    parser.add_argument("--assumed-width-m", type=float, default=0.35)

    parser.add_argument("--hover-speed-threshold", type=float, default=1.0)
    parser.add_argument("--hover-radius", type=float, default=2.0)
    parser.add_argument("--hover-min-seconds", type=float, default=4.0)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    intrinsics = None
    if all(v is not None for v in (args.fx, args.fy, args.cx, args.cy)):
        intrinsics = CameraIntrinsics(fx=args.fx, fy=args.fy, cx=args.cx, cy=args.cy)

    tracker = CentroidTracker(
        max_disappeared=args.max_disappeared,
        max_distance=args.max_distance,
        fps=args.fps,
        camera_intrinsics=intrinsics,
        assumed_drone_width_m=args.assumed_width_m,
        hover_speed_threshold_mps=args.hover_speed_threshold,
        hover_radius_m=args.hover_radius,
        hover_min_seconds=args.hover_min_seconds,
    )

    frames = load_frames(args.input_json) if args.input_json else default_demo_frames()

    for frame_idx, detections in enumerate(frames):
        active = tracker.update(detections)
        print(f"frame={frame_idx} active_track_ids={list(active.keys())}")
        for track_id, track in active.items():
            stats = track["stats"]
            print(
                f"  id={track_id} time={stats['time_in_frame_s']:.2f}s "
                f"px_speed={stats['pixel_speed_current_px_s']:.2f} "
                f"closest_m={stats['closest_distance_m']}"
            )

    print("\ncompleted_tracks=")
    print(json.dumps(tracker.get_completed_tracks(), indent=2))


if __name__ == "__main__":
    main()
