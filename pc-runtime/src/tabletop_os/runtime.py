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


class ApplicationRuntime:
    """Application host on the existing system facade, ticked by the PC UI loop.

    No background application loop: camera input, device events, application
    updates and frame publication each have one owner on the caller's tick.
    """

    def __init__(self, config, application_config, system, simulate=False):
        from .application import ApplicationHost

        if simulate and system.options.enabled:
            raise ValueError('Simulation cannot enable hardware motion')
        if application_config.rules.guard_mode == 'virtual' and system.options.enabled:
            raise ValueError('Virtual guard cannot enable hardware motion')
        self.config, self.system = config, system
        self.simulate = simulate
        self.host = ApplicationHost()
        self.started = self.closed = False
        self.rgb = b''
        self._configure(application_config)

    def _configure(self, application_config):
        from .apps.patrol.game import PatrolApp
        from .patrol_simulation import SimulatedPieces
        from .system.application_motion import ApplicationMotion, TablePlane
        from .system.pieces import PieceRegistry
        from .system.proximity import ProximityService, StablePosition

        self.application_config = application_config
        self.plane = TablePlane(application_config.rules.grid)
        self.registry = PieceRegistry(application_config.bindings, self.config.camera.april_tag_dictionary,
                                      application_config.proximity.observation_timeout_s)
        self.proximity = ProximityService(application_config.proximity)
        self.filters = {key: StablePosition(application_config.proximity) for key in self.registry.by_id}
        self.simulation = SimulatedPieces(application_config, self.plane) if self.simulate else None
        mode = ('virtual' if application_config.rules.guard_mode == 'virtual' else
                'simulation' if self.simulate else 'hardware' if self.system.options.enabled else 'manual')
        self.motion = ApplicationMotion(self.system, self.registry, self.plane, application_config.proximity,
                                        mode, self.simulation if mode == 'simulation' else None,
                                        application_config.motion_timeout_s, application_config.rules.virtual_piece_ids)
        self.host.load(PatrolApp(application_config.rules))
        self._confirmed_physical = self.host.app.confirmed.copy()
        self._started_at = None
        self._last_now = None
        self.hand_position = None
        self.system.tracking.clear()
        self.registry.announce()
        LOG.info('Patrol guard mode=%s; motion backend=%s', application_config.rules.guard_mode, mode)

    def start(self):
        if self.closed:
            raise RuntimeError('RUNTIME_CLOSED')
        self.system.start()
        self.host.start()
        self.started = True

    def tick_simulation(self, now):
        if not self.simulate:
            raise RuntimeError('NOT_SIMULATION')
        sample, observations = self.simulation.samples(now)
        return self.tick(sample, observations, (), now)

    def tick(self, sample, observations, mechanical_observations, now):
        import math
        from .apps.patrol.game import GamePhase
        from .apps.patrol.render import render_frame
        from .system.pieces import PieceObservation
        from .frame_protocol import FRAME_BYTES

        if not self.started or self.closed:
            raise RuntimeError('RUNTIME_NOT_RUNNING')
        if not math.isfinite(now) or self._last_now is not None and now < self._last_now:
            raise ValueError('INVALID_OR_BACKWARDS_TIME')
        self._last_now = now
        if self._started_at is None:
            self._started_at = now
        device_events = self.system.update(observations, mechanical_observations, now)
        physical = tuple(replace(tag, position=self.plane.physical(tag.position)) for tag in observations)
        hand = replace(sample, position=self.plane.physical(sample.position) if sample.position else None)
        self.registry.update(physical, now)
        app = self.host.app
        if not self.host.running:
            self.hand_position = None
            self.rgb = self.system.publish_rgb(bytes(FRAME_BYTES))
            return self.rgb
        self.hand_position = (hand.position if hand.position is not None and hand.position.finite()
                              and 0 <= now-hand.timestamp <= self.application_config.proximity.observation_timeout_s
                              else None)
        if self.registry.ambiguous:
            app.fail('DUPLICATE_VISIBLE_TAGS:'+','.join(sorted(self.registry.ambiguous)))
        fatal = next((e for e in device_events if e.get('type') in
                     ('connection.lost', 'control.lease_expired', 'motion.interlock', 'arduino.error',
                      'motion.stopped', 'motion.cancelled', 'error')), None)
        if fatal:
            app.fail(str(fatal.get('code', fatal.get('reason', fatal['type']))))
        if self.system.options.enabled and self.system.motion_view.phase == 'ERROR':
            app.fail(self.system.motion_view.error)
        if app.phase != GamePhase.SETUP:
            for key in self.registry.by_id:
                last = self.registry.last.get(key)
                if now-(last.timestamp if last else self._started_at) > self.application_config.proximity.tag_loss_grace_s:
                    # Guard may be covered while the magnet is moving; the motion
                    # service has its own bounded timeout and final verification.
                    if not (self.motion.active and self.motion.pending.piece_id == key):
                        app.fail(f'TRACKING_LOST:{key}; uncover and restart')
        placements = {}
        for key, stable_filter in self.filters.items():
            observation = self.registry.visible.get(key)
            if stable_filter.update(observation, now):
                placements[key] = PieceObservation(key, observation.position, observation.timestamp)
        events = self.proximity.update(hand, self.registry.visible, self.registry.last,
                                       app.actionable, self._confirmed_physical, now)
        result = self.motion.update(hand, now)
        old_turn = app.turn
        for request in self.host.tick(placements, events, result, now):
            self.motion.begin(request, now)
        for event in events:
            if (event.kind == 'piece_position_stabilized' and not app.needs_return
                    and app.phase != GamePhase.ERROR_RECOVERY
                    and app.rules.grid.locate(event.position) == app.positions[event.piece_id]):
                self._confirmed_physical[event.piece_id] = event.position
                if app.turn == old_turn:
                    self.proximity.cancelled()
        if app.turn != old_turn:
            self.proximity.committed()
        if app.phase == GamePhase.ERROR_RECOVERY:
            self.motion.cancel(app.error)
        self.rgb = self.system.publish_rgb(render_frame(app, self.system.rotation, self.system.flip_x,
                                                        cursor=self.hand_position))
        return self.rgb

    def pause(self):
        self.motion.cancel('APPLICATION_PAUSED')
        self.host.pause()
        self.proximity.reset()
        self.hand_position = None

    def stop(self):
        from .frame_protocol import FRAME_BYTES
        self.motion.cancel('APPLICATION_STOPPED')
        if self.system.motion_view.phase in ('HOMING', 'MOVING', 'VERIFYING'):
            self.system.cancel('APPLICATION_STOPPED')
        self.host.stop()
        self.proximity.reset()
        self.hand_position = None
        self.system.publish_rgb(bytes(FRAME_BYTES))

    def reload(self, application_config):
        """Explicit restart only. Never mutate bindings during an operation."""
        application_config.bindings.validate(self.config.camera.april_tag_dictionary)
        if application_config.rules.guard_mode == 'virtual' and self.system.options.enabled:
            raise ValueError('Virtual guard cannot enable hardware motion')
        if self.motion.active or self.proximity.busy or self.system.motion_view.phase in ('HOMING', 'MOVING', 'VERIFYING'):
            raise RuntimeError('RELOAD_BUSY: finish the interaction or stop before reloading')
        self.stop()
        self.system.reset()
        self.host.unload()
        self._configure(application_config)
        self.host.start()

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.motion.cancel('APPLICATION_CLOSED')
            self.host.unload()
        finally:
            self.system.close()
