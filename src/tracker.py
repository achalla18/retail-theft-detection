from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import math


@dataclass
class CameraIntrinsics:
    """Minimal pinhole-camera intrinsics in pixel units."""

    fx: float
    fy: float
    cx: float
    cy: float


class CentroidTracker:
    """
    Centroid-based multi-object tracker with per-object drone statistics.

    Each tracked object maintains:
    - time in frame
    - flight path (centroid history)
    - pixel speed history (px/s)
    - optional approximate range/3D position/real-world speed if intrinsics are provided
    - hover duration estimate
    - closest camera approach estimate
    """

    def __init__(
        self,
        max_disappeared: int = 30,
        max_distance: float = 80,
        fps: float = 30.0,
        camera_intrinsics: CameraIntrinsics | None = None,
        assumed_drone_width_m: float = 0.35,
        hover_speed_threshold_mps: float = 1.0,
        hover_radius_m: float = 2.0,
        hover_min_seconds: float = 4.0,
    ):
        self.next_id = 0
        self.max_disappeared = max_disappeared
        self.max_distance = max_distance
        self.fps = fps

        # Calibration and approximation parameters.
        self.camera_intrinsics = camera_intrinsics
        self.assumed_drone_width_m = assumed_drone_width_m

        # Hover configuration.
        self.hover_speed_threshold_mps = hover_speed_threshold_mps
        self.hover_radius_m = hover_radius_m
        self.hover_min_seconds = hover_min_seconds

        # Core tracker state.
        self.objects: OrderedDict[int, tuple[float, float]] = OrderedDict()
        self.disappeared: OrderedDict[int, int] = OrderedDict()
        self.bboxes: OrderedDict[int, tuple[float, float, float, float]] = OrderedDict()
        self.trails: OrderedDict[int, list[tuple[float, float]]] = OrderedDict()

        # Frame bookkeeping.
        self.frame_index = -1

        # Per-object statistics state.
        self.stats: OrderedDict[int, dict[str, Any]] = OrderedDict()

    def _register(self, centroid: tuple[float, float], bbox: tuple[float, float, float, float]):
        obj_id = self.next_id
        self.objects[obj_id] = centroid
        self.disappeared[obj_id] = 0
        self.bboxes[obj_id] = bbox
        self.trails[obj_id] = [centroid]

        self.stats[obj_id] = {
            "first_seen_frame": self.frame_index,
            "last_seen_frame": self.frame_index,
            "first_seen_time": self.frame_index / self.fps,
            "last_seen_time": self.frame_index / self.fps,
            "pixel_speed_history": [],
            "distance_history_m": [],
            "position_3d_history_m": [],
            "speed_3d_history_mps": [],
            "closest_distance_m": None,
            "hover_segments": [],
            "active_hover_start_frame": None,
            "active_hover_anchor": None,
        }

        self.next_id += 1
        return obj_id

    def _finalize_hover_if_active(self, obj_id: int):
        stat = self.stats[obj_id]
        start = stat["active_hover_start_frame"]
        if start is None:
            return

        duration_s = (self.frame_index - start + 1) / self.fps
        if duration_s >= self.hover_min_seconds:
            stat["hover_segments"].append(
                {
                    "start_frame": start,
                    "end_frame": self.frame_index,
                    "duration_s": duration_s,
                }
            )

        stat["active_hover_start_frame"] = None
        stat["active_hover_anchor"] = None

    def _deregister(self, obj_id: int):
        self._finalize_hover_if_active(obj_id)
        del self.objects[obj_id]
        del self.disappeared[obj_id]
        del self.bboxes[obj_id]
        del self.trails[obj_id]
        del self.stats[obj_id]

    def _bbox_width(self, bbox: tuple[float, float, float, float]) -> float:
        x1, _, x2, _ = bbox
        return max(1e-6, float(x2 - x1))

    def _centroid_3d(
        self, centroid: tuple[float, float], bbox: tuple[float, float, float, float]
    ) -> tuple[float, float, float] | None:
        if self.camera_intrinsics is None:
            return None

        u, v = centroid
        z = (
            self.camera_intrinsics.fx * self.assumed_drone_width_m
        ) / self._bbox_width(bbox)

        x = ((u - self.camera_intrinsics.cx) * z) / self.camera_intrinsics.fx
        y = ((v - self.camera_intrinsics.cy) * z) / self.camera_intrinsics.fy
        return (x, y, z)

    def _update_stats(
        self,
        obj_id: int,
        prev_centroid: tuple[float, float] | None,
        new_centroid: tuple[float, float],
        bbox: tuple[float, float, float, float],
    ):
        stat = self.stats[obj_id]
        stat["last_seen_frame"] = self.frame_index
        stat["last_seen_time"] = self.frame_index / self.fps

        # Pixel speed (px/s): ||c_t - c_(t-1)|| * fps
        if prev_centroid is not None:
            delta_px = math.dist(new_centroid, prev_centroid)
            pixel_speed = delta_px * self.fps
            stat["pixel_speed_history"].append(pixel_speed)

        # Approximate 3D position and speed in m/s (if calibrated fields exist).
        pos3d = self._centroid_3d(new_centroid, bbox)
        if pos3d is not None:
            stat["position_3d_history_m"].append(pos3d)
            stat["distance_history_m"].append(pos3d[2])

            if (
                stat["closest_distance_m"] is None
                or pos3d[2] < stat["closest_distance_m"]
            ):
                stat["closest_distance_m"] = pos3d[2]

            if len(stat["position_3d_history_m"]) >= 2:
                prev = stat["position_3d_history_m"][-2]
                curr = pos3d
                speed_3d = math.dist(curr, prev) / (1.0 / self.fps)
                stat["speed_3d_history_mps"].append(speed_3d)
                self._update_hover_state(obj_id, speed_3d, curr)
            else:
                self._update_hover_state(obj_id, speed=None, curr_pos=pos3d)

    def _update_hover_state(
        self,
        obj_id: int,
        speed: float | None,
        curr_pos: tuple[float, float, float],
    ):
        stat = self.stats[obj_id]
        anchor = stat["active_hover_anchor"]
        start = stat["active_hover_start_frame"]

        meets_speed = speed is not None and speed < self.hover_speed_threshold_mps

        if anchor is None and meets_speed:
            stat["active_hover_anchor"] = curr_pos
            stat["active_hover_start_frame"] = self.frame_index
            return

        if anchor is None:
            return

        radius_ok = math.dist(curr_pos, anchor) <= self.hover_radius_m
        if meets_speed and radius_ok:
            return

        if start is not None:
            duration_s = (self.frame_index - start) / self.fps
            if duration_s >= self.hover_min_seconds:
                stat["hover_segments"].append(
                    {
                        "start_frame": start,
                        "end_frame": self.frame_index - 1,
                        "duration_s": duration_s,
                    }
                )

        stat["active_hover_start_frame"] = None
        stat["active_hover_anchor"] = None

    def update(self, detections: list[dict]) -> dict:
        self.frame_index += 1

        # If no detections, mark all existing objects as disappeared.
        if len(detections) == 0:
            for obj_id in list(self.disappeared.keys()):
                self.disappeared[obj_id] += 1
                if self.disappeared[obj_id] > self.max_disappeared:
                    self._deregister(obj_id)
            return self._build_output()

        input_centroids = [tuple(d["centroid"]) for d in detections]
        input_bboxes = [tuple(d["bbox"]) for d in detections]

        if len(self.objects) == 0:
            for cent, bbox in zip(input_centroids, input_bboxes):
                self._register(cent, bbox)
                self._update_stats(self.next_id - 1, None, cent, bbox)
            return self._build_output()

        obj_ids = list(self.objects.keys())
        obj_centroids = list(self.objects.values())

        # Distance matrix for greedy assignment.
        distances = [
            [math.dist(o, i) for i in input_centroids] for o in obj_centroids
        ]

        matched_objs = set()
        matched_inputs = set()
        pairs = sorted(
            ((r, c, distances[r][c]) for r in range(len(obj_centroids)) for c in range(len(input_centroids))),
            key=lambda x: x[2],
        )

        for r, c, distance in pairs:
            if r in matched_objs or c in matched_inputs:
                continue
            if distance > self.max_distance:
                continue

            obj_id = obj_ids[r]
            prev_centroid = self.objects[obj_id]
            new_centroid = input_centroids[c]
            new_bbox = input_bboxes[c]

            self.objects[obj_id] = new_centroid
            self.bboxes[obj_id] = new_bbox
            self.disappeared[obj_id] = 0
            self.trails[obj_id].append(new_centroid)
            self._update_stats(obj_id, prev_centroid, new_centroid, new_bbox)

            matched_objs.add(r)
            matched_inputs.add(c)

        # Existing objects not matched in this frame.
        for r in range(len(obj_ids)):
            if r not in matched_objs:
                obj_id = obj_ids[r]
                self.disappeared[obj_id] += 1
                if self.disappeared[obj_id] > self.max_disappeared:
                    self._deregister(obj_id)

        # New objects that appeared in this frame.
        for c in range(len(input_centroids)):
            if c not in matched_inputs:
                self._register(input_centroids[c], input_bboxes[c])
                self._update_stats(self.next_id - 1, None, input_centroids[c], input_bboxes[c])

        return self._build_output()

    def _build_output(self) -> dict:
        """Package tracked objects into a clean output dict."""
        output: dict[int, dict[str, Any]] = {}

        for obj_id in self.objects:
            s = self.stats[obj_id]
            first_frame = s["first_seen_frame"]
            last_frame = s["last_seen_frame"]
            frame_count = last_frame - first_frame + 1
            time_in_frame = frame_count / self.fps

            hover_time = sum(seg["duration_s"] for seg in s["hover_segments"])
            if s["active_hover_start_frame"] is not None:
                hover_time += (self.frame_index - s["active_hover_start_frame"] + 1) / self.fps

            output[obj_id] = {
                "bbox": self.bboxes[obj_id],
                "centroid": self.objects[obj_id],
                "trail": list(self.trails[obj_id][-120:]),
                "stats": {
                    "first_seen_frame": first_frame,
                    "last_seen_frame": last_frame,
                    "time_in_frame_s": time_in_frame,
                    "pixel_speed_current_px_s": s["pixel_speed_history"][-1]
                    if s["pixel_speed_history"]
                    else 0.0,
                    "pixel_speed_avg_px_s": (sum(s["pixel_speed_history"]) / len(s["pixel_speed_history"]))
                    if s["pixel_speed_history"]
                    else 0.0,
                    "estimated_distance_current_m": s["distance_history_m"][-1]
                    if s["distance_history_m"]
                    else None,
                    "closest_distance_m": s["closest_distance_m"],
                    "estimated_speed_current_mps": s["speed_3d_history_mps"][-1]
                    if s["speed_3d_history_mps"]
                    else None,
                    "estimated_speed_avg_mps": (sum(s["speed_3d_history_mps"]) / len(s["speed_3d_history_mps"]))
                    if s["speed_3d_history_mps"]
                    else None,
                    "hover_duration_s": hover_time,
                    "hover_segments": list(s["hover_segments"]),
                    "position_3d_current_m": s["position_3d_history_m"][-1]
                    if s["position_3d_history_m"]
                    else None,
                },
            }

        return output

    def _build_single_output(self, obj_id: int) -> dict[str, Any]:
        s = self.stats[obj_id]
        first_frame = s["first_seen_frame"]
        last_frame = s["last_seen_frame"]
        frame_count = last_frame - first_frame + 1
        time_in_frame = frame_count / self.fps

        hover_time = sum(seg["duration_s"] for seg in s["hover_segments"])
        if s["active_hover_start_frame"] is not None:
            hover_time += (self.frame_index - s["active_hover_start_frame"] + 1) / self.fps

        return {
            "bbox": self.bboxes[obj_id],
            "centroid": self.objects[obj_id],
            "trail": list(self.trails[obj_id][-120:]),
            "stats": {
                "first_seen_frame": first_frame,
                "last_seen_frame": last_frame,
                "time_in_frame_s": time_in_frame,
                "pixel_speed_current_px_s": s["pixel_speed_history"][-1]
                if s["pixel_speed_history"]
                else 0.0,
                "pixel_speed_avg_px_s": (sum(s["pixel_speed_history"]) / len(s["pixel_speed_history"]))
                if s["pixel_speed_history"]
                else 0.0,
                "estimated_distance_current_m": s["distance_history_m"][-1]
                if s["distance_history_m"]
                else None,
                "closest_distance_m": s["closest_distance_m"],
                "estimated_speed_current_mps": s["speed_3d_history_mps"][-1]
                if s["speed_3d_history_mps"]
                else None,
                "estimated_speed_avg_mps": (sum(s["speed_3d_history_mps"]) / len(s["speed_3d_history_mps"]))
                if s["speed_3d_history_mps"]
                else None,
                "hover_duration_s": hover_time,
                "hover_segments": list(s["hover_segments"]),
                "position_3d_current_m": s["position_3d_history_m"][-1]
                if s["position_3d_history_m"]
                else None,
            },
        }
