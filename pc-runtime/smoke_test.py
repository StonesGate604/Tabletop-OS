"""Generate a synthetic tag36h11 image and verify the detector/overlay."""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path

import cv2
import numpy as np
from pupil_apriltags import Detector

from apriltag_camera import detection_observation, draw_detection, emit_observations


def main() -> int:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    marker = cv2.aruco.generateImageMarker(dictionary, 7, 300)
    gray = np.full((500, 500), 255, dtype=np.uint8)
    gray[100:400, 100:400] = marker

    detections = Detector(families="tag36h11").detect(gray)
    if len(detections) != 1 or detections[0].tag_id != 7:
        print(f"FAIL: expected tag 7, got {[item.tag_id for item in detections]}")
        return 1

    result = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    draw_detection(result, detections[0])
    observation = detection_observation(detections[0])
    center_observation = observation["center_px"]
    if (
        observation["tag_id"] != 7
        or abs(center_observation["x"] - 250.0) > 1.0
        or abs(center_observation["y"] - 250.0) > 1.0
    ):
        print(f"FAIL: unexpected observation {observation}")
        return 2

    json_output = StringIO()
    with redirect_stdout(json_output):
        emit_observations(
            detections,
            sequence=1,
            camera_index=1,
            frame_width=500,
            frame_height=500,
            output_format="json",
        )
    payload = json.loads(json_output.getvalue())
    if (
        payload.get("type") != "vision.tags"
        or payload.get("coordinate_space") != "camera_px"
        or payload["tags"][0]["tag_id"] != 7
    ):
        print(f"FAIL: unexpected JSON payload {payload}")
        return 3
    output_dir = Path("outputs")
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "apriltag_smoke_test.png"
    clean_tag = output_dir / "tag36h11_id7.png"
    if not cv2.imwrite(str(output), result):
        print(f"FAIL: could not save {output}")
        return 4
    if not cv2.imwrite(str(clean_tag), gray):
        print(f"FAIL: could not save {clean_tag}")
        return 5

    center = tuple(np.round(detections[0].center).astype(int))
    print(
        f"PASS: detected tag36h11 ID 7 at center={center}; "
        f"wrote {output} and {clean_tag}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

