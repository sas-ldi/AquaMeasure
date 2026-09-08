"""Recalage numerique et cache de lecture, sans donnees de terrain."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_measure_to_coco_journey as journey
from src.imaging.rect_mapping import RectMapping


class RectMappingTest(unittest.TestCase):
    def test_without_calibration_coordinates_are_unchanged(self):
        mapping = RectMapping.identity()
        self.assertEqual(mapping.map_points([(1.25, 9.75), (-1, -1)]), [(1.25, 9.75), (-1, -1)])
        self.assertEqual(mapping.map_box(1, 2, 30, 40), (1, 2, 30, 40))

    def test_subpixel_and_frame_scale(self):
        yy, xx = np.indices((80, 100), dtype=np.float32)
        mapping = RectMapping(xx + 7, yy - 3, frame_size=(200, 160))
        np.testing.assert_allclose(mapping.map_point(50.5, 60.5), (64.5, 54.5))
        np.testing.assert_allclose(mapping.map_boxes([(20, 30, 60, 70)]), [(34, 24, 74, 64)])

    def test_calibration_map_roundtrips_rectified_landmarks(self):
        camera = np.array([[480., 0, 320], [0, 475., 240], [0, 0, 1]])
        distortion = np.array([-0.24, 0.04, 0.001, -0.002, 0.0])
        rotation, _ = cv2.Rodrigues(np.array([0.03, -0.06, 0.01]))
        maps = cv2.initUndistortRectifyMap(camera, distortion, rotation, camera, (640, 480), cv2.CV_32FC1)
        mapping = RectMapping(*maps)
        rectified = np.array([(85.25, 95.75), (320.5, 240.25), (520.2, 360.4)])
        raw = np.asarray(mapping.map_points(rectified))
        reconstructed = cv2.undistortPoints(raw.reshape(-1, 1, 2), camera, distortion, R=rotation, P=camera).reshape(-1, 2)
        error = np.linalg.norm(reconstructed - rectified, axis=1).max()
        self.assertLess(error, 0.02)

    def test_playback_converts_once_for_sixty_overlay_reads(self):
        journey.QCoreApplication.instance() or journey.QCoreApplication([])
        measure = journey._MeasureStub(Path(''))
        fish = journey.FishController(measure)
        yy, xx = np.indices((80, 100), dtype=np.float32)
        mapping = RectMapping(xx + 7, yy - 3)
        measure.overlay_remap_active = lambda: True
        measure.rect_mapping = lambda left=True: mapping
        measure.rect_mapping_token = lambda: 1
        cache = {'tracksByFrame': {3: [{'x1': 20., 'y1': 20., 'x2': 80., 'y2': 60.}]},
                 'instantByFrame': {}, 'trackPoints': {'t': {'trackId': 1, 'frames': [3, 4], 'points': [{'x': 50, 'y': 40}, {'x': 55, 'y': 42}]}}}
        with patch.object(fish, '_annotation_overlays', return_value=cache), patch.object(fish, '_build_display_overlays', wraps=fish._build_display_overlays) as convert:
            for _ in range(60):
                actual = fish._display_overlays()
            self.assertEqual(convert.call_count, 1)
            self.assertEqual(actual['trackPoints']['t']['points'][0], {'x': 57., 'y': 37.})
            self.assertEqual(cache['trackPoints']['t']['points'][0], {'x': 50, 'y': 40})

    def test_pinned_trail_grows_without_future_points(self):
        row = {'trackId': 7, 'frames': list(range(1000)), 'points': [{'x': i, 'y': i} for i in range(1000)]}
        tracks = journey.FishController._persisted_trails({'trackPoints': {'t': row}}, [], 700, pinned_key='t')
        points = tracks[0]['points']
        self.assertEqual(points[0]['x'], 0)
        self.assertEqual(points[-1]['x'], 700)
        self.assertLessEqual(len(points), 400)


if __name__ == '__main__':
    unittest.main()
