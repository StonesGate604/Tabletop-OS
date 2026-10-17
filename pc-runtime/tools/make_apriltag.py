"""Generate a white-bordered AprilTag image for the configured single piece."""
import argparse
from pathlib import Path
import cv2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", type=int, default=0)
    parser.add_argument("--size", type=int, default=400)
    parser.add_argument("--output", type=Path, default=Path("tmp/apriltag-0.png"))
    args = parser.parse_args()
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    if not 0 <= args.id < len(dictionary.bytesList) or not 64 <= args.size <= 4096:
        parser.error("Invalid tag id or size")
    marker = cv2.aruco.generateImageMarker(dictionary, args.id, args.size)
    marker = cv2.copyMakeBorder(marker, args.size//8, args.size//8, args.size//8, args.size//8,
                                cv2.BORDER_CONSTANT, value=255)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(args.output), marker):
        raise RuntimeError("Cannot write marker image")
    print(f"Saved {args.output}; print with white border and mount flat on piece")


if __name__ == "__main__":
    main()
