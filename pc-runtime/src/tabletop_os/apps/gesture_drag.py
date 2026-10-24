"""Two-pinch test application: select a piece, preview a target, submit once.

The application sees observations and calls the platform's movement service.
Camera ownership, tag memory, coordinate conversion and device I/O belong to
the system layer.
"""
from dataclasses import dataclass

from ..gestures import PinchDetector
from ..model import InputSample, Observation, Point
from ..system.tracking import TagMemory, in_display


@dataclass(frozen=True)
class Scene:
    """Application drawing intent, independent of pixels and device transport."""

    cursor: Point | None = None
    tags: tuple[Observation, ...] = ()
    selected_id: int | None = None
    target: Point | None = None
    confirmed: bool = False
    cached_ids: frozenset[int] = frozenset()


class SelectionPreview:
    """Application selection state; observations are retained by TagMemory."""

    def __init__(self, interaction, radius_px=6, hold_s=None, memory=None):
        self.pinch = PinchDetector(interaction.pinch_on, interaction.pinch_off,
                                   interaction.debounce_ms)
        self.radius = radius_px
        self.memory = memory if memory is not None else TagMemory(interaction, hold_s)
        self.clear()

    @property
    def tags(self):
        return self.memory.tags

    @property
    def visible_tags(self):
        return self.memory.visible_tags

    @property
    def ambiguous_ids(self):
        return self.memory.ambiguous_ids

    @property
    def cached_ids(self):
        return self.memory.cached_ids

    @property
    def display_tags(self):
        return self.memory.display_tags

    @property
    def timeout(self):
        return self.memory.timeout

    @property
    def hold_s(self):
        return self.memory.hold_s

    def clear(self):
        self.selected_id = None
        self.target = None
        self.phase = 'IDLE'

    def update(self, sample, observations, now):
        """Compatibility entry point for callers without a system session."""
        self.memory.update(observations, now)
        self.update_input(sample, now)

    def update_input(self, sample, now):
        """Consume input after the system has updated its shared tag memory."""
        cursor = sample.position if in_display(sample.position) else None
        event = self.pinch.update(sample.pinch_ratio if cursor else None, now)
        if cursor is None or self.selected_id is not None and self.selected_id not in self.tags:
            self.clear()
        if cursor is None:
            return
        if self.phase == 'IDLE' and event == 'begin' and self.tags:
            tag = min(self.tags.values(), key=lambda tag: cursor.distance(tag.position))
            if cursor.distance(tag.position) <= self.radius:
                self.selected_id = tag.piece_id
                self.phase = 'SELECTED'
        elif self.phase == 'SELECTED' and event == 'end':
            self.phase = 'AIMING'
            self.target = cursor
        elif self.phase == 'AIMING':
            self.target = cursor
            if event == 'begin':
                self.phase = 'CONFIRMED_PREVIEW'


class GestureDragApp:
    """Test application using system services rather than hardware adapters."""

    def __init__(self, interaction):
        self.interaction = interaction
        self.selection = SelectionPreview(interaction)
        self.error = ''

    def attach(self, system):
        """Bind to a session without retaining requests from another session."""
        if self.selection.memory is not system.tracking:
            self.selection = SelectionPreview(self.interaction, memory=system.tracking)
            self.error = ''

    def update(self, sample, observations, system, now):
        """Apply user input after system.update() has consumed observations.

        ``observations`` stays in the signature for the application host; tag
        memory is already current in the system and must not be updated twice.
        """
        self.attach(system)
        if not 0 <= now-sample.timestamp <= self.interaction.input_timeout_ms/1000:
            sample = InputSample(None, None, now)
        previous_phase = self.selection.phase
        self.selection.update_input(sample, now)
        if (self.selection.phase != 'CONFIRMED_PREVIEW'
                or previous_phase == 'CONFIRMED_PREVIEW'):
            return
        motion = system.motion_view
        if not motion.enabled:
            return
        if motion.phase != 'IDLE':
            self.error = 'CANCEL_OR_RESET_WITH_C_FIRST'
            return
        try:
            system.request_move(self.selection.selected_id, self.selection.target, now)
            self.error = ''
        except (ValueError, RuntimeError, OSError) as exc:
            self.error = str(exc)
            self.selection.phase = 'AIMING'

    def scene(self, system, cursor):
        self.attach(system)
        selected_id, target = self.selection.selected_id, self.selection.target
        confirmed = self.selection.phase == 'CONFIRMED_PREVIEW'
        motion = system.motion_view
        if motion.phase in ('MOVING', 'VERIFYING', 'DONE'):
            selected_id, target, confirmed = motion.selected_id, motion.target, True
        return Scene(
            cursor=cursor if in_display(cursor) else None,
            tags=tuple(system.tracking.display_tags),
            selected_id=selected_id,
            target=target,
            confirmed=confirmed,
            cached_ids=frozenset(system.tracking.cached_ids),
        )

    def _clear_selection(self):
        self.selection.clear()
        self.selection.pinch.update(None, 0.0)
        self.error = ''

    def cancel(self, system):
        self.attach(system)
        system.cancel()
        self._clear_selection()

    def stop(self, system):
        self.attach(system)
        system.stop()
        self._clear_selection()

    def reset_mapping(self):
        self.selection.memory.clear()
        self._clear_selection()
