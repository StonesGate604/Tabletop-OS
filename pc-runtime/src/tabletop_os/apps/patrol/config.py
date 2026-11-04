"""Strict Patrol document loader; bindings are kept outside the game rules."""
from dataclasses import dataclass
import json
from pathlib import Path

from ...system.pieces import PieceBindings, PieceDefinition
from ...system.proximity import ProximityConfig
from .grid import DIRECTIONS, Grid


@dataclass(frozen=True)
class PatrolRules:
    grid: Grid
    player_id: str
    guard_id: str
    initial_positions: tuple
    objective: tuple[int, int]
    exit: tuple[int, int]
    patrol_path: tuple
    patrol_mode: str
    guard_facing: str
    vision_distance: int
    guard_steps: int = 1
    guard_mode: str = 'physical'

    @property
    def virtual_piece_ids(self):
        return frozenset({self.guard_id}) if self.guard_mode == 'virtual' else frozenset()


@dataclass(frozen=True)
class PatrolConfig:
    bindings: PieceBindings
    rules: PatrolRules
    proximity: ProximityConfig
    motion_timeout_s: float = 360


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def fields(value, allowed, required=(), name='configuration'):
    if not isinstance(value, dict) or set(value)-set(allowed) or set(required)-set(value):
        raise ValueError(f'Unknown or missing fields in {name}')


