"""Logical movement requests adapted to existing safe motion or manual placement."""
from ..application import MotionResult
from ..model import Point
from .proximity import StablePosition


class TablePlane:
    """Selected LED plane mm <-> unrotated display coordinates; never motor axes."""

    def __init__(self, board):
        self.width_mm, self.height_mm = board.width_mm, board.height_mm

    def physical(self, point):
        return Point(point.x/63*self.width_mm, point.y/95*self.height_mm)

    def display(self, point):
        return Point(point.x/self.width_mm*63, point.y/self.height_mm*95)

    def contains(self, point):
        return point.finite() and 0 <= point.x <= self.width_mm and 0 <= point.y <= self.height_mm


class ApplicationMotion:
    def __init__(self, system, registry, plane, proximity_config, mode='manual', simulation=None,
                 timeout_s=360, virtual_piece_ids=frozenset()):
        if mode not in ('manual', 'simulation', 'hardware', 'virtual'):
            raise ValueError('Unknown guard motion mode')
        if (mode == 'hardware') != system.options.enabled:
            raise ValueError('Hardware motion must be explicitly enabled in system options')
        if mode == 'hardware' and not system.options.pick_place:
            raise ValueError('Application motion requires the existing safe pick-place service')
        if (mode == 'simulation') != (simulation is not None):
            raise ValueError('Simulation motion requires simulated input')
        if mode == 'virtual' and (not virtual_piece_ids or set(virtual_piece_ids).intersection(registry.by_id)):
            raise ValueError('Virtual motion requires logical pieces without enabled physical bindings')
        self.system, self.registry, self.plane = system, registry, plane
        self.mode, self.simulation, self.timeout_s = mode, simulation, timeout_s
        self.virtual_piece_ids = frozenset(virtual_piece_ids)
        self.placement = StablePosition(proximity_config)
        self.config = proximity_config
        self.pending = None
        self.started_at = 0
        self._away_since = None
        self._result = None
        self.request_count = 0

    @property
    def active(self):
        return self.pending is not None

    def begin(self, request, now):
        if self.pending:
            raise RuntimeError('APPLICATION_MOTION_BUSY')
        allowed = self.virtual_piece_ids if self.mode == 'virtual' else self.registry.by_id
        if request.piece_id not in allowed:
            raise ValueError(f'UNREGISTERED_LOGICAL_PIECE:{request.piece_id}')
        if not self.plane.contains(request.target):
            raise ValueError('POINT_OUTSIDE_TABLE')
        self.pending, self.started_at = request, now
        self.placement.reset()
        self._away_since = self._result = None
        self.request_count += 1
        try:
            if self.mode == 'hardware':
                if self.system.motion_view.phase == 'DONE':
                    self.system.reset()
                # The only logical -> physical ID conversion at the motion boundary.
                physical_id = self.registry.by_id[request.piece_id].tag_id
                self.system.request_move(physical_id, self.plane.display(request.target), now)
            elif self.mode == 'simulation':
                self.simulation.schedule(request.piece_id, request.target, now)
        except (ValueError, RuntimeError, OSError) as exc:
            self._finish(False, str(exc))

    def update(self, hand, now):
        request = self.pending
        if request:
            if now-self.started_at > self.timeout_s:
                self.cancel('GUARD_MOTION_TIMEOUT')
            elif self.mode == 'virtual':
                # This completes a virtual rule action, not a physical motion.
                # No fabricated observation or hardware-completed event is used.
                self._finish(True)
            elif self.mode == 'hardware' and self.system.motion_view.phase == 'ERROR':
                self._finish(False, self.system.motion_view.error or 'GUARD_MOTION_FAILED')
            else:
                observation = self.registry.visible.get(request.piece_id)
                if observation and observation.timestamp <= self.started_at:
                    observation = None
                stable = self.placement.update(observation, now)
                at_target = (observation is not None and
                             observation.position.distance(request.target) <= self.config.stability_radius_mm)
                fresh_hand = 0 <= now-hand.timestamp <= self.config.observation_timeout_s
                clear = (fresh_hand and (hand.position is None or
                         hand.position.distance(request.target) > self.config.proximity_exit_mm))
                if clear:
                    if self._away_since is None:
                        self._away_since = now
                else:
                    self._away_since = None
                released = self._away_since is not None and now-self._away_since >= self.config.release_s
                hardware_done = self.mode != 'hardware' or self.system.motion_view.phase == 'DONE'
                if stable and at_target and released and hardware_done:
                    self._finish(True)
        result, self._result = self._result, None
        return result

    def _finish(self, success, error=''):
        if self.pending:
            self._result = MotionResult(self.pending.token, success, error)
        self.pending = None
        self.placement.reset()

    def cancel(self, reason='APPLICATION_STOPPED'):
        if self.pending:
            if self.mode == 'hardware':
                self.system.cancel(reason)
            if self.simulation:
                self.simulation.cancel()
            self._finish(False, reason)
