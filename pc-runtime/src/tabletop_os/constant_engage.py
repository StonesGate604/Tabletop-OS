"""One drag with a physically engaged magnet; no servo, seek or home.

The caller supplies fresh observations in mechanical millimetres, independently
of its display cache. A completed motor task still needs visual verification.
"""
import math

from .model import Observation, Point


class ConstantEngageDrag:
    def __init__(self, config, device, speed_mm_s=2, max_drag_mm=None, allow_pickup_offset=False):
        if not math.isfinite(speed_mm_s) or not 0.5 <= speed_mm_s <= 25:
            raise ValueError('Speed must be finite and between 0.5 and 25 mm/s')
        if max_drag_mm is not None and (not math.isfinite(max_drag_mm) or max_drag_mm <= 0):
            raise ValueError('Maximum drag distance must be finite and positive')
        self.config = config
        self.device = device
        self.speed_mm_s = speed_mm_s
        self.max_drag_mm = max_drag_mm
        self.allow_pickup_offset = allow_pickup_offset
        self.phase = 'IDLE'
        self.error = ''
        self.selected_id = None
        self.target = None
        self.request_id = None
        self.verified_samples = 0
        self._deadline = 0.0
        self._completed_at = 0.0
        self._last_sample_at = 0.0
        self._last_verified_at = 0.0

    @property
    def active(self):
        return self.phase in ('MOVING', 'VERIFYING')

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
        if not self.allow_pickup_offset and status.position.distance(observation.position) > self.config.gripper.pickup_tolerance_mm:
            raise RuntimeError('PICKUP_POSITION_MISMATCH')
        drag_distance = max(status.position.distance(target), observation.position.distance(target))
        if self.max_drag_mm is not None and drag_distance > self.max_drag_mm:
            raise RuntimeError('DRAG_DISTANCE_EXCEEDED')
        self.selected_id = observation.piece_id
        self.target = target
        self.error = ''
        self.verified_samples = 0
        self._last_sample_at = self._last_verified_at = 0.0
        self._deadline = now+max(cfg.command_timeout_s,status.position.distance(target)/self.speed_mm_s+10)
        self.phase = 'MOVING'
        try:
            self.request_id = self.device.request('motion.move_to', x_mm=target.x,
                                                  y_mm=target.y, speed_mm_s=self.speed_mm_s)
        except (RuntimeError, OSError):
            # A delivery failure is ambiguous; do not replay it on reconnect.
            self.cancel('MOTION_SEND_FAILED')
            raise

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
            if matches and kind == 'motion.completed' and self.phase == 'MOVING':
                self.phase = 'VERIFYING'
                self._completed_at = self._last_sample_at = now
                self._deadline = now+self.config.interaction.verification_timeout_s
                self.verified_samples = 0
                self._last_verified_at = 0.0
        status = self.device.snapshot()
        if not status.connected:
            self.cancel('DEVICE_DISCONNECTED')
            return
        if status.fault != 'NONE':
            self.cancel('DEVICE_FAULT:'+status.fault)
            return
        fresh = (status.telemetry_age_ms >= 0 and now >= status.received_at and
                 status.telemetry_age_ms+(now-status.received_at)*1000 <= 500)
        if not fresh:
            self.cancel('TELEMETRY_STALE')
            return
        if not (status.ready and status.referenced and status.lease_valid and
                status.commands_enabled and status.mode in ('IDLE', 'TASK_MOVE')):
            self.cancel('DEVICE_NOT_READY')
            return
        if now > self._deadline:
            self.cancel('VERIFICATION_TIMEOUT' if self.phase == 'VERIFYING' else 'MOTION_TIMEOUT')
            return
        if self.phase == 'VERIFYING':
            self._verify(observations, now)

    def _verify(self, observations, now):
        cfg = self.config.interaction
        timeout = cfg.observation_timeout_ms/1000
        selected = [tag for tag in observations if tag.piece_id == self.selected_id]
        if len(selected) != 1:
            if len(selected) > 1 or (self._last_verified_at and now-self._last_verified_at > timeout):
                self.verified_samples = 0
                self._last_verified_at = 0.0
            return
        tag = selected[0]
        if not 0 <= now-tag.timestamp <= timeout:
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

    def cancel(self, reason='USER_STOP'):
        if not self.active:
            return
        self.phase = 'ERROR'
        self.error = reason
        try:
            self.device.request('motion.stop')
        except (RuntimeError, OSError):
            pass

    def reset(self):
        if self.active:
            raise RuntimeError('MOTION_BUSY')
        self.phase = 'IDLE'
        self.error = ''
        self.selected_id = self.target = self.request_id = None
        self.verified_samples = 0
        self._deadline = self._completed_at = self._last_sample_at = self._last_verified_at = 0.0
