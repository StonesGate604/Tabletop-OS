"""Camera capability checks with fake capture/model objects; no device access."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tabletop_os.calibration import Calibration
from tabletop_os.config import CameraConfig, RuntimeConfig
from tabletop_os.model import Observation
from tabletop_os.system.camera import CameraSession


@unittest.skipUnless(importlib.util.find_spec('numpy'), 'Vision dependencies unavailable')
class CameraSessionTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.np = np
        self.raw = np.arange(16*30*3, dtype=np.uint8).reshape(16, 30, 3)
        self.config = RuntimeConfig(camera=CameraConfig(
            crop_x=2, crop_y=3, crop_width=20, crop_height=10))
        self.capture = Mock()
        self.capture.isOpened.return_value = True
        self.capture.read.side_effect = lambda: (True, self.raw.copy())
        self.landmarker = Mock()
        self.landmarker.detect_for_video.return_value = SimpleNamespace(hand_landmarks=[])
        self.create_model = Mock(return_value=self.landmarker)
        self.detector = Mock()
        self.detector.detect.side_effect = lambda frame, cal, now: (
            Observation(7, cal.map(4, 5), now),)
        self.cv = SimpleNamespace(
            VideoCapture=Mock(return_value=self.capture), CAP_PROP_FRAME_WIDTH=3,
            CAP_PROP_FRAME_HEIGHT=4, CAP_PROP_BUFFERSIZE=38, COLOR_BGR2RGB=1,
            cvtColor=lambda frame, code: frame)
        self.mp = SimpleNamespace(
            tasks=SimpleNamespace(
                BaseOptions=Mock(),
                vision=SimpleNamespace(
                    HandLandmarkerOptions=Mock(), RunningMode=SimpleNamespace(VIDEO='VIDEO'),
                    HandLandmarker=SimpleNamespace(create_from_options=self.create_model))),
            Image=lambda **fields: fields, ImageFormat=SimpleNamespace(SRGB='SRGB'))
        self.enterContext(patch.dict('sys.modules', {'cv2': self.cv, 'mediapipe': self.mp}))
        self.enterContext(patch.object(Path, 'is_file', return_value=True))
        self.warmup = self.enterContext(patch(
            'tabletop_os.system.camera.read_camera_frame', return_value=self.raw))
        self.enterContext(patch('tabletop_os.system.camera.AprilTagDetector', return_value=self.detector))
        self.session = CameraSession(self.config)
        self.addCleanup(self.session.close)
        self.mapping = Calibration(20, 10, 63, 63, ((1,0,0),(0,1,0),(0,0,1)))

    def test_warmup_frame_is_cropped_once_and_next_reads_share_origin(self):
        self.session.start()
        self.assertEqual(self.session.size, (20,10))
        first = self.session.read()
        self.warmup.assert_called_once_with(self.capture)
        self.capture.read.assert_not_called()
        self.np.testing.assert_array_equal(first, self.raw[3:13,2:22])
        self.np.testing.assert_array_equal(self.session.read(), first)
        self.capture.read.assert_called_once_with()

    def test_same_frame_is_detected_directly_in_both_maps(self):
        self.session.start()
        frame = self.session.read()
        mechanical = Calibration(20,10,342,515,((2,0,1),(0,3,2),(0,0,1)))
        sample, display_tags, physical_tags, pixel = self.session.analyze(
            frame, self.mapping, mechanical, 1.0)
        self.assertIsNone(sample.position)
        self.assertIsNone(pixel)
        self.assertEqual(display_tags[0].position, self.mapping.map(4,5))
        self.assertEqual(physical_tags[0].position, mechanical.map(4,5))
        self.assertIs(self.detector.detect.call_args_list[0].args[0], frame)
        self.assertIs(self.detector.detect.call_args_list[1].args[0], frame)

    def test_hand_midpoint_uses_crop_and_inference_timestamps_increase(self):
        self.session.start()
        frame = self.session.read()
        hand = [SimpleNamespace(x=.5,y=.5) for _ in range(21)]
        hand[4], hand[8] = SimpleNamespace(x=.3,y=.8), SimpleNamespace(x=.5,y=.4)
        hand[5], hand[17] = SimpleNamespace(x=.2,y=.2), SimpleNamespace(x=.8,y=.2)
        self.landmarker.detect_for_video.return_value = SimpleNamespace(hand_landmarks=[hand])
        sample, _, _, pixel = self.session.analyze(frame,self.mapping,now=1.0)
        self.assertAlmostEqual(sample.position.x,8)
        self.assertAlmostEqual(sample.position.y,6)
        self.assertEqual(pixel,(8,6))
        self.assertGreater(sample.pinch_ratio,0)
        self.session.analyze(frame,self.mapping,now=1.0)
        self.session.analyze(frame,self.mapping,now=.9)
        self.assertEqual([call.args[1] for call in self.landmarker.detect_for_video.call_args_list],
                         [1000,1001,1002])

    def test_resolution_change_and_failed_reads_are_rejected(self):
        self.session.start()
        self.session.read()
        self.capture.read.side_effect = None
        self.capture.read.return_value = (True,self.np.zeros((17,30,3),dtype=self.np.uint8))
        with self.assertRaisesRegex(RuntimeError,'resolution changed'):
            self.session.read()
        self.capture.read.return_value = (False,None)
        with self.assertRaisesRegex(RuntimeError,'read failed'):
            self.session.read()

    def test_intrinsics_and_wrong_map_size_rejected_before_inference(self):
        self.session.start()
        frame = self.session.read()
        intrinsics = Calibration(20,10,342,515,((1,0,0),(0,1,0),(0,0,1)),
                                 camera_matrix=((1,0,0),(0,1,0),(0,0,1)),
                                 distortion=(0,0,0,0,0))
        with self.assertRaisesRegex(ValueError,'plane-only'):
            self.session.analyze(frame,self.mapping,intrinsics,1)
        wrong_size = Calibration(21,10,63,63,self.mapping.matrix)
        with self.assertRaisesRegex(ValueError,'camera crop'):
            self.session.analyze(frame,wrong_size,now=1)
        self.detector.detect.assert_not_called()
        self.landmarker.detect_for_video.assert_not_called()

    def test_partial_start_failure_closes_created_resources(self):
        with patch('tabletop_os.system.camera.AprilTagDetector', side_effect=RuntimeError('detector failed')):
            with self.assertRaisesRegex(RuntimeError,'detector failed'):
                self.session.start()
        self.landmarker.close.assert_called_once_with()
        self.capture.release.assert_called_once_with()
        self.assertIsNone(self.session.capture)

    def test_close_is_idempotent_and_releases_capture_even_if_model_close_fails(self):
        self.session.start()
        self.landmarker.close.side_effect = RuntimeError('model cleanup failed')
        with self.assertRaisesRegex(RuntimeError,'model cleanup failed'):
            self.session.close()
        self.session.close()
        self.capture.release.assert_called_once_with()
        self.landmarker.close.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
