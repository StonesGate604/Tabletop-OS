"""Multiple-tag, two-pinch selection preview in LED pixels; emits no motion."""
import math
from .gestures import PinchDetector


def in_display(position):
    return position is not None and position.finite() and 0 <= position.x <= 63 and 0 <= position.y <= 63


class SelectionPreview:
    def __init__(self, interaction, radius_px=6, hold_s=None):
        self.pinch = PinchDetector(interaction.pinch_on, interaction.pinch_off, interaction.debounce_ms)
        self.timeout = interaction.observation_timeout_ms/1000
        if hold_s is not None and (not math.isfinite(hold_s) or hold_s < self.timeout):
            raise ValueError('Tag hold duration must be finite and at least the observation timeout')
        self.hold_s = hold_s
        self.radius = radius_px
        self.tags = {}
        self.visible_tags = ()
        self.ambiguous_ids = set()
        self.clear()

    @property
    def cached_ids(self):
        visible_ids = {tag.piece_id for tag in self.visible_tags}
        return frozenset(self.tags.keys()-visible_ids)

    @property
    def display_tags(self):
        return self.visible_tags + tuple(tag for key,tag in self.tags.items() if key in self.cached_ids)

    def clear(self):
        self.selected_id = None
        self.target = None
        self.phase = 'IDLE'

    def update(self, sample, observations, now):
        self.visible_tags = tuple(tag for tag in observations if in_display(tag.position)
                                  and 0 <= now-tag.timestamp <= self.timeout)
        ids = [tag.piece_id for tag in self.visible_tags]
        self.ambiguous_ids.update(key for key in ids if ids.count(key) > 1)
        for observation in self.visible_tags:
            if observation.piece_id not in self.ambiguous_ids:
                previous = self.tags.get(observation.piece_id)
                if previous is None or observation.timestamp > previous.timestamp:
                    self.tags[observation.piece_id] = observation
        self.tags = {key:tag for key,tag in self.tags.items() if key not in self.ambiguous_ids and
                     now >= tag.timestamp and (self.hold_s is None or now-tag.timestamp <= self.hold_s)}
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
