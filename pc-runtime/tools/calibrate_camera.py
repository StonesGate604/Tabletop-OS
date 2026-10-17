"""Four-click plane calibration; explicit save, no hardware motion."""
import argparse
from pathlib import Path
import cv2
from tabletop_os.calibration import from_corners
from tabletop_os.config import load_config


def main():
    parser = argparse.ArgumentParser(description="按桌面 (0,0),(W,0),(W,H),(0,H) 顺序点击四角；S 保存，R 重选，Esc 取消")
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1]/"config/runtime.json")
    parser.add_argument("--image", type=Path, help="可选离线标定图；缺省从相机获取一帧")
    parser.add_argument("--corners", help="离线坐标，例如 100,100;1100,100;1100,600;100,600")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = load_config(args.config)
    capture = None
    try:
        if args.image:
            image = cv2.imread(str(args.image))
        else:
            capture = cv2.VideoCapture(config.camera.index)
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, config.camera.width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, config.camera.height)
            ok, image = capture.read()
            if not ok:
                raise RuntimeError("Camera read failed")
        if image is None:
            raise ValueError("Image not available")
        output = args.output or config.path(config.camera.calibration_path)
        height, width = image.shape[:2]
        corners = []
        if args.corners:
            corners = [tuple(float(v) for v in item.split(",")) for item in args.corners.split(";")]
            from_corners(corners, (width, height), config.board).save(output)
            print(f"Saved {output}; validate independent points before motion")
            return
        title = "Tabletop calibration: 0,0 -> W,0 -> W,H -> 0,H | S save R reset Esc cancel"
        cv2.namedWindow(title)
        def click(event, x, y, flags, userdata):
            if event == cv2.EVENT_LBUTTONDOWN and len(corners) < 4:
                corners.append((x, y))
        cv2.setMouseCallback(title, click)
        while True:
            display = image.copy()
            for index, point in enumerate(corners):
                x, y = map(int, point)
                cv2.circle(display, (x, y), 6, (0, 255, 0), 2)
                cv2.putText(display, str(index+1), (x+8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,255,0), 2)
            cv2.imshow(title, display)
            key = cv2.waitKey(20) & 255
            if key == 27:
                return
            if key == ord("r"):
                corners.clear()
            if key == ord("s") and len(corners) == 4:
                from_corners(corners, (width, height), config.board).save(output)
                print(f"Saved {output}; plane only, lens distortion not calibrated")
                return
    finally:
        if capture:
            capture.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
