"""Camera pixels -> board millimetres. No implicit full-image calibration."""
from dataclasses import dataclass
from pathlib import Path
import json
import math
from .config import BoardConfig
from .model import Point


@dataclass(frozen=True)
class Calibration:
    width: int
    height: int
    board_width_mm: float
    board_height_mm: float
    matrix: tuple[tuple[float, ...], ...]
    camera_matrix: tuple | None = None
    distortion: tuple | None = None

    def map(self, x: float, y: float) -> Point:
        h = self.matrix
        denominator = h[2][0]*x+h[2][1]*y+h[2][2]
        if abs(denominator) < 1e-12:
            raise ValueError("Point maps to homography infinity")
        result = Point((h[0][0]*x+h[0][1]*y+h[0][2])/denominator,
                       (h[1][0]*x+h[1][1]*y+h[1][2])/denominator)
        if not result.finite():
            raise ValueError("Invalid projected point")
        return result

    def save(self, path: Path):
        from dataclasses import asdict
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix+".tmp")
        temporary.write_text(json.dumps({"v": 1, **asdict(self)}, indent=2), encoding="utf-8")
        temporary.replace(path)

    @classmethod
    def load(cls, path: Path, board: BoardConfig) -> "Calibration":
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if raw.pop("v", None) != 1:
            raise ValueError("Unsupported calibration version")
        result = cls(**raw)
        if (result.width <= 0 or result.height <= 0 or
                abs(result.board_width_mm-board.width_mm) > 1e-6 or
                abs(result.board_height_mm-board.height_mm) > 1e-6 or
                len(result.matrix) != 3 or any(len(row) != 3 for row in result.matrix) or
                not all(math.isfinite(v) for row in result.matrix for v in row)):
            raise ValueError("Invalid calibration or board dimensions changed")
        if (result.camera_matrix is None) != (result.distortion is None):
            raise ValueError("Intrinsics and distortion must be supplied together")
        h = result.matrix
        determinant = (h[0][0]*(h[1][1]*h[2][2]-h[1][2]*h[2][1]) -
                       h[0][1]*(h[1][0]*h[2][2]-h[1][2]*h[2][0]) +
                       h[0][2]*(h[1][0]*h[2][1]-h[1][1]*h[2][0]))
        if abs(determinant) < 1e-12:
            raise ValueError("Singular saved calibration")
        return result


def from_corners(corners: list[tuple[float, float]], size: tuple[int, int], board: BoardConfig) -> Calibration:
    import cv2
    import numpy as np
    if len(corners) != 4:
        raise ValueError("Select four board corners in order: (0,0), (W,0), (W,H), (0,H)")
    pixels = np.asarray(corners, dtype=np.float32)
    if not np.isfinite(pixels).all() or not cv2.isContourConvex(pixels) or abs(cv2.contourArea(pixels)) < 100:
        raise ValueError("Calibration points must form a non-degenerate convex quadrilateral")
    if any(not (0 <= x < size[0] and 0 <= y < size[1]) for x, y in corners):
        raise ValueError("Calibration point outside image")
    target = np.array([[0,0], [board.width_mm,0], [board.width_mm,board.height_mm],
                       [0,board.height_mm]], dtype=np.float32)
    h = cv2.getPerspectiveTransform(pixels, target)
    if not np.isfinite(h).all() or abs(np.linalg.det(h)) < 1e-12:
        raise ValueError("Singular calibration")
    return Calibration(size[0], size[1], board.width_mm, board.height_mm,
                       tuple(tuple(float(x) for x in row) for row in h))
