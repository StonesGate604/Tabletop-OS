from pathlib import Path
import importlib.util
import unittest
from tabletop_os.config import BoardConfig
from tabletop_os.calibration import Calibration, from_corners

HAS_CV = importlib.util.find_spec('cv2') is not None


@unittest.skipUnless(HAS_CV, 'Install the vision extra')
class VisionTests(unittest.TestCase):
    def test_homography_maps_corners_and_rejects_degenerate(self):
        h = from_corners([(100,100),(700,80),(750,550),(80,560)],(800,600),BoardConfig())
        point = h.map(750,550)
        self.assertAlmostEqual(point.x,342,places=3)
        self.assertAlmostEqual(point.y,515,places=3)
        with self.assertRaises(ValueError):
            from_corners([(0,0),(10,10),(20,20),(30,30)],(800,600),BoardConfig())

    def test_real_opencv_apriltag_detector_on_generated_tag(self):
        import cv2
        from tabletop_os.vision import AprilTagDetector
        dictionary=cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
        marker=cv2.aruco.generateImageMarker(dictionary,0,200)
        image=cv2.copyMakeBorder(marker,100,100,100,100,cv2.BORDER_CONSTANT,value=255)
        h=from_corners([(0,0),(399,0),(399,399),(0,399)],(400,400),BoardConfig())
        observations=AprilTagDetector('DICT_APRILTAG_36h11',100).detect(image,h,1)
        self.assertEqual(len(observations),1)
        self.assertEqual(observations[0].piece_id,0)
        self.assertAlmostEqual(observations[0].position.x,171,delta=1)
        self.assertAlmostEqual(observations[0].position.y,257.5,delta=1)

    @unittest.skipUnless(importlib.util.find_spec('mediapipe'), 'MediaPipe not installed')
    def test_actual_hand_landmarker_model_load_and_blank_inference(self):
        import mediapipe as mp
        import numpy as np
        model=Path(__file__).resolve().parents[1]/'models/hand_landmarker.task'
        if not model.exists():
            self.skipTest('Run tools/download_hand_model.py')
        options=mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,num_hands=1)
        with mp.tasks.vision.HandLandmarker.create_from_options(options) as detector:
            result=detector.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,
                                                      data=np.zeros((240,320,3),dtype=np.uint8)),1)
        self.assertEqual(result.hand_landmarks,[])


if __name__ == '__main__':
    unittest.main()
