"""A deterministic virtual actuator and independently observed magnetic piece."""
import math
import uuid
from .config import RuntimeConfig
from .model import Point, Observation, DeviceSnapshot


class SimulationDevice:
    def __init__(self, config: RuntimeConfig):
        self.config = config
        self.position = Point(config.interaction.initial_x_mm, config.interaction.initial_y_mm)
        self.piece = self.position
        self.target = self.position
        self.mode = "IDLE"
        self.events = []
        self.active = None
        self.command_id = 0
        self.engaged = False
        self.attached = False
        self.slip = False
        self.tag_visible = True
        self.now = 0.0
        self.last_tick = None
        self.rgb = b""
        self.move_count = 0
        self.history = []

    def start(self):
        pass

    def request(self, kind: str, **fields) -> str:
        request = uuid.uuid4().hex
        self.history.append((kind, dict(fields)))
        if kind in ("motion.move_to", "motion.home"):
            if self.mode != "IDLE":
                raise RuntimeError("BUSY")
            target = Point(fields["x_mm"], fields["y_mm"]) if kind == "motion.move_to" else Point(2, 2)
            if not self.config.board.contains(target, motion=True):
                raise RuntimeError("RANGE")
            self.command_id += 1
            self.target, self.mode, self.active = target, "TASK_MOVE", request
            self.speed = fields.get("speed_mm_s", 10)
            self.move_count += 1
            for event in ("motion.accepted", "motion.started"):
                self.events.append({"v": 1, "type": event, "reply_to": request, "command_id": self.command_id})
        elif kind == "actuator.servo":
            self.engaged = fields["angle"] == self.config.gripper.engage_angle
            self.attached = self.engaged and self.position.distance(self.piece) <= self.config.gripper.pickup_tolerance_mm
            self.events.append({"v": 1, "type": "actuator.completed", "reply_to": request, "angle": fields["angle"]})
        elif kind in ("motion.stop", "actuator.servo_off"):
            self.target = self.position
            self.mode = "IDLE"
            self.engaged = self.attached = False
            self.events.append({"v": 1, "type": "motion.stopped", "reason": "USER"})
        else:
            raise ValueError(f"Unsupported simulation command {kind}")
        return request

    def tick(self, now: float):
        elapsed = 0 if self.last_tick is None else max(0, now-self.last_tick)
        self.now, self.last_tick = now, now
        if self.mode == "TASK_MOVE":
            distance = self.position.distance(self.target)
            fraction = min(1, self.speed*elapsed/distance) if distance else 1
            self.position = Point(self.position.x+(self.target.x-self.position.x)*fraction,
                                  self.position.y+(self.target.y-self.position.y)*fraction)
            if self.attached and not self.slip:
                self.piece = self.position
            if fraction >= 1:
                self.mode = "IDLE"
                self.events.append({"v": 1, "type": "motion.completed", "reply_to": self.active,
                                    "command_id": self.command_id})
                self.active = None

    def observation(self, now: float) -> Observation | None:
        return Observation(self.config.interaction.piece_id, self.piece, now) if self.tag_visible else None

    def snapshot(self) -> DeviceSnapshot:
        return DeviceSnapshot(True, True, True, self.mode, self.position, self.target, "NONE", True,
                              True, self.active is not None, self.now, 0)

    def poll(self) -> list[dict]:
        events, self.events = self.events, []
        return events

    def publish(self, rgb: bytes):
        self.rgb = rgb

    def close(self):
        self.mode = "DISCONNECTED"
        self.engaged = self.attached = False
