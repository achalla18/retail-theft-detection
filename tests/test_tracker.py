import unittest

from src.tracker import CameraIntrinsics, CentroidTracker


class TrackerTests(unittest.TestCase):
    def test_time_and_pixel_speed(self):
        tracker = CentroidTracker(fps=10.0, max_disappeared=1)

        out = tracker.update([{"centroid": (10, 10), "bbox": (0, 0, 20, 20)}])
        self.assertAlmostEqual(out[0]["stats"]["time_in_frame_s"], 0.1)

        out = tracker.update([{"centroid": (13, 14), "bbox": (3, 4, 23, 24)}])
        self.assertAlmostEqual(out[0]["stats"]["pixel_speed_current_px_s"], 50.0)
        self.assertAlmostEqual(out[0]["stats"]["time_in_frame_s"], 0.2)

    def test_distance_and_3d_speed(self):
        tracker = CentroidTracker(
            fps=10.0,
            camera_intrinsics=CameraIntrinsics(fx=1000, fy=1000, cx=100, cy=100),
            assumed_drone_width_m=0.4,
            max_disappeared=1,
        )

        out = tracker.update([{"centroid": (100, 100), "bbox": (80, 80, 120, 120)}])
        self.assertAlmostEqual(out[0]["stats"]["estimated_distance_current_m"], 10.0)

        out = tracker.update([{"centroid": (104, 100), "bbox": (84, 80, 124, 120)}])
        self.assertIsNotNone(out[0]["stats"]["estimated_speed_current_mps"])
        self.assertAlmostEqual(out[0]["stats"]["closest_distance_m"], 10.0)

    def test_completed_track_archive(self):
        tracker = CentroidTracker(max_disappeared=1)
        tracker.update([{"centroid": (1, 1), "bbox": (0, 0, 2, 2)}])
        tracker.update([])
        tracker.update([])
        completed = tracker.get_completed_tracks()
        self.assertIn(0, completed)


if __name__ == "__main__":
    unittest.main()
