"""Single-owner runtime orchestration, usable by Tk and deterministic tests."""
from dataclasses import replace
import logging
from .config import RuntimeConfig
from .device import NetworkDevice
from .gestures import PinchDetector
from .interaction import DemoController, Phase
from .model import InputSample, Observation
from .render import Compositor
from .sequence import MoveSequence
from .simulation import SimulationDevice

LOG = logging.getLogger(__name__)


class Runtime:
    def __init__(self, config: RuntimeConfig, simulate: bool, mouse: bool = False, device=None):
        if simulate:
            # Virtual angles only. Never written to a hardware configuration.
            config = replace(config, gripper=replace(config.gripper, calibrated=True,
                                                     release_angle=60, engage_angle=120))
        self.config = config
        self.simulate = simulate
        self.device = device or (SimulationDevice(config) if simulate else NetworkDevice(config.network))
        self.controller = DemoController(config)
        i = config.interaction
        self.pinch = PinchDetector(i.pinch_on, i.pinch_off, 0 if mouse else i.debounce_ms)
        self.compositor = Compositor(config)
        self.sequence = MoveSequence(config, self.controller, self.device)
        self.last_phase = self.controller.phase
        self.rgb = b""
        self.last_input_stamp = -1.0
        self.home_stage = ""
        self.home_request = None
        self.home_deadline = 0.0
        self.home_settle_until = 0.0

    def start(self):
        self.device.start()

    def tick(self, sample: InputSample, observations: tuple[Observation, ...], now: float) -> bytes:
        if self.simulate:
            self.device.tick(now)
            observation = self.device.observation(now)
            observations = (observation,) if observation else ()
        events = self.device.poll()
        status = self.device.snapshot()
        for observation in observations:
            self.controller.observe(observation, now)
        try:
            self.sequence.update(events, status, now)
        except (RuntimeError, ValueError) as exc:
            self.sequence.abort(str(exc))
        self._home_events(events, status, now)
        if self.sequence.stage == "IDLE" and not self.home_stage:
            for message in events:
                if message.get("type") in ("error", "arduino.error", "motion.interlock"):
                    self.controller.fail(str(message.get("code", message.get("reason", "DEVICE_ERROR"))))
        if not (0 <= now-sample.timestamp <= self.config.interaction.input_timeout_ms/1000):
            sample = InputSample(None, None, now)
        event = None
        if sample.timestamp > self.last_input_stamp:
            self.last_input_stamp = sample.timestamp
            event = self.pinch.update(sample.pinch_ratio if sample.position and self.config.board.contains(sample.position) else None, now)
        if not self.home_stage:
            self.controller.input(sample.position, event, now)
        intent = self.controller.take_intent()
        if intent:
            try:
                self.sequence.begin(intent, status, now)
            except (RuntimeError, ValueError) as exc:
                self.controller.fail(str(exc))
        self.controller.tick(now)
        if self.controller.phase == Phase.ERROR and self.sequence.stage != "IDLE":
            self.sequence.abort(self.controller.error)
        self.compositor.record(status, now, self.sequence.dragging)
        self.rgb = self.compositor.render(self.controller, now, self.pinch.pinched)
        self.device.publish(self.rgb)
        if self.controller.phase != self.last_phase:
            LOG.info("Interaction %s -> %s; sequence=%s error=%s", self.last_phase.value,
                     self.controller.phase.value, self.sequence.stage, self.controller.error)
            self.last_phase = self.controller.phase
        return self.rgb

    def stop(self):
        self.home_stage = ""
        self.home_request = None
        self.sequence.abort("USER_STOP")

    def reset(self):
        if self.sequence.stage != "IDLE" or self.home_stage:
            self.stop()
        else:
            self.controller.reset()
            self.pinch.update(None, 0)

    def home(self, now: float):
        if not self.config.gripper.calibrated:
            self.controller.fail("GRIPPER_NOT_CALIBRATED")
            return
        if self.sequence.stage != "IDLE" or self.home_stage:
            return
        try:
            if not self.device.snapshot().can_operate_servo(now):
                raise RuntimeError("DEVICE_NOT_READY")
            self.controller.reset()
            self.pinch.update(None, now)
            self.home_request = self.device.request("actuator.servo", angle=self.config.gripper.release_angle)
            self.home_stage = "HOME_RELEASE"
            self.home_deadline = now+self.config.interaction.command_timeout_s
        except RuntimeError as exc:
            self.controller.fail(str(exc))

    def _home_events(self, events, status, now):
        if not self.home_stage:
            return
        for message in events:
            kind = message.get("type")
            if (kind in ("connection.lost", "control.lease_expired", "motion.interlock", "motion.stopped", "arduino.error") or
                (message.get("reply_to") == self.home_request and kind == "error")):
                self.stop()
                self.controller.fail(str(message.get("code", kind)))
                return
            if message.get("reply_to") != self.home_request:
                continue
            if kind == "actuator.completed" and self.home_stage == "HOME_RELEASE":
                self.home_stage = "HOME_SETTLE"
                self.home_settle_until = now+self.config.gripper.settle_s
            elif kind == "motion.completed" and self.home_stage == "HOMING":
                self.home_stage = ""
                self.home_request = None
                self.pinch.update(None, now)
                return
        if now > self.home_deadline:
            self.stop()
            self.controller.fail("HOME_TIMEOUT")
        elif self.home_stage == "HOME_SETTLE" and now >= self.home_settle_until and status.can_operate_servo(now):
            try:
                self.home_request = self.device.request("motion.home")
                self.home_stage = "HOMING"
            except RuntimeError as exc:
                self.stop()
                self.controller.fail(str(exc))

    def close(self):
        self.device.close()
