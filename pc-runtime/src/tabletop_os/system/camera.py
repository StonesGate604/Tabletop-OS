"""Camera acquisition and vision inference without application or device control."""
import time

from ..model import InputSample
from ..vision import AprilTagDetector, crop_frame, hand_sample, read_camera_frame


class CameraSession:
    """One cropped camera stream shared by hand, display and mechanical vision.

    Vision dependencies are loaded only when the session starts, allowing the
    system and application modules to remain importable in simulation installs.
    """

    def __init__(self, config):
        self.config = config
        self.capture = None
        self.landmarker = None
        self.detector = None
        self.size = None
        self._raw_size = None
        self._first_frame = None
        self._timestamp_ms = -1

    def start(self):
        if self.capture is not None:
            raise RuntimeError('Camera session already started')
        import cv2
        import mediapipe as mp

        cfg = self.config.camera
        if not self.config.path(cfg.model_path).is_file():
            raise FileNotFoundError('Hand model missing; run tools/download_hand_model.py')
        try:
            self.capture = cv2.VideoCapture(cfg.index)
            self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.width)
            self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.height)
            self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not self.capture.isOpened():
                raise RuntimeError('Camera failed to open')
            raw = read_camera_frame(self.capture)
            self._raw_size = (raw.shape[1], raw.shape[0])
            self._first_frame = crop_frame(raw, cfg)
            self.size = (self._first_frame.shape[1], self._first_frame.shape[0])
            options = mp.tasks.vision.HandLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=str(self.config.path(cfg.model_path))),
                running_mode=mp.tasks.vision.RunningMode.VIDEO,
                num_hands=1,
                min_hand_detection_confidence=cfg.min_confidence,
                min_hand_presence_confidence=cfg.min_confidence,
                min_tracking_confidence=cfg.min_confidence)
            self.landmarker = mp.tasks.vision.HandLandmarker.create_from_options(options)
            self.detector = AprilTagDetector(cfg.april_tag_dictionary, cfg.marker_min_area_px)
            self._timestamp_ms = -1
            return self
        except BaseException:
            self.close()
            raise

    def read(self):
        if self.capture is None:
            raise RuntimeError('Camera session is not started')
        if self._first_frame is not None:
            frame, self._first_frame = self._first_frame, None
            return frame
        ok, raw = self.capture.read()
        if not ok or raw is None:
            raise RuntimeError('Camera read failed')
        if (raw.shape[1], raw.shape[0]) != self._raw_size:
            raise RuntimeError('Camera resolution changed; restart and recalibrate')
        frame = crop_frame(raw, self.config.camera)
        if (frame.shape[1], frame.shape[0]) != self.size:
            raise RuntimeError('Camera crop size changed; restart and recalibrate')
        return frame

    def validate_calibration(self, calibration):
        if (calibration.width, calibration.height) != self.size:
            raise ValueError('Calibration differs from camera crop; recalibrate')
        if calibration.camera_matrix is not None or calibration.distortion is not None:
            raise ValueError('Gesture application requires plane-only calibrations; lens-corrected mappings are not supported')

    def analyze(self, frame, mapping, mechanical=None, now=None):
        if self.landmarker is None or self.detector is None:
            raise RuntimeError('Camera session is not started')
        import cv2
        import mediapipe as mp

        if now is None:
            now = time.monotonic()
        if (frame.shape[1], frame.shape[0]) != self.size:
            raise ValueError('Frame differs from camera crop')
        self.validate_calibration(mapping)
        if mechanical is not None:
            self.validate_calibration(mechanical)
        observations = self.detector.detect(frame, mapping, now)
        mechanical_observations = self.detector.detect(frame, mechanical, now) if mechanical is not None else ()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        self._timestamp_ms = max(self._timestamp_ms + 1, int(now * 1000))
        result = self.landmarker.detect_for_video(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), self._timestamp_ms)
        sample = InputSample(None, None, now)
        hand_pixel = None
        if result.hand_landmarks:
            hand = result.hand_landmarks[0]
            width, height = self.size
            sample = hand_sample(hand, width, height, mapping, now)
            hand_pixel = (round((hand[4].x + hand[8].x) * width / 2),
                          round((hand[4].y + hand[8].y) * height / 2))
        return sample, observations, mechanical_observations, hand_pixel

    def close(self):
        landmarker, capture = self.landmarker, self.capture
        self.landmarker = self.capture = self.detector = None
        self._first_frame = None
        try:
            if landmarker is not None:
                landmarker.close()
        finally:
            if capture is not None:
                capture.release()

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, traceback):
        self.close()
