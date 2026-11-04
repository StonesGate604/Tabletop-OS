"""Physical marker identities terminate here; applications receive logical IDs."""
from dataclasses import dataclass
import logging

from ..model import Point

LOG = logging.getLogger(__name__)

# OpenCV's predefined AprilTag dictionaries (also available without vision extras).
TAG_FAMILIES = {
    'tag16h5': ('DICT_APRILTAG_16h5', 30),
    'tag25h9': ('DICT_APRILTAG_25h9', 35),
    'tag36h10': ('DICT_APRILTAG_36h10', 2320),
    'tag36h11': ('DICT_APRILTAG_36h11', 587),
}


@dataclass(frozen=True)
class PieceDefinition:
    logical_id: str
    tag_id: int
    role: str
    enabled: bool = True


@dataclass(frozen=True)
class PieceObservation:
    piece_id: str
    position: Point
    timestamp: float


@dataclass(frozen=True)
class PieceBindings:
    tag_family: str
    pieces: tuple[PieceDefinition, ...]

    def validate(self, detector_dictionary):
        if not isinstance(self.tag_family, str) or self.tag_family not in TAG_FAMILIES:
            raise ValueError(f'Unsupported tag_family: {self.tag_family}')
        dictionary, size = TAG_FAMILIES[self.tag_family]
        if dictionary != detector_dictionary:
            raise ValueError(f'Tag family mismatch: {self.tag_family} requires {dictionary}; detector uses {detector_dictionary}')
        ids, tags = set(), set()
        for piece in self.pieces:
            if (not isinstance(piece.logical_id, str) or not piece.logical_id.strip()
                    or not isinstance(piece.role, str) or not piece.role.strip()
                    or type(piece.enabled) is not bool):
                raise ValueError('Each piece requires a logical ID, role and boolean enabled')
            if type(piece.tag_id) is not int or not 0 <= piece.tag_id < size:
                raise ValueError(f'Invalid tag_id for {piece.logical_id}: expected integer 0..{size-1}')
            if piece.logical_id in ids or piece.tag_id in tags:
                raise ValueError(f'Duplicate logical ID or AprilTag ID: {piece.logical_id} / {piece.tag_id}')
            ids.add(piece.logical_id)
            tags.add(piece.tag_id)


class PieceRegistry:
    """Immutable bindings and last confirmed identities, never nearest-neighbour matching."""

    def __init__(self, bindings, detector_dictionary, observation_timeout_s=.25):
        bindings.validate(detector_dictionary)
        self.bindings = bindings
        self.by_tag = {p.tag_id: p for p in bindings.pieces if p.enabled}
        self.by_id = {p.logical_id: p for p in bindings.pieces if p.enabled}
        self.timeout = observation_timeout_s
        self.last = {}
        self.visible = {}
        self.ambiguous = set()
        self.unknown = set()
        self._warned = set()

    def announce(self):
        LOG.info('AprilTag family=%s', self.bindings.tag_family)
        for p in self.bindings.pieces:
            LOG.info('Piece %s role=%s <- AprilTag %s enabled=%s', p.logical_id, p.role, p.tag_id, p.enabled)

    def update(self, observations, now):
        self.visible = {}
        self.unknown = set()
        groups = {}
        for observation in observations:
            if not observation.position.finite() or not 0 <= now-observation.timestamp <= self.timeout:
                continue
            groups.setdefault(observation.piece_id, []).append(observation)
        for tag_id, group in groups.items():
            definition = self.by_tag.get(tag_id)
            if definition is None:
                self.unknown.add(tag_id)
                if tag_id not in self._warned:
                    LOG.warning('Unregistered/disabled AprilTag %s ignored', tag_id)
                    self._warned.add(tag_id)
                continue
            logical_id = definition.logical_id
            if len(group) != 1:
                self.ambiguous.add(logical_id)  # latched until explicit restart
            if logical_id in self.ambiguous:
                continue
            observation = group[0]
            previous = self.last.get(logical_id)
            if previous and observation.timestamp < previous.timestamp:
                continue
            resolved = PieceObservation(logical_id, observation.position, observation.timestamp)
            self.visible[logical_id] = resolved
            self.last[logical_id] = resolved
        return self.visible
