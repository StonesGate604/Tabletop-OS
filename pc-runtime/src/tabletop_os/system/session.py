"""Single-owner system facade for application input, rendering and motion."""
from dataclasses import dataclass
import math

from ..constant_engage import ConstantEngageDrag
from ..pick_place import PickPlaceDrag
from ..device import NetworkDevice
from ..frame_protocol import FRAME_BYTES
from ..model import DeviceSnapshot, Point
from .coordinates import project_between
from .display import pointer_frame
from .tracking import TagMemory, in_display


@dataclass(frozen=True)
class MotionOptions:
    enabled: bool = False
    pick_place: bool = False
    speed_mm_s: float = 2
    max_drag_mm: float | None = None
    allow_pickup_offset: bool = False

    def __post_init__(self):
        if (type(self.enabled) is not bool or type(self.pick_place) is not bool or
                type(self.allow_pickup_offset) is not bool):
            raise ValueError('Motion mode flags must be booleans')
        if self.pick_place and (not self.enabled or self.allow_pickup_offset):
            raise ValueError('Pick-place requires motion and does not use pickup-offset bypass')
        if not math.isfinite(self.speed_mm_s) or not 0.5 <= self.speed_mm_s <= 50:
            raise ValueError('Speed must be finite and between 0.5 and 50 mm/s')
        if self.max_drag_mm is not None and (not math.isfinite(self.max_drag_mm) or self.max_drag_mm <= 0):
            raise ValueError('Maximum drag distance must be finite and positive')


@dataclass(frozen=True)
class MotionView:
    enabled: bool
    phase: str
    stage: str
    error: str
    selected_id: int | None
    target: Point | None
    device: DeviceSnapshot


class TabletopSystem:
    """Own hardware policy; applications only request actions and provide scenes."""

    def __init__(self, config, display_mapping, mechanical_mapping=None, options=MotionOptions(),
                 device=None, rotation=180, flip_x=False, hold_s=None):
        if rotation not in (0, 180) or type(flip_x) is not bool:
            raise ValueError('Invalid display orientation')
        if display_mapping is None:
            raise ValueError('Display calibration is required')
        if options.enabled:
            if mechanical_mapping is None:
                raise ValueError('Mechanical calibration is required for motion')
            if (display_mapping.width, display_mapping.height) != (mechanical_mapping.width, mechanical_mapping.height):
                raise ValueError('Mechanical and display calibrations must use the same camera crop')
        self.config = config
        self.options = options
        self.display_mapping = display_mapping
        self.mechanical_mapping = mechanical_mapping
        self.rotation = rotation
        self.flip_x = flip_x
        self.tracking = TagMemory(config.interaction, hold_s)
        self._device = device if device is not None else NetworkDevice(config.network)
        if options.enabled and options.pick_place:
            self._mover = PickPlaceDrag(config, self._device, options.speed_mm_s,
                                        options.max_drag_mm)
        elif options.enabled:
            self._mover = ConstantEngageDrag(config, self._device, options.speed_mm_s,
                                             options.max_drag_mm,
                                             allow_pickup_offset=options.allow_pickup_offset)
        else:
            self._mover = None
        self._mechanical_observations = ()
        self._error = ''
        self._started = False
        self._closed = False

    def start(self):
        if self._closed:
            raise RuntimeError('SYSTEM_CLOSED')
        if not self._started:
            self._device.start()
            self._started = True

    def update(self, observations, mechanical_observations, now):
        if self._closed:
            raise RuntimeError('SYSTEM_CLOSED')
        self.tracking.update(observations, now)
        self._mechanical_observations = tuple(mechanical_observations)
        events = self._device.poll()
        if self._mover:
            self._mover.update(events, self._mechanical_observations, now)

    def request_move(self, piece_id, target_display, now):
        try:
            if self._closed:
                raise RuntimeError('SYSTEM_CLOSED')
            if self._mover is None:
                raise RuntimeError('MOTION_DISABLED')
            if not in_display(target_display):
                raise RuntimeError('POINT_OUTSIDE_DISPLAY')
            candidates = [tag for tag in self._mechanical_observations if tag.piece_id == piece_id]
            if len(candidates) != 1:
                raise RuntimeError('UNCOVER_SELECTED_TAG_AND_CONFIRM_AGAIN')
            target = project_between(target_display, self.display_mapping, self.mechanical_mapping)
            try:
                self._mover.begin(candidates[0], target, now)
            except RuntimeError as exc:
                if str(exc) == 'PICKUP_POSITION_MISMATCH':
                    reported = self.snapshot().position
                    seen = candidates[0].position
                    raise RuntimeError(f'PICKUP_POSITION_MISMATCH motor=({reported.x:.1f},{reported.y:.1f}) '
                                       f'tag=({seen.x:.1f},{seen.y:.1f}) mm') from exc
                raise
            self._error = ''
            return self._mover.request_id
        except (ValueError, RuntimeError, OSError) as exc:
            self._error = str(exc)
            raise

    @property
    def motion_view(self):
        mover = self._mover
        target = (project_between(mover.target, self.mechanical_mapping, self.display_mapping)
                  if mover and mover.target is not None else None)
        return MotionView(enabled=mover is not None, phase=mover.phase if mover else 'DISABLED',
                          stage=getattr(mover, 'stage', mover.phase) if mover else 'DISABLED',
                          error=(mover.error if mover else '') or self._error,
                          selected_id=mover.selected_id if mover else None,
                          target=target, device=self.snapshot())

    def snapshot(self):
        return self._device.snapshot()

    def display_status(self):
        return self._device.display_status()

    def publish(self, scene):
        if self._closed:
            raise RuntimeError('SYSTEM_CLOSED')
        rgb = pointer_frame(scene.cursor, self.rotation, self.flip_x, scene.tags, scene.selected_id,
                            scene.target, scene.confirmed, scene.cached_ids)
        self._device.publish(rgb)
        return rgb

    def cancel(self, reason='USER_CANCEL'):
        if self._mover:
            if self._mover.active:
                self._mover.cancel(reason)
            else:
                self._mover.reset()
        self._error = ''

    def stop(self):
        if self._mover and self._mover.active:
            self._mover.cancel('USER_STOP')
        else:
            if self._mover:
                self._mover.phase, self._mover.error = 'ERROR', 'USER_STOP'
            try:
                self._device.request('motion.stop')
            except (RuntimeError, OSError):
                pass
        self._error = ''

    def home(self, now):
        try:
            if self._closed:
                raise RuntimeError('SYSTEM_CLOSED')
            if not self.options.pick_place or self._mover is None:
                raise RuntimeError('HOME_REQUIRES_PICK_PLACE')
            self._mover.home(now)
            self._error = ''
        except (ValueError, RuntimeError, OSError) as exc:
            self._error = str(exc)
            raise

    def reset(self):
        if self._mover:
            self._mover.reset()
        self._error = ''

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._mover:
            self._mover.cancel('WINDOW_CLOSED')
        if self._started:
            try:
                self._device.publish(bytes(FRAME_BYTES))
            finally:
                self._device.close()
        self._mechanical_observations = ()
