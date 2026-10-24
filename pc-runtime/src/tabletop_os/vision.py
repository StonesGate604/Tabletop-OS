"""Camera + MediaPipe Tasks + AprilTag dictionaries, isolated in a process."""
from dataclasses import dataclass
import logging
import multiprocessing as mp
import queue
import time

from .calibration import Calibration
from .config import RuntimeConfig
from .model import InputSample, Observation

LOG = logging.getLogger(__name__)


def crop_frame(frame, config):
    """Use the same pixel origin for calibration and all vision consumers."""
    if not config.crop_width:
        return frame
    x, y, w, h = config.crop_x, config.crop_y, config.crop_width, config.crop_height
    if x+w > frame.shape[1] or y+h > frame.shape[0]:
        raise ValueError("Camera crop outside actual frame; check resolution")
    return frame[y:y+h, x:x+w].copy()


def read_camera_frame(capture, timeout_s=10):
    """Virtual cameras can return black frames while their source starts."""
    deadline = time.monotonic()+timeout_s
    while time.monotonic() < deadline:
        ok, frame = capture.read()
        if ok and frame is not None and frame.std() > 1:
            return frame
        time.sleep(0.02)
    raise RuntimeError("Camera has no usable image; check virtual camera output")


@dataclass(frozen=True)
class VisionResult:
    sample: InputSample
    observations: tuple[Observation, ...] = ()
    preview: bytes = b""
    error: str = ""


class AprilTagDetector:
    def __init__(self, dictionary: str, min_area: float):
        import cv2
        self.cv2 = cv2
        if not dictionary.startswith("DICT_APRILTAG_") or not hasattr(cv2.aruco, dictionary):
            raise ValueError("Unsupported AprilTag dictionary")
        self.detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary)),
                                               cv2.aruco.DetectorParameters())
        self.min_area = min_area

    def detect(self, frame, calibration: Calibration, now: float) -> tuple[Observation, ...]:
        corners, ids, _ = self.detector.detectMarkers(frame)
        result = []
        if ids is not None:
            for points, tag in zip(corners, ids.flatten()):
                polygon = points.reshape(4, 2)
                if abs(self.cv2.contourArea(polygon)) < self.min_area:
                    continue
                # Project corners first; averaging image coordinates creates perspective bias.
                projected = [calibration.map(float(x), float(y)) for x, y in polygon]
                from .model import Point
                result.append(Observation(int(tag), Point(sum(p.x for p in projected)/4,
                                                          sum(p.y for p in projected)/4), now))
        return tuple(result)


def hand_sample(landmarks, width: int, height: int, calibration: Calibration, now: float) -> InputSample:
    import math
    # 4/8: thumb/index tips. 5/17: palm width. Use pixel-scaled distances for rectangular images.
    def distance(a, b):
        return math.hypot((a.x-b.x)*width, (a.y-b.y)*height)
    palm = distance(landmarks[5], landmarks[17])
    if palm < 5:
        return InputSample(None, None, now)
    ratio = distance(landmarks[4], landmarks[8])/palm
    # Midpoint between tips stays stable when fingers come together.
    x = (landmarks[4].x+landmarks[8].x)*width/2
    y = (landmarks[4].y+landmarks[8].y)*height/2
    return InputSample(calibration.map(x, y), ratio, now)


def _put_latest(output, value):
    try:
        output.put_nowait(value)
    except queue.Full:
        try:
            output.get_nowait()
        except queue.Empty:
            pass
        try:
            output.put_nowait(value)
        except queue.Full:
            pass


def camera_process(config: RuntimeConfig, output, stop):
    capture = landmarker = None
    output.cancel_join_thread()
    try:
        import cv2
        import mediapipe as mediapipe
        import numpy as np
        cfg = config.camera
        calibration = Calibration.load(config.path(cfg.calibration_path), config.board)
        if not config.path(cfg.model_path).is_file():
            raise FileNotFoundError("Hand model missing; run tools/download_hand_model.py")
        capture = cv2.VideoCapture(cfg.index)
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.height)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not capture.isOpened():
            raise RuntimeError("Camera failed to open")
        options = mediapipe.tasks.vision.HandLandmarkerOptions(
            base_options=mediapipe.tasks.BaseOptions(model_asset_path=str(config.path(cfg.model_path))),
            running_mode=mediapipe.tasks.vision.RunningMode.VIDEO, num_hands=1,
            min_hand_detection_confidence=cfg.min_confidence,
            min_hand_presence_confidence=cfg.min_confidence, min_tracking_confidence=cfg.min_confidence)
        landmarker = mediapipe.tasks.vision.HandLandmarker.create_from_options(options)
        tags = AprilTagDetector(cfg.april_tag_dictionary, cfg.marker_min_area_px)
        previous_ms = -1
        first_frame = read_camera_frame(capture)
        while not stop.is_set():
            if first_frame is not None:
                ok, frame = True, first_frame
                first_frame = None
            else:
                ok, frame = capture.read()
            now = time.monotonic()
            if not ok:
                raise RuntimeError("Camera read failed")
            if calibration.camera_matrix is not None:
                frame = cv2.undistort(frame, np.array(calibration.camera_matrix), np.array(calibration.distortion))
            frame = crop_frame(frame, cfg)
            height, width = frame.shape[:2]
            if (width, height) != (calibration.width, calibration.height):
                raise ValueError("Camera resolution/crop differs from calibration; recalibrate")
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            timestamp_ms = max(previous_ms+1, int(now*1000))
            previous_ms = timestamp_ms
            result = landmarker.detect_for_video(mediapipe.Image(image_format=mediapipe.ImageFormat.SRGB, data=rgb), timestamp_ms)
            sample = hand_sample(result.hand_landmarks[0], width, height, calibration, now) if result.hand_landmarks else InputSample(None, None, now)
            observations = tags.detect(frame, calibration, now)
            preview_height = max(1, round(height*480/width))
            small = cv2.resize(rgb, (480, preview_height))
            preview = f"P6\n480 {preview_height}\n255\n".encode()+small.tobytes()
            _put_latest(output, VisionResult(sample, observations, preview))
    except Exception as exc:
        _put_latest(output, VisionResult(InputSample(None, None, time.monotonic()), error=str(exc)))
    finally:
        if landmarker:
            landmarker.close()
        if capture:
            capture.release()


class CameraWorker:
    def __init__(self, config: RuntimeConfig):
        context = mp.get_context("spawn")
        self.output = context.Queue(maxsize=1)
        self.stop = context.Event()
        self.process = context.Process(target=camera_process, args=(config, self.output, self.stop),
                                       name="camera-vision", daemon=True)
        self.result = VisionResult(InputSample(None, None, 0))

    def start(self):
        self.process.start()

    def latest(self) -> VisionResult:
        try:
            while True:
                self.result = self.output.get_nowait()
        except queue.Empty:
            return self.result

    def close(self):
        self.stop.set()
        self.process.join(timeout=2)
        if self.process.is_alive():
            LOG.warning("Camera read did not return; terminating isolated vision process")
            self.process.terminate()
            self.process.join(timeout=2)
        self.output.close()
