"""
detector.py — YOLOv8-based person detection.

Wraps the Ultralytics YOLOv8 model to detect people in video frames.
Returns bounding boxes and confidence scores for each detected person.
"""

from ultralytics import YOLO
import numpy as np


class PersonDetector:
    def __init__(self, model_name: str = "yolov8n.pt", confidence: float = 0.5):
        """
        Initialize the YOLOv8 detector.

        Args:
            model_name: Which YOLO model to use. 'yolov8n.pt' is the fastest
                        (nano). Use 'yolov8s.pt' for better accuracy.
            confidence: Minimum confidence score to count as a detection.
        """
        self.model = YOLO(model_name)
        self.confidence = confidence
        # In COCO dataset, class 0 = person
        self.person_class_id = 0

    def detect(self, frame: np.ndarray) -> list[dict]:
        """
        Run detection on a single frame.

        Args:
            frame: BGR image (from OpenCV).

        Returns:
            List of detections, each a dict with:
              - 'bbox': [x1, y1, x2, y2] pixel coordinates
              - 'confidence': float 0-1
              - 'centroid': (cx, cy) center point
        """
        # Run YOLO inference (verbose=False suppresses per-frame logs)
        results = self.model(frame, verbose=False, conf=self.confidence)

        detections = []
        for result in results:
            boxes = result.boxes
            if boxes is None:
                continue

            for i in range(len(boxes)):
                class_id = int(boxes.cls[i])
                if class_id != self.person_class_id:
                    continue

                conf = float(boxes.conf[i])
                x1, y1, x2, y2 = boxes.xyxy[i].tolist()

                cx = int((x1 + x2) / 2)
                cy = int((y1 + y2) / 2)

                detections.append({
                    "bbox": [int(x1), int(y1), int(x2), int(y2)],
                    "confidence": round(conf, 3),
                    "centroid": (cx, cy),
                })

        return detections
