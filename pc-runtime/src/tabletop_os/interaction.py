"""Single-piece two-pinch interaction; only observations commit piece state."""
from enum import Enum
from .config import RuntimeConfig
from .model import Point, Observation, MoveIntent


class Phase(str, Enum):
    IDLE = "IDLE"
    SELECTED = "SELECTED"
    AIMING = "AIMING"
    COMMITTED = "COMMITTED"
    MOVING = "MOVING"
    VERIFYING = "VERIFYING"
    ERROR = "ERROR"


class DemoController:
    def __init__(self, config: RuntimeConfig):
        self.config = config
        self.phase = Phase.IDLE
        self.piece = Point(config.interaction.initial_x_mm, config.interaction.initial_y_mm)
        self.observation: Observation | None = None
        self.cursor: Point | None = None
        self.target: Point | None = None
        self.intent: MoveIntent | None = None
        self.request_id: str | None = None
        self.command_id: int | None = None
        self.error = ""
        self.deadline = 0.0
        self.verification_since = 0.0
        self.verified_samples = 0
        self.last_verified_at = 0.0
        self.completed_moves = 0

    def observe(self, observation: Observation, now: float):
        cfg = self.config.interaction
        if (observation.piece_id != cfg.piece_id or not self.config.board.contains(observation.position) or
                observation.timestamp > now or now-observation.timestamp > cfg.observation_timeout_ms/1000 or
                (self.observation and observation.timestamp <= self.observation.timestamp)):
            return
        self.observation = observation
        if self.phase == Phase.IDLE:
            self.piece = observation.position
        elif self.phase == Phase.VERIFYING and observation.timestamp > self.verification_since:
            if self.last_verified_at and observation.timestamp-self.last_verified_at > cfg.observation_timeout_ms/1000:
                self.verified_samples = 0
            if self.target and observation.position.distance(self.target) <= cfg.verification_tolerance_mm:
                self.verified_samples += 1
                self.last_verified_at = observation.timestamp
                if self.verified_samples >= cfg.verification_samples:
                    self.piece = observation.position
                    self.completed_moves += 1
                    self._idle()
            else:
                self.verified_samples = 0

    def input(self, position: Point | None, event: str | None, now: float):
        self.cursor = position if position and self.config.board.contains(position) else None
        if self.phase not in (Phase.IDLE, Phase.SELECTED, Phase.AIMING):
            return
        if self.cursor is None or event == "lost":
            if self.phase != Phase.IDLE:
                self._idle()
            return
        if self.phase == Phase.IDLE and event == "begin":
            cfg = self.config.interaction
            if (self.observation and 0 <= now-self.observation.timestamp <= cfg.observation_timeout_ms/1000 and
                    self.cursor.distance(self.observation.position) <= cfg.selection_radius_mm):
                self.piece = self.observation.position
                self.target = self.cursor
                self.phase = Phase.SELECTED
        elif self.phase == Phase.SELECTED and event == "end":
            self.phase = Phase.AIMING
            self.target = self.cursor
        elif self.phase == Phase.AIMING:
            self.target = self.cursor
            if event == "begin" and self.config.board.contains(self.target, motion=True):
                self.intent = MoveIntent(self.piece, self.target)
                self.phase = Phase.COMMITTED
                self.deadline = now + self.config.interaction.command_timeout_s

    def take_intent(self) -> MoveIntent | None:
        value, self.intent = self.intent, None
        return value

    def submitted(self, request_id: str):
        self.request_id = request_id

    def event(self, message: dict, now: float):
        if self.phase not in (Phase.COMMITTED, Phase.MOVING):
            return
        kind = message.get("type")
        matches = (self.request_id is not None and message.get("reply_to") == self.request_id) or (
            self.command_id is not None and message.get("command_id") == self.command_id)
        if kind in ("error", "arduino.error") and matches:
            self.fail(str(message.get("code", "DEVICE_ERROR")))
        elif matches and kind in ("motion.accepted", "motion.started"):
            self.command_id = message.get("command_id", self.command_id)
            if kind == "motion.started":
                self.phase = Phase.MOVING
        elif matches and kind == "motion.completed":
            self.phase = Phase.VERIFYING
            self.verification_since = now
            self.deadline = now + self.config.interaction.verification_timeout_s
            self.verified_samples = 0
        elif kind in ("motion.cancelled", "motion.stopped", "control.lease_expired", "motion.interlock", "connection.lost"):
            self.fail(str(message.get("reason", kind)))

    def tick(self, now: float):
        if self.phase in (Phase.SELECTED, Phase.AIMING) and (self.observation is None or
                now-self.observation.timestamp > self.config.interaction.observation_timeout_ms/1000):
            self._idle()
        if self.phase == Phase.VERIFYING and self.last_verified_at and (
                now-self.last_verified_at > self.config.interaction.observation_timeout_ms/1000):
            self.verified_samples = 0
        if self.phase in (Phase.COMMITTED, Phase.MOVING, Phase.VERIFYING) and now > self.deadline:
            self.fail("VERIFICATION_TIMEOUT" if self.phase == Phase.VERIFYING else "MOTION_TIMEOUT")

    def fail(self, reason: str):
        self.phase = Phase.ERROR
        self.error = reason
        self.intent = None

    def reset(self):
        # An explicit user action is required; a failed task is never replayed.
        self._idle()

    def _idle(self):
        self.phase = Phase.IDLE
        self.target = self.intent = self.request_id = self.command_id = None
        self.verified_samples = 0
        self.last_verified_at = 0
        self.error = ""
