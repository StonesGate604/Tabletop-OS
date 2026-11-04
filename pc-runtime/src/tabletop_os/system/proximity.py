"""Game-independent 2D proximity and placement estimation in calibrated plane mm."""
from dataclasses import dataclass
from enum import Enum
import math

from ..model import Point


class InteractionState(str, Enum):
    IDLE = 'IDLE'
    PROXIMITY = 'PROXIMITY'
    MANIPULATING = 'MANIPULATING'
    RELEASE_PENDING = 'RELEASE_PENDING'
    COMMITTED = 'COMMITTED'
    CANCELLED = 'CANCELLED'


@dataclass(frozen=True)
class ProximityConfig:
    proximity_enter_mm: float = 32
    proximity_exit_mm: float = 48
    proximity_dwell_s: float = .12
    tracking_loss_grace_s: float = .4
    tag_loss_grace_s: float = 1.5
    stability_s: float = .3
    stability_radius_mm: float = 3
    stability_samples: int = 3
    observation_timeout_s: float = .25
    release_s: float = .25

    def __post_init__(self):
        numeric = [v for k, v in vars(self).items() if k != 'stability_samples']
        if (any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in numeric)
                or self.proximity_enter_mm >= self.proximity_exit_mm
                or self.tag_loss_grace_s < self.observation_timeout_s
                or type(self.stability_samples) is not int or self.stability_samples < 2):
            raise ValueError('Invalid proximity/stability configuration; enter must be less than exit')


@dataclass(frozen=True)
class InteractionEvent:
    kind: str
    piece_id: str
    position: Point | None = None


class StablePosition:
    """Requires distinct, consecutive fresh observations; cached frames cannot settle."""

    def __init__(self, config):
        self.config = config
        self.reset()

    def reset(self):
        self.anchor = None
        self.since = self.last_stamp = None
        self.count = 0
        self.stable = False

    def update(self, observation, now):
        cfg = self.config
        if observation is None or not 0 <= now-observation.timestamp <= cfg.observation_timeout_s:
            self.reset()
            return False
        if self.last_stamp is not None and observation.timestamp <= self.last_stamp:
            return False
        if (self.anchor is None or observation.position.distance(self.anchor) > cfg.stability_radius_mm
                or observation.timestamp-self.last_stamp > cfg.observation_timeout_s):
            self.anchor, self.since, self.count = observation.position, observation.timestamp, 0
            self.stable = False
        self.last_stamp = observation.timestamp
        self.count += 1
        self.stable = (self.count >= cfg.stability_samples and
                       observation.timestamp-self.since >= cfg.stability_s)
        return self.stable


class ProximityService:
    def __init__(self, config=ProximityConfig()):
        self.config = config
        self.reset()

    def reset(self):
        self.state = InteractionState.IDLE
        self.target = None
        self._candidate = None
        self._candidate_since = 0
        self._last_hand = None
        self._away_since = None
        self._emitted_position = None
        self._loss_reported = False
        self.placement = StablePosition(self.config)

    @property
    def busy(self):
        return self.state in (InteractionState.MANIPULATING, InteractionState.RELEASE_PENDING)

    def committed(self):
        self.state = InteractionState.COMMITTED
        self.target = None
        self._candidate = None
        self.placement.reset()

    def cancelled(self):
        self.state = InteractionState.CANCELLED
        self.target = self._candidate = None
        self.placement.reset()

    def update(self, hand, visible, last, actionable, confirmed, now):
        cfg, events = self.config, []
        fresh_hand = 0 <= now-hand.timestamp <= cfg.observation_timeout_s
        position = hand.position if fresh_hand else None
        if position is not None and not position.finite():
            position = None
        if position is not None:
            self._last_hand = (position, now)
        elif self._last_hand and now-self._last_hand[1] <= cfg.tracking_loss_grace_s:
            position = self._last_hand[0]

        if self.target is not None and self.target not in actionable:
            if self.target:
                events.append(InteractionEvent('piece_interaction_cancelled', self.target))
            self.reset()
        if self.target is None:
            candidates = []
            for key in actionable:
                reference = visible.get(key) or last.get(key)
                # A hand can hide a known tag before the proximity dwell ends.
                # Keep its identity and last observed position for selection only;
                # StablePosition below still accepts live observations exclusively.
                if (position is not None and reference is not None
                        and 0 <= now-reference.timestamp <= cfg.tag_loss_grace_s):
                    candidates.append((position.distance(reference.position), key))
            nearest = min(candidates, default=(math.inf, None))
            candidate = nearest[1] if nearest[0] <= cfg.proximity_enter_mm else None
            if candidate != self._candidate:
                self._candidate, self._candidate_since = candidate, now
            if candidate and now-self._candidate_since >= cfg.proximity_dwell_s:
                self.target = candidate
                self.state = InteractionState.PROXIMITY
                self._emitted_position = None
                events.append(InteractionEvent('piece_proximity_entered', candidate))

        # Placement can be validated without a successful hand detection. It still
        # needs fresh, stable tag samples plus a configurable hand-clear interval.
        key = self.target or (next(iter(actionable)) if len(actionable) == 1 else None)
        if key is None or key not in confirmed:
            return events
        observation = visible.get(key)
        original = confirmed[key]
        trusted = last.get(key)
        reference = observation.position if observation else trusted.position if trusted else original
        near = position is not None and position.distance(reference) <= cfg.proximity_exit_mm
        if near or not fresh_hand:
            self._away_since = None
        elif self._away_since is None:
            self._away_since = now
        released = self._away_since is not None and now-self._away_since >= cfg.release_s
        if observation is None:
            self.placement.reset()
            if self.target and self.state != InteractionState.MANIPULATING:
                self.state = InteractionState.MANIPULATING
                events.append(InteractionEvent('piece_manipulation_started', key))
            if trusted and now-trusted.timestamp > cfg.tag_loss_grace_s and not self._loss_reported:
                events.append(InteractionEvent('piece_tracking_lost', key))
                self._loss_reported = True
            return events
        self._loss_reported = False
        moved = observation.position.distance(original) > cfg.stability_radius_mm
        if moved and self.state in (InteractionState.IDLE, InteractionState.PROXIMITY,
                                     InteractionState.CANCELLED, InteractionState.COMMITTED):
            self.target = key
            self.state = InteractionState.MANIPULATING
            events.append(InteractionEvent('piece_manipulation_started', key))
        if self.state == InteractionState.MANIPULATING:
            self.state = InteractionState.RELEASE_PENDING
        stable = self.placement.update(observation, now)
        changed = (self._emitted_position is None or
                   observation.position.distance(self._emitted_position) > cfg.stability_radius_mm)
        if stable and released and changed:
            self._emitted_position = observation.position
            events.append(InteractionEvent('piece_position_stabilized', key, observation.position))
        if released and not moved and self.target:
            events.extend((InteractionEvent('piece_proximity_exited', key),
                           InteractionEvent('piece_interaction_cancelled', key)))
            self.state = InteractionState.CANCELLED
            self.target = self._candidate = None
        return events
