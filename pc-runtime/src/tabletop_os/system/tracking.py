"""Last-observed tag positions, kept separate from interaction state."""
import math

from ..frame_protocol import HEIGHT, WIDTH


def in_display(position):
    return (position is not None and position.finite() and
            0 <= position.x <= WIDTH - 1 and 0 <= position.y <= HEIGHT - 1)


class TagMemory:
    """Preserve display positions without making cached data a fresh observation."""

    def __init__(self, interaction, hold_s=None):
        self.timeout = interaction.observation_timeout_ms/1000
        if hold_s is not None and (not math.isfinite(hold_s) or hold_s < self.timeout):
            raise ValueError('Tag hold duration must be finite and at least the observation timeout')
        self.hold_s = hold_s
        self.clear()

    @property
    def cached_ids(self):
        visible_ids = {tag.piece_id for tag in self.visible_tags}
        return frozenset(self.tags.keys()-visible_ids)

    @property
    def display_tags(self):
        cached = self.cached_ids
        return self.visible_tags + tuple(tag for key, tag in self.tags.items() if key in cached)

    def clear(self):
        self.tags = {}
        self.visible_tags = ()
        self.ambiguous_ids = set()

    def update(self, observations, now):
        self.visible_tags = tuple(tag for tag in observations if in_display(tag.position)
                                  and 0 <= now-tag.timestamp <= self.timeout)
        ids = [tag.piece_id for tag in self.visible_tags]
        self.ambiguous_ids.update(key for key in ids if ids.count(key) > 1)
        for observation in self.visible_tags:
            if observation.piece_id not in self.ambiguous_ids:
                previous = self.tags.get(observation.piece_id)
                if previous is None or observation.timestamp > previous.timestamp:
                    self.tags[observation.piece_id] = observation
        self.tags = {key: tag for key, tag in self.tags.items() if key not in self.ambiguous_ids and
                     now >= tag.timestamp and (self.hold_s is None or now-tag.timestamp <= self.hold_s)}
