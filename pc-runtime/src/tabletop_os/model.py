"""Value objects shared by input, interaction, rendering and device adapters."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def distance(self, other: "Point") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def finite(self) -> bool:
        return math.isfinite(self.x) and math.isfinite(self.y)


@dataclass(frozen=True)
class Observation:
    piece_id: int
    position: Point
    timestamp: float


@dataclass(frozen=True)
class InputSample:
    position: Point | None
    pinch_ratio: float | None
    timestamp: float


@dataclass(frozen=True)
class MoveIntent:
    start: Point
    target: Point


@dataclass(frozen=True)
class DeviceSnapshot:
    connected: bool = False
    ready: bool = False
    referenced: bool = False
    mode: str = "DISCONNECTED"
    position: Point = Point(0, 0)
    target: Point = Point(0, 0)
    fault: str = "UNKNOWN"
    lease_valid: bool = False
    commands_enabled: bool = False
    in_flight: bool = False
    received_at: float = 0
    telemetry_age_ms: int = -1

    def can_move(self, now: float, freshness_ms: int = 500) -> bool:
        return self.can_operate_servo(now, freshness_ms) and self.referenced and self.mode == "IDLE"

    def can_operate_servo(self, now: float, freshness_ms: int = 500) -> bool:
        return (self.connected and self.ready and
                self.mode in ("IDLE", "UNREFERENCED") and self.fault == "NONE" and
                self.lease_valid and self.commands_enabled and not self.in_flight and
                self.telemetry_age_ms >= 0 and now >= self.received_at and
                self.telemetry_age_ms+(now-self.received_at)*1000 <= freshness_ms)


def snapshot_from_message(message: dict, now: float) -> DeviceSnapshot:
    a = message["arduino"]
    position = Point(float(a["actual_x_mm"]), float(a["actual_y_mm"]))
    target = Point(float(a["target_x_mm"]), float(a["target_y_mm"]))
    if not position.finite() or not target.finite():
        raise ValueError("Non-finite device position")
    return DeviceSnapshot(
        connected=bool(a["connected"]),
        ready=bool(message.get("command_id_synchronized", False)) and all(a.get(k, False) for k in
                  ("ready", "firmware_compatible", "config_confirmed", "watchdog_enabled")),
        referenced=bool(a["referenced"]), mode=str(a["mode"]),
        position=position, target=target, fault=str(a["fault"]),
        lease_valid=bool(message["control_lease_valid"]),
        commands_enabled=bool(message["motion_commands_enabled"]),
        in_flight=bool(message["motion_command_in_flight"]),
        received_at=now, telemetry_age_ms=int(a["telemetry_age_ms"]))
