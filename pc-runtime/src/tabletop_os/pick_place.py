"""Calibrated release/seek/engage/drag/release sequence for the gesture app.

Display pixels and mechanical millimetres are converted by ``TabletopSystem``
through their shared camera plane.  This class owns only actuator sequencing,
safety gates and observed-result verification in mechanical coordinates.
"""
import math

from .model import Observation, Point


class PickPlaceDrag:
    def __init__(self, config, device, speed_mm_s=2, max_drag_mm=None):
        if not math.isfinite(speed_mm_s) or not 0.5 <= speed_mm_s <= 50:
            raise ValueError('Speed must be finite and between 0.5 and 50 mm/s')
        if max_drag_mm is not None and (not math.isfinite(max_drag_mm) or max_drag_mm <= 0):
            raise ValueError('Maximum drag distance must be finite and positive')
        if not config.gripper.calibrated:
            raise ValueError('GRIPPER_NOT_CALIBRATED')
        self.config = config
        self.device = device
        self.speed_mm_s = speed_mm_s
        self.max_drag_mm = max_drag_mm
        self.phase = 'IDLE'
        self.stage = 'IDLE'
        self.error = ''
        self.selected_id = None
        self.start = None
        self.target = None
        self.request_id = None
        self.verified_samples = 0
        self._deadline = 0.0
        self._settle_until = 0.0
        self._completed_at = 0.0
        self._last_sample_at = 0.0
        self._last_verified_at = 0.0

    @property
    def active(self):
        return self.phase in ('HOMING', 'MOVING', 'VERIFYING')

    def begin(self, observation: Observation, target: Point, now: float):
        if self.phase != 'IDLE':
            raise RuntimeError('MOTION_BUSY' if self.active else 'RESET_REQUIRED')
        cfg = self.config.interaction
        if not math.isfinite(now) or not 0 <= now-observation.timestamp <= cfg.observation_timeout_ms/1000:
            raise RuntimeError('PIECE_OBSERVATION_STALE')
        if not (self.config.board.contains(observation.position, motion=True)
                and self.config.board.contains(target, motion=True)):
            raise RuntimeError('POINT_OUTSIDE_WORKSPACE')
        status = self.device.snapshot()
        if not status.can_move(now) or not status.position.finite():
            raise RuntimeError('DEVICE_NOT_READY')
        if self.max_drag_mm is not None and observation.position.distance(target) > self.max_drag_mm:
            raise RuntimeError('DRAG_DISTANCE_EXCEEDED')
        self.selected_id = observation.piece_id
        self.start = observation.position
        self.target = target
        self.error = ''
        self.verified_samples = 0
        self._last_sample_at = self._last_verified_at = 0.0
        travel = status.position.distance(self.start)+self.start.distance(target)
        self._deadline = now+max(cfg.command_timeout_s,
                                 travel/self.speed_mm_s+4*self.config.gripper.settle_s+20)
        self.phase = 'MOVING'
        self.stage = 'RELEASE'
        self._dispatch(status, (observation,), now)

    def home(self, now: float):
        if self.phase != 'IDLE':
            raise RuntimeError('MOTION_BUSY' if self.active else 'RESET_REQUIRED')
        status = self.device.snapshot()
        if not math.isfinite(now) or not status.can_operate_servo(now):
            raise RuntimeError('DEVICE_NOT_READY')
        self.selected_id = self.start = self.target = None
        self.error = ''
        self.phase = 'HOMING'
        self.stage = 'HOME_RELEASE'
        self._deadline = now+self.config.interaction.command_timeout_s
        self._dispatch(status, (), now)

    def update(self, events, observations, now):
        if not self.active:
            return
        if not math.isfinite(now):
            self.cancel('INVALID_TIME')
            return
        for message in events:
            kind = message.get('type')
            if kind in ('connection.lost', 'control.lease_expired', 'motion.interlock',
                        'motion.stopped', 'motion.cancelled', 'arduino.error'):
                self.cancel(str(message.get('code', message.get('reason', kind))))
                return
            matches = self.request_id is not None and message.get('reply_to') == self.request_id
            if kind == 'error' and (matches or not message.get('reply_to')):
                self.cancel(str(message.get('code', 'DEVICE_ERROR')))
                return
            if not matches:
                continue
            if kind == 'actuator.completed':
                settle = {
                    'HOME_RELEASE': 'SETTLE_HOME',
                    'RELEASE': 'SETTLE_RELEASE',
                    'ENGAGE': 'SETTLE_ENGAGE',
                    'RELEASE_FINAL': 'SETTLE_FINAL',
                }.get(self.stage)
                if settle:
                    self.request_id = None
                    self.stage = settle
                    self._settle_until = now+self.config.gripper.settle_s
            elif kind == 'motion.completed':
                if self.stage == 'HOMING':
                    self._idle()
                    return
                next_stage = {'SEEK': 'ENGAGE', 'DRAG': 'RELEASE_FINAL'}.get(self.stage)
                if next_stage:
                    self.request_id = None
                    self.stage = next_stage

        status = self.device.snapshot()
        if not self._healthy(status, now):
            return
        if now > self._deadline:
            self.cancel('VERIFICATION_TIMEOUT' if self.phase == 'VERIFYING' else
                        'HOME_TIMEOUT' if self.phase == 'HOMING' else 'SEQUENCE_TIMEOUT')
            return
        if self.stage.startswith('SETTLE_') and now >= self._settle_until:
            if self.stage == 'SETTLE_HOME':
                self.stage = 'HOMING'
            elif self.stage == 'SETTLE_RELEASE':
                self.stage = 'SEEK'
            elif self.stage == 'SETTLE_ENGAGE':
                self.stage = 'DRAG'
            elif self.stage == 'SETTLE_FINAL':
                self.stage = 'VERIFYING'
                self.phase = 'VERIFYING'
                self._completed_at = self._last_sample_at = now
                self._last_verified_at = 0.0
                self.verified_samples = 0
                self._deadline = now+self.config.interaction.verification_timeout_s
        if self.phase == 'VERIFYING':
            self._verify(observations, now)
        else:
            self._dispatch(status, observations, now)

    def _healthy(self, status, now):
        if not status.connected:
            self.cancel('DEVICE_DISCONNECTED')
            return False
        if status.fault != 'NONE':
            self.cancel('DEVICE_FAULT:'+status.fault)
            return False
        fresh = (status.telemetry_age_ms >= 0 and now >= status.received_at and
                 status.telemetry_age_ms+(now-status.received_at)*1000 <= 500)
        allowed_modes = ('IDLE', 'UNREFERENCED', 'HOMING') if self.phase == 'HOMING' else ('IDLE', 'TASK_MOVE')
        if not (fresh and status.ready and status.lease_valid and status.commands_enabled
                and status.mode in allowed_modes and (self.phase == 'HOMING' or status.referenced)):
            self.cancel('DEVICE_NOT_READY')
            return False
        return True

    def _dispatch(self, status, observations, now):
        if self.request_id is not None:
            return
        servo_stage = self.stage in ('HOME_RELEASE', 'RELEASE', 'ENGAGE', 'RELEASE_FINAL')
        if servo_stage:
            ready = status.can_operate_servo(now)
        else:
            ready = status.can_operate_servo(now) if self.stage == 'HOMING' else status.can_move(now)
        if not ready:
            return
        try:
            if self.stage in ('HOME_RELEASE', 'RELEASE', 'RELEASE_FINAL'):
                self.request_id = self.device.request(
                    'actuator.servo', angle=self.config.gripper.release_angle)
            elif self.stage == 'ENGAGE':
                tag = self._fresh_selected(observations, now)
                if tag is None or tag.position.distance(self.start) > self.config.gripper.pickup_tolerance_mm:
                    self.cancel('PIECE_MOVED_OR_NOT_OBSERVED_AT_PICKUP')
                    return
                self.request_id = self.device.request(
                    'actuator.servo', angle=self.config.gripper.engage_angle)
            elif self.stage == 'HOMING':
                self.request_id = self.device.request('motion.home')
            elif self.stage in ('SEEK', 'DRAG'):
                target = self.start if self.stage == 'SEEK' else self.target
                if self.stage == 'DRAG' and status.position.distance(self.start) > self.config.gripper.pickup_tolerance_mm:
                    self.cancel('PICKUP_POSITION_MISMATCH')
                    return
                self.request_id = self.device.request(
                    'motion.move_to', x_mm=target.x, y_mm=target.y,
                    speed_mm_s=self.speed_mm_s)
        except (RuntimeError, OSError):
            self.cancel('COMMAND_SEND_FAILED')
            raise

    def _fresh_selected(self, observations, now):
        timeout = self.config.interaction.observation_timeout_ms/1000
        selected = [tag for tag in observations if tag.piece_id == self.selected_id]
        if len(selected) != 1 or not 0 <= now-selected[0].timestamp <= timeout:
            return None
        return selected[0]

    def _verify(self, observations, now):
        cfg = self.config.interaction
        timeout = cfg.observation_timeout_ms/1000
        tag = self._fresh_selected(observations, now)
        if tag is None:
            if self._last_verified_at and now-self._last_verified_at > timeout:
                self.verified_samples = 0
                self._last_verified_at = 0.0
            return
        if tag.timestamp <= self._completed_at or tag.timestamp <= self._last_sample_at:
            return
        self._last_sample_at = tag.timestamp
        if not tag.position.finite() or tag.position.distance(self.target) > cfg.verification_tolerance_mm:
            self.verified_samples = 0
            self._last_verified_at = 0.0
            return
        if self._last_verified_at and tag.timestamp-self._last_verified_at > timeout:
            self.verified_samples = 0
        self.verified_samples += 1
        self._last_verified_at = tag.timestamp
        if self.verified_samples >= cfg.verification_samples:
            self.phase = 'DONE'
            self.stage = 'DONE'

    def cancel(self, reason='USER_STOP'):
        if not self.active:
            return
        self.phase = 'ERROR'
        self.stage = 'ERROR'
        self.error = reason
        self.request_id = None
        try:
            self.device.request('motion.stop')
        except (RuntimeError, OSError):
            pass

    def reset(self):
        if self.active:
            raise RuntimeError('MOTION_BUSY')
        self._idle()

    def _idle(self):
        self.phase = self.stage = 'IDLE'
        self.error = ''
        self.selected_id = self.start = self.target = self.request_id = None
        self.verified_samples = 0
        self._deadline = self._settle_until = self._completed_at = 0.0
        self._last_sample_at = self._last_verified_at = 0.0
