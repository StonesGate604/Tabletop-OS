"""Servo release -> seek -> engage -> drag -> release; await every event."""
from .config import RuntimeConfig
from .interaction import DemoController
from .model import DeviceSnapshot, MoveIntent


class MoveSequence:
    def __init__(self, config: RuntimeConfig, controller: DemoController, device):
        self.config, self.controller, self.device = config, controller, device
        self.stage = "IDLE"
        self.request_id = None
        self.intent = None
        self.settle_until = 0.0
        self.deadline = 0.0
        self.logical_id = ""

    @property
    def dragging(self) -> bool:
        return self.stage == "DRAG"

    def begin(self, intent: MoveIntent, status: DeviceSnapshot, now: float):
        if self.stage != "IDLE":
            raise RuntimeError("SEQUENCE_BUSY")
        if not self.config.gripper.calibrated:
            raise RuntimeError("GRIPPER_NOT_CALIBRATED")
        if not status.can_move(now):
            raise RuntimeError("DEVICE_NOT_READY")
        self.intent = intent
        self.stage = "RELEASE"
        self.deadline = now + self.config.interaction.command_timeout_s
        self.logical_id = f"action-{int(now*1000000)}"
        self.controller.submitted(self.logical_id)
        self._dispatch(status, now)

    def update(self, events: list[dict], status: DeviceSnapshot, now: float):
        if self.stage == "IDLE":
            return
        for message in events:
            kind = message.get("type")
            if kind in ("connection.lost", "control.lease_expired", "motion.interlock", "motion.stopped", "motion.cancelled"):
                self.abort(str(message.get("reason", kind)))
                return
            matches = message.get("reply_to") == self.request_id and self.request_id is not None
            if kind in ("error", "arduino.error") and (matches or kind == "arduino.error"):
                self.abort(str(message.get("code", "DEVICE_ERROR")))
                return
            if not matches:
                continue
            if kind == "motion.started" and self.stage == "DRAG":
                self.controller.event({"type": kind, "reply_to": self.logical_id,
                                       "command_id": message.get("command_id")}, now)
            if kind == "actuator.completed" and self.stage in ("RELEASE", "ENGAGE", "RELEASE_FINAL"):
                self.request_id = None
                self.settle_until = now+self.config.gripper.settle_s
                self.stage = {"RELEASE": "SETTLE_RELEASE", "ENGAGE": "SETTLE_ENGAGE",
                              "RELEASE_FINAL": "SETTLE_FINAL"}[self.stage]
            elif kind == "motion.completed" and self.stage in ("SEEK", "DRAG"):
                self.request_id = None
                self.stage = "ENGAGE" if self.stage == "SEEK" else "RELEASE_FINAL"
        if self.stage == "IDLE":
            return
        if not status.connected or status.fault != "NONE" or now > self.deadline:
            self.abort("SEQUENCE_TIMEOUT" if now > self.deadline else "DEVICE_UNSAFE")
            return
        if self.stage.startswith("SETTLE_") and now >= self.settle_until:
            if self.stage == "SETTLE_FINAL":
                self.stage = "IDLE"
                self.controller.event({"type": "motion.completed", "reply_to": self.logical_id}, now)
                return
            self.stage = "SEEK" if self.stage == "SETTLE_RELEASE" else "DRAG"
        self._dispatch(status, now)

    def _dispatch(self, status: DeviceSnapshot, now: float):
        if self.request_id is not None or not status.can_move(now) or self.intent is None:
            return
        g = self.config.gripper
        if self.stage in ("RELEASE", "RELEASE_FINAL", "ENGAGE"):
            if self.stage == "ENGAGE":
                observation = self.controller.observation
                if (observation is None or now-observation.timestamp > self.config.interaction.observation_timeout_ms/1000 or
                        observation.position.distance(self.intent.start) > g.pickup_tolerance_mm):
                    self.abort("PIECE_MOVED_OR_NOT_OBSERVED_AT_PICKUP")
                    return
            angle = g.engage_angle if self.stage == "ENGAGE" else g.release_angle
            self.request_id = self.device.request("actuator.servo", angle=angle)
        elif self.stage in ("SEEK", "DRAG"):
            target = self.intent.start if self.stage == "SEEK" else self.intent.target
            if self.stage == "DRAG" and status.position.distance(self.intent.start) > g.pickup_tolerance_mm:
                self.abort("PICKUP_POSITION_MISMATCH")
                return
            self.request_id = self.device.request("motion.move_to", x_mm=target.x, y_mm=target.y,
                                                  speed_mm_s=self.config.interaction.speed_mm_s)

    def abort(self, reason: str):
        self.stage = "IDLE"
        self.request_id = None
        self.controller.fail(reason)
        try:
            self.device.request("motion.stop")
        except RuntimeError:
            pass
