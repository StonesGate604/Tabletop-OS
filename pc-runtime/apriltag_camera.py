"""Real-time tag36h11 detection with a Windows camera.

Press Q or Esc to quit. The green arrow points toward the tag's top edge.
Detected coordinates can be printed as readable text or NDJSON for Tabletop OS.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

import cv2
import numpy as np
from pupil_apriltags import Detector


BACKENDS = {
    "auto": cv2.CAP_ANY,
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect tag36h11 AprilTags from a USB or built-in camera."
    )
    parser.add_argument(
        "--camera",
        type=int,
        default=1,
        help="camera index (default: 1, physical Insta360 Link 2 on this PC)",
    )
    parser.add_argument(
        "--backend",
        choices=BACKENDS,
        default="dshow",
        help="OpenCV camera backend (default: dshow)",
    )
    parser.add_argument("--width", type=int, default=1920, help="requested frame width")
    parser.add_argument("--height", type=int, default=1080, help="requested frame height")
    parser.add_argument("--fps", type=float, default=30.0, help="requested camera FPS")
    parser.add_argument(
        "--decimate",
        type=float,
        default=2.0,
        help="AprilTag detection downsampling; higher is faster (default: 2.0)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=min(4, os.cpu_count() or 2),
        help="AprilTag detector worker threads (default: up to 4)",
    )
    parser.add_argument(
        "--output",
        choices=("text", "json", "none"),
        default="text",
        help="coordinate output format (default: text)",
    )
    parser.add_argument(
        "--print-rate",
        type=float,
        default=10.0,
        help="maximum coordinate snapshots per second (default: 10)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="do not open a preview window; stop with Ctrl+C",
    )
    return parser.parse_args()


def open_camera(
    index: int, backend_name: str, width: int, height: int, fps: float
) -> cv2.VideoCapture:
    camera = cv2.VideoCapture(index, BACKENDS[backend_name])
    # MJPEG avoids the USB bandwidth bottleneck common with uncompressed 720p.
    camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    camera.set(cv2.CAP_PROP_FPS, fps)
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return camera


def detection_observation(detection: object) -> dict[str, object]:
    corners = np.asarray(detection.corners, dtype=np.float32)
    center = np.asarray(detection.center, dtype=np.float32)

    # pupil-apriltags orders corners counter-clockwise from the upper-right in
    # image coordinates. The midpoint of corners 0 and 1 is the tag's top edge.
    top_midpoint = (corners[0] + corners[1]) / 2
    dx = top_midpoint[0] - center[0]
    dy = top_midpoint[1] - center[1]
    angle_deg = math.degrees(math.atan2(-dy, dx))

    return {
        "tag_id": int(detection.tag_id),
        "center_px": {"x": round(float(center[0]), 2), "y": round(float(center[1]), 2)},
        "heading_deg": round(float(angle_deg), 2),
        "corners_px": [
            {"x": round(float(point[0]), 2), "y": round(float(point[1]), 2)}
            for point in corners
        ],
        "decision_margin": round(float(detection.decision_margin), 2),
        "hamming": int(detection.hamming),
    }


def draw_detection(frame: np.ndarray, detection: object) -> None:
    observation = detection_observation(detection)
    corners = np.asarray(detection.corners, dtype=np.float32)
    points = np.round(corners).astype(np.int32)
    center = tuple(np.round(detection.center).astype(int))
    top_midpoint = tuple(np.round((corners[0] + corners[1]) / 2).astype(int))

    cv2.polylines(frame, [points], True, (0, 255, 0), 2, cv2.LINE_AA)
    cv2.circle(frame, center, 5, (0, 0, 255), -1, cv2.LINE_AA)
    cv2.arrowedLine(frame, center, top_midpoint, (0, 255, 0), 3, cv2.LINE_AA, tipLength=0.25)

    label = (
        f"ID {detection.tag_id}  center=({center[0]}, {center[1]})  "
        f"heading={observation['heading_deg']:.1f} deg"
    )
    label_origin = (points[:, 0].min(), max(24, points[:, 1].min() - 10))
    cv2.putText(
        frame,
        label,
        label_origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )


def emit_observations(
    detections: list[object],
    *,
    sequence: int,
    camera_index: int,
    frame_width: int,
    frame_height: int,
    output_format: str,
) -> None:
    tags = [detection_observation(detection) for detection in detections]
    if output_format == "json":
        message = {
            "v": 1,
            "type": "vision.tags",
            "seq": sequence,
            "captured_at_ms": int(time.time() * 1000),
            "coordinate_space": "camera_px",
            "camera": {
                "index": camera_index,
                "width_px": frame_width,
                "height_px": frame_height,
            },
            "tags": tags,
        }
        print(json.dumps(message, ensure_ascii=False, separators=(",", ":")), flush=True)
        return

    timestamp = time.strftime("%H:%M:%S")
    if not tags:
        print(f"[{timestamp}] no tags", flush=True)
        return
    for tag in tags:
        center = tag["center_px"]
        print(
            f"[{timestamp}] ID {tag['tag_id']}  "
            f"center_px=({center['x']:.2f}, {center['y']:.2f})  "
            f"heading={tag['heading_deg']:.2f} deg  "
            f"margin={tag['decision_margin']:.2f}",
            flush=True,
        )


def main() -> int:
    args = parse_args()
    detector = Detector(
        families="tag36h11",
        nthreads=max(1, args.threads),
        quad_decimate=max(1.0, args.decimate),
        quad_sigma=0.0,
        refine_edges=1,
        decode_sharpening=0.25,
        debug=0,
    )

    camera = open_camera(args.camera, args.backend, args.width, args.height, args.fps)
    if not camera.isOpened():
        print(
            f"Cannot open camera {args.camera} with backend {args.backend}. "
            "Try --camera 0, --camera 6, or --backend msmf.",
            file=sys.stderr,
        )
        return 2

    ok, frame = camera.read()
    if not ok or frame is None:
        camera.release()
        print(
            "Camera opened but did not return a frame. Close other camera apps and "
            "check Windows Settings > Privacy & security > Camera.",
            file=sys.stderr,
        )
        return 3

    print(
        f"Camera ready: index={args.camera}, backend={args.backend}, "
        f"frame={frame.shape[1]}x{frame.shape[0]}, "
        f"requested_fps={args.fps:g}, decimate={args.decimate:g}, "
        f"threads={args.threads}. "
        + ("Press Ctrl+C to quit." if args.headless else "Press Q or Esc to quit."),
        file=sys.stderr,
    )

    previous_time = time.perf_counter()
    last_print_time = 0.0
    print_interval = 1.0 / max(args.print_rate, 0.1)
    sequence = 0
    smoothed_fps = 0.0
    try:
        while True:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            detections = detector.detect(gray, estimate_tag_pose=False)
            for detection in detections:
                draw_detection(frame, detection)

            now = time.perf_counter()
            if args.output != "none" and now - last_print_time >= print_interval:
                sequence += 1
                emit_observations(
                    detections,
                    sequence=sequence,
                    camera_index=args.camera,
                    frame_width=frame.shape[1],
                    frame_height=frame.shape[0],
                    output_format=args.output,
                )
                last_print_time = now
            instantaneous_fps = 1.0 / max(now - previous_time, 1e-6)
            smoothed_fps = instantaneous_fps if smoothed_fps == 0 else 0.9 * smoothed_fps + 0.1 * instantaneous_fps
            previous_time = now
            cv2.putText(
                frame,
                f"tag36h11 | tags: {len(detections)} | {smoothed_fps:.1f} FPS",
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 0),
                2,
                cv2.LINE_AA,
            )
            if not args.headless:
                cv2.imshow("AprilTag tag36h11 - Q/Esc to quit", frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break

            ok, frame = camera.read()
            if not ok or frame is None:
                print("Lost the camera stream.", file=sys.stderr)
                return 4
    except KeyboardInterrupt:
        pass
    finally:
        camera.release()
        cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