def parse_patrol(raw, runtime):
    fields(raw, ('tag_family', 'pieces', 'map', 'game', 'proximity', 'motion_timeout_s'),
           ('tag_family', 'pieces', 'map', 'game'))
    if not isinstance(raw['pieces'], dict) or not raw['pieces']:
        raise ValueError('pieces must be a nonempty logical-ID object')
    pieces = []
    for key, value in raw['pieces'].items():
        fields(value, ('tag_id', 'role', 'enabled'), ('tag_id', 'role', 'enabled'), f'pieces.{key}')
        pieces.append(PieceDefinition(key, value['tag_id'], value['role'], value['enabled']))
    bindings = PieceBindings(raw['tag_family'], tuple(pieces))
    bindings.validate(runtime.camera.april_tag_dictionary)
    m, g = raw['map'], raw['game']
    fields(g, ('player_id', 'guard_id', 'patrol_path', 'patrol_mode', 'guard_facing',
               'vision_distance', 'guard_steps', 'guard_mode'),
           ('player_id', 'guard_id', 'patrol_path', 'patrol_mode', 'guard_facing', 'vision_distance'), 'game')
    guard_mode = g.get('guard_mode', 'physical')
    if guard_mode not in ('physical', 'virtual'):
        raise ValueError('guard_mode must be physical or virtual')
    enabled = {p.logical_id: p for p in pieces if p.enabled}
    definitions = {p.logical_id: p for p in pieces}
    player, guard = g['player_id'], g['guard_id']
    if (not isinstance(player, str) or not isinstance(guard, str) or not guard.strip() or player == guard
            or player not in enabled or enabled[player].role != 'player'):
        raise ValueError('game.player_id / guard_id must name distinct player / guard roles; the player must be enabled')
    if guard_mode == 'physical':
        if guard not in enabled or enabled[guard].role != 'guard':
            raise ValueError('Physical guard_id must name an enabled guard role')
    elif guard in enabled:
        raise ValueError('Virtual guard cannot have an enabled physical binding; disable or remove its pieces entry')
    elif guard in definitions and definitions[guard].role != 'guard':
        raise ValueError('Virtual guard_id must name a guard role')
    fields(m, ('rows', 'columns', 'width_mm', 'height_mm', 'obstacles', 'objective', 'exit', 'initial_positions',
               'center_tolerance', 'boundary_hysteresis'),
           ('rows', 'columns', 'obstacles', 'objective', 'exit', 'initial_positions'), 'map')
    rows, columns = m['rows'], m['columns']
    if any(type(v) is not int for v in (rows, columns)) or not (2 <= rows <= 48 and 2 <= columns <= 32):
        raise ValueError('Map rows must be 2..48 and columns 2..32')

    def cell(value):
        if (not isinstance(value, (list, tuple)) or len(value) != 2
                or any(type(v) is not int for v in value)
                or not (0 <= value[0] < rows and 0 <= value[1] < columns)):
            raise ValueError(f'Invalid map cell: {value}')
        return tuple(value)

    if not isinstance(m['obstacles'], list):
        raise ValueError('obstacles must be a list')
    tolerance, hysteresis = m.get('center_tolerance', .32), m.get('boundary_hysteresis', .06)
    if (type(tolerance) not in (int, float) or type(hysteresis) not in (int, float)
            or not 0 < tolerance < .5 or not 0 <= hysteresis < .5-tolerance):
        raise ValueError('Invalid center tolerance / boundary hysteresis (fractions of a cell)')
    import math
    width, height = m.get('width_mm', runtime.board.width_mm), m.get('height_mm', runtime.board.height_mm)
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 < v <= 10000 for v in (width, height)):
        raise ValueError('Map plane dimensions must be finite positive millimetres')
    grid = Grid(rows, columns, width, height,
                frozenset(cell(v) for v in m['obstacles']), tolerance, hysteresis)
    if not isinstance(m['initial_positions'], dict):
        raise ValueError('initial_positions must be a logical-ID object')
    initial = {key: cell(value) for key, value in m['initial_positions'].items()}
    required = set(enabled) | ({guard} if guard_mode == 'virtual' else set())
    if set(initial) != required:
        raise ValueError('initial_positions must match all enabled physical pieces and the virtual guard, if used')
    if len(set(initial.values())) != len(initial):
        raise ValueError('Initial piece cells must be distinct')
    objective, exit_cell = cell(m['objective']), cell(m['exit'])
    if any(not grid.passable(v) for v in (*initial.values(), objective, exit_cell)):
        raise ValueError('Initial positions, objective and exit must be passable')
    if not isinstance(g['patrol_path'], list) or len(g['patrol_path']) < 2:
        raise ValueError('patrol_path requires at least two cells')
    path = tuple(cell(v) for v in g['patrol_path'])
    if len(set(path)) != len(path) or initial[guard] != path[0] or any(not grid.passable(v) for v in path):
        raise ValueError('Patrol path must be unique, passable and start at the guard')
    static = set(initial.values())-{initial[player], initial[guard]}
    if static.intersection(path):
        raise ValueError('Patrol path crosses another stationary piece')
    if (g['patrol_mode'] not in ('loop', 'reverse') or not isinstance(g['guard_facing'], str)
            or g['guard_facing'] not in DIRECTIONS):
        raise ValueError('Invalid patrol_mode or guard_facing')
    edges = list(zip(path, path[1:]))
    if g['patrol_mode'] == 'loop':
        edges.append((path[-1], path[0]))
    if any(abs(a[0]-b[0])+abs(a[1]-b[1]) != 1 for a, b in edges):
        raise ValueError('Each patrol step, including the loop seam, must be one orthogonal cell')
    vision, steps = g['vision_distance'], g.get('guard_steps', 1)
    if (type(vision) is not int or not 1 <= vision <= max(rows, columns)
            or type(steps) is not int or not 1 <= steps <= len(path)):
        raise ValueError('Invalid vision_distance or guard_steps')
    proximity_raw = raw.get('proximity', {})
    fields(proximity_raw, ProximityConfig.__dataclass_fields__, name='proximity')
    proximity = ProximityConfig(**proximity_raw)
    if proximity.stability_radius_mm >= min(grid.width_mm/columns, grid.height_mm/rows)*tolerance:
        raise ValueError('Stability radius must fit within the cell center tolerance')
    timeout = raw.get('motion_timeout_s', 360)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('motion_timeout_s must be finite and positive')
    return PatrolConfig(bindings, PatrolRules(grid, player, guard, tuple(initial.items()), objective, exit_cell,
                        path, g['patrol_mode'], g['guard_facing'], vision, steps, guard_mode), proximity, timeout)


def load_patrol(path: str | Path, runtime):
    return parse_patrol(json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=unique_object), runtime)
