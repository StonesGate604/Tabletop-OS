"""Strict configuration; machine-specific files are never assumed calibrated."""
from dataclasses import dataclass, field, fields
from pathlib import Path
import json
import math

from .model import Point


@dataclass(frozen=True)
class BoardConfig:
    width_mm: float = 342
    height_mm: float = 515
    margin_mm: float = 2
    width_px: int = 64
    height_px: int = 64
    flip_x: bool = False
    flip_y: bool = False

    def contains(self, p: Point, motion: bool = False) -> bool:
        m = self.margin_mm if motion else 0
        return p.finite() and m <= p.x <= self.width_mm-m and m <= p.y <= self.height_mm-m


@dataclass(frozen=True)
class InteractionConfig:
    piece_id: int = 0
    initial_x_mm: float = 80
    initial_y_mm: float = 100
    selection_radius_mm: float = 22
    observation_timeout_ms: int = 500
    input_timeout_ms: int = 250
    pinch_on: float = 0.25
    pinch_off: float = 0.40
    debounce_ms: int = 60
    speed_mm_s: float = 10
    command_timeout_s: float = 90
    verification_timeout_s: float = 5
    verification_tolerance_mm: float = 5
    verification_samples: int = 3


@dataclass(frozen=True)
class RenderConfig:
    fps: int = 60
    cursor_radius_px: int = 4
    cursor_green: int = 140
    cursor_blue: int = 255
    trail_blue: int = 220
    trail_lifetime_ms: int = 1500
    trail_max_points: int = 128
    trail_min_step_mm: float = 0.25


@dataclass(frozen=True)
class NetworkConfig:
    host: str = "127.0.0.1"
    control_port: int = 7420
    frame_port: int = 7422
    heartbeat_ms: int = 400
    reconnect_ms: int = 1000
    response_timeout_ms: int = 1500


@dataclass(frozen=True)
class CameraConfig:
    index: int = 0
    width: int = 1280
    height: int = 720
    model_path: str = "../models/hand_landmarker.task"
    calibration_path: str = "local-calibration.json"
    min_confidence: float = 0.6
    april_tag_dictionary: str = "DICT_APRILTAG_36h11"
    marker_min_area_px: float = 100


@dataclass(frozen=True)
class GripperConfig:
    calibrated: bool = False
    release_angle: int | None = None
    engage_angle: int | None = None
    settle_s: float = 0.6
    pickup_tolerance_mm: float = 3


@dataclass(frozen=True)
class RuntimeConfig:
    board: BoardConfig = field(default_factory=BoardConfig)
    interaction: InteractionConfig = field(default_factory=InteractionConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    gripper: GripperConfig = field(default_factory=GripperConfig)
    base_dir: Path = Path(".")

    def path(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else self.base_dir / p


def load_config(path: str | Path) -> RuntimeConfig:
    path = Path(path).resolve()
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    sections = {"board": BoardConfig, "interaction": InteractionConfig,
                "render": RenderConfig, "network": NetworkConfig, "camera": CameraConfig,
                "gripper": GripperConfig}
    if not isinstance(raw, dict) or set(raw) - sections.keys():
        raise ValueError("Unknown configuration section")
    values = {}
    for name, cls in sections.items():
        section = raw.get(name, {})
        if not isinstance(section, dict) or set(section) - {f.name for f in fields(cls)}:
            raise ValueError(f"Unknown fields in {name}")
        defaults = cls()
        for key, value in section.items():
            expected = next(f.type for f in fields(cls) if f.name == key)
            default = getattr(defaults, key)
            valid = ((value is None or type(value) is int) if default is None else
                     type(value) is bool if expected is bool else
                     type(value) is int if expected is int else
                     type(value) in (int, float) and math.isfinite(value) if expected is float else
                     isinstance(value, str))
            if not valid:
                raise ValueError(f"Invalid type/value for {name}.{key}")
        values[name] = cls(**section)
    result = RuntimeConfig(**values, base_dir=path.parent)
    b, i, r, n, c = result.board, result.interaction, result.render, result.network, result.camera
    if not (0 < b.width_mm <= 10000 and 0 < b.height_mm <= 10000 and
            0 <= b.margin_mm < min(b.width_mm, b.height_mm)/2 and
            b.width_px == 64 and b.height_px == 64):
        raise ValueError("V0.1 requires a valid board and a 64x64 frame")
    if not (0 < i.pinch_on < i.pinch_off < 2 and i.debounce_ms >= 0 and
            0.5 <= i.speed_mm_s <= 25 and i.verification_samples >= 1 and
            i.command_timeout_s > 0 and i.verification_timeout_s > 0 and
            i.observation_timeout_ms >= 50 and i.input_timeout_ms >= 50 and
            i.selection_radius_mm > 0 and i.verification_tolerance_mm > 0 and
            b.contains(Point(i.initial_x_mm, i.initial_y_mm), motion=True)):
        raise ValueError("Invalid interaction configuration")
    if not (1 <= r.fps <= 120 and 1 <= r.cursor_radius_px <= 32 and
            r.trail_lifetime_ms > 0 and 2 <= r.trail_max_points <= 4096 and
            r.trail_min_step_mm >= 0 and all(0 <= color <= 255 for color in
            (r.cursor_green, r.cursor_blue, r.trail_blue))):
        raise ValueError("Invalid render configuration")
    if not (1 <= n.control_port <= 65535 and 1 <= n.frame_port <= 65535 and
            n.control_port != n.frame_port and 100 <= n.heartbeat_ms <= 500 and
            n.reconnect_ms >= 100 and n.response_timeout_ms >= n.heartbeat_ms*2):
        raise ValueError("Invalid network configuration")
    if not (c.index >= 0 and c.width > 0 and c.height > 0 and 0 < c.min_confidence <= 1 and c.marker_min_area_px > 0):
        raise ValueError("Invalid camera configuration")
    g = result.gripper
    if not (g.settle_s >= 0.1 and g.pickup_tolerance_mm > 0 and all(
            a is None or 0 <= a <= 180 for a in (g.release_angle, g.engage_angle))):
        raise ValueError("Invalid gripper configuration")
    if g.calibrated and (g.release_angle is None or g.engage_angle is None or g.release_angle == g.engage_angle):
        raise ValueError("Calibrated gripper requires two distinct safe angles")
    return result
