"""
detector.py — AerialGuard aerial object detector.

Wraps Ultralytics YOLO in track() mode so detection and multi-object
tracking happen in a single call.  BoT-SORT and ByteTrack are both
supported via Ultralytics' built-in tracker configs.

Custom drone models (e.g., trained on VisDrone) can be dropped in by
changing the model_name in config/settings.json.  The standard
yolov8n.pt works as a stand-in: in outdoor scenes with clear sky
backgrounds almost every detection is an aerial target of interest.
"""

from ultralytics import YOLO
import numpy as np


# COCO class names (subset that may appear as aerial objects)
_COCO_NAMES: dict[int, str] = {
    0:  "person",
    1:  "bicycle",
    2:  "car",
    4:  "airplane",
    14: "bird",
    16: "dog",
}


class AerialDetector:
    """
    Runs YOLO in persistent-track mode so object IDs survive across frames.

    Args:
        model_name:      Path or Ultralytics model name.  Default yolov8n.pt.
        confidence:      Minimum detection confidence (0–1).
        iou_threshold:   NMS IoU threshold.
        tracker:         Ultralytics tracker config: 'bytetrack.yaml' or
                         'botsort.yaml'.
        target_classes:  List of COCO class IDs to keep, or None for all.
    """

    def __init__(
        self,
        model_name: str = "yolov8n.pt",
        confidence: float = 0.35,
        iou_threshold: float = 0.5,
        tracker: str = "bytetrack.yaml",
        target_classes: list[int] | None = None,
    ):
        self.model = YOLO(model_name)
        self.confidence = confidence
        self.iou = iou_threshold
        self.tracker = tracker
        self.target_classes = target_classes  # None = all classes

    def track(self, frame: np.ndarray) -> list[dict]:
        """
        Run detection + tracking on one frame.

        Returns a list of dicts, each representing one tracked object:
          track_id   – persistent integer ID across frames
          bbox       – [x1, y1, x2, y2] in pixels
          centroid   – (cx, cy) center of bbox
          confidence – detection score 0–1
          class_id   – COCO class integer
          class_name – human-readable class label
        """
        results = self.model.track(
            frame,
            persist=True,
            tracker=self.tracker,
            conf=self.confidence,
            iou=self.iou,
            classes=self.target_classes,
            verbose=False,
        )

        tracked: list[dict] = []
        for result in results:
            boxes = result.boxes
            if boxes is None or boxes.id is None:
                continue

            ids  = boxes.id.int().cpu().tolist()
            xyxy = boxes.xyxy.cpu().tolist()
            conf = boxes.conf.cpu().tolist()
            cls  = boxes.cls.int().cpu().tolist()

            for tid, box, c, class_id in zip(ids, xyxy, conf, cls):
                x1, y1, x2, y2 = (int(v) for v in box)
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2
                tracked.append({
                    "track_id":   tid,
                    "bbox":       [x1, y1, x2, y2],
                    "centroid":   (cx, cy),
                    "confidence": round(c, 3),
                    "class_id":   class_id,
                    "class_name": _COCO_NAMES.get(class_id, f"class_{class_id}"),
                })

        return tracked
