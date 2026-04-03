"""
tracker.py — Centroid-based multi-person tracker.

Assigns persistent IDs to detected people across frames by matching
new detections to existing tracked objects using centroid distance.
Maintains a history of positions for each tracked person (used for
trail visualization and movement analysis).
"""

import numpy as np
from collections import OrderedDict


class CentroidTracker:
    def __init__(self, max_disappeared: int = 30, max_distance: float = 80):
        """
        Args:
            max_disappeared: Number of consecutive frames a person can be
                             missing before we drop them.
            max_distance: Maximum pixel distance to match a detection to
                          an existing tracked person.
        """
        self.next_id = 0
        self.max_disappeared = max_disappeared
        self.max_distance = max_distance

        # Tracked objects: id -> centroid (cx, cy)
        self.objects = OrderedDict()

        # How many frames each object has been missing
        self.disappeared = OrderedDict()

        # Bounding boxes for each object: id -> [x1,y1,x2,y2]
        self.bboxes = OrderedDict()

        # Position history for trails: id -> list of (cx, cy)
        self.trails = OrderedDict()

    def _register(self, centroid, bbox):
        """Add a new person with a fresh ID."""
        obj_id = self.next_id
        self.objects[obj_id] = centroid
        self.disappeared[obj_id] = 0
        self.bboxes[obj_id] = bbox
        self.trails[obj_id] = [centroid]
        self.next_id += 1
        return obj_id

    def _deregister(self, obj_id):
        """Remove a tracked person."""
        del self.objects[obj_id]
        del self.disappeared[obj_id]
        del self.bboxes[obj_id]
        del self.trails[obj_id]

    def update(self, detections: list[dict]) -> dict:
        """
        Update tracker with new detections from the current frame.

        Args:
            detections: List of dicts from PersonDetector.detect()

        Returns:
            Dict of currently tracked objects: {id: detection_dict}
            Each detection_dict has 'bbox', 'centroid', 'trail'
        """
        # If no detections, mark all existing objects as disappeared
        if len(detections) == 0:
            for obj_id in list(self.disappeared.keys()):
                self.disappeared[obj_id] += 1
                if self.disappeared[obj_id] > self.max_disappeared:
                    self._deregister(obj_id)
            return self._build_output()

        # Extract centroids and bboxes from detections
        input_centroids = [d["centroid"] for d in detections]
        input_bboxes = [d["bbox"] for d in detections]

        # If we have no existing objects, register all detections
        if len(self.objects) == 0:
            for cent, bbox in zip(input_centroids, input_bboxes):
                self._register(cent, bbox)
            return self._build_output()

        # Match existing objects to new detections using distance
        obj_ids = list(self.objects.keys())
        obj_centroids = list(self.objects.values())

        # Compute distance matrix: existing objects vs new detections
        obj_arr = np.array(obj_centroids)
        inp_arr = np.array(input_centroids)
        distances = np.linalg.norm(obj_arr[:, None] - inp_arr[None, :], axis=2)

        # Greedy matching: find closest pairs
        matched_objs = set()
        matched_inputs = set()

        # Sort all (obj_idx, input_idx) pairs by distance
        rows, cols = np.unravel_index(
            np.argsort(distances, axis=None), distances.shape
        )

        for r, c in zip(rows, cols):
            if r in matched_objs or c in matched_inputs:
                continue
            if distances[r, c] > self.max_distance:
                continue

            obj_id = obj_ids[r]
            self.objects[obj_id] = input_centroids[c]
            self.bboxes[obj_id] = input_bboxes[c]
            self.disappeared[obj_id] = 0
            self.trails[obj_id].append(input_centroids[c])

            matched_objs.add(r)
            matched_inputs.add(c)

        # Handle unmatched existing objects (disappeared)
        for r in range(len(obj_ids)):
            if r not in matched_objs:
                obj_id = obj_ids[r]
                self.disappeared[obj_id] += 1
                if self.disappeared[obj_id] > self.max_disappeared:
                    self._deregister(obj_id)

        # Handle unmatched new detections (new people)
        for c in range(len(input_centroids)):
            if c not in matched_inputs:
                self._register(input_centroids[c], input_bboxes[c])

        return self._build_output()

    def _build_output(self) -> dict:
        """Package tracked objects into a clean output dict."""
        output = {}
        for obj_id in self.objects:
            output[obj_id] = {
                "bbox": self.bboxes[obj_id],
                "centroid": self.objects[obj_id],
                "trail": list(self.trails[obj_id][-60:]),  # keep last 60 points
            }
        return output
