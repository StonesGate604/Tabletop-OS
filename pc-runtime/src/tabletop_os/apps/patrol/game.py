"""Pure Patrol rules. No marker IDs, camera, device or transport dependencies."""
from enum import Enum

from ...application import MotionRequest
from .grid import facing_between


class GamePhase(str, Enum):
    SETUP = 'SETUP'
    PLAYER_TURN = 'PLAYER_TURN'
    PLAYER_MOVE_VALIDATION = 'PLAYER_MOVE_VALIDATION'
    GUARD_TURN = 'GUARD_TURN'
    GUARD_MOVING = 'GUARD_MOVING'
    RESOLVE = 'RESOLVE'
    GAME_WON = 'GAME_WON'
    GAME_LOST = 'GAME_LOST'
    ERROR_RECOVERY = 'ERROR_RECOVERY'


class PatrolApp:
    def __init__(self, rules):
        self.rules = rules
        self.initialize()

    def initialize(self):
        self.positions = dict(self.rules.initial_positions)
        self.phase = GamePhase.SETUP
        self.history = [self.phase.value]
        self.turn = 0
        self.objective_acquired = False
        self.preview = False
        self.needs_return = False
        self.error = ''
        self.guard_state = 'PATROL'  # ALERT reserved for future games
        self.guard_facing = self.rules.guard_facing
        self.path_index = 0
        self.path_direction = 1
        self._requests = []
        self.pending = None
        self._serial = 0
        self._steps_remaining = 0
        self.active = False

    def start(self):
        self.active = True

    def pause(self):
        self.active = False
        self.preview = False
        if self.pending:
            self.fail('PAUSED_DURING_MOTION_RESTART_REQUIRED')

    def stop(self):
        self.active = False
        self.preview = False
        self.pending = None
        self._requests.clear()

    def close(self):
        self.stop()

    def transition(self, phase):
        if phase != self.phase:
            self.phase = phase
            self.history.append(phase.value)

    @property
    def actionable(self):
        return frozenset({self.rules.player_id}) if self.active and self.phase == GamePhase.PLAYER_TURN else frozenset()

    @property
    def confirmed(self):
        return {key: self.rules.grid.center(cell) for key, cell in self.positions.items()}

    @property
    def guard_vision(self):
        return self.rules.grid.vision(self.positions[self.rules.guard_id], self.guard_facing,
                                      self.rules.vision_distance)

    def get_legal_moves(self, piece_id):
        if piece_id not in self.actionable or self.needs_return:
            return frozenset()
        occupied = {v for key, v in self.positions.items() if key != piece_id}
        return frozenset(self.rules.grid.neighbours(self.positions[piece_id])-occupied)

    def observe(self, placements, now):
        if not self.active:
            return
        if self.phase == GamePhase.SETUP:
            ready = all(key in placements and self.rules.grid.locate(placements[key].position) == cell
                        for key, cell in self.positions.items() if key not in self.rules.virtual_piece_ids)
            self.error = '' if ready else 'SETUP: place every physical piece at its marked start cell'
            if ready:
                self.transition(GamePhase.PLAYER_TURN)
            return
        if self.phase in (GamePhase.GAME_WON, GamePhase.GAME_LOST, GamePhase.ERROR_RECOVERY):
            return
        for key, observation in placements.items():
            if key not in self.positions or key == self.rules.player_id or key in self.rules.virtual_piece_ids:
                continue
            if key == self.rules.guard_id and self.phase == GamePhase.GUARD_MOVING:
                continue
            if self.rules.grid.locate(observation.position) != self.positions[key]:
                self.fail(f'UNEXPECTED_PIECE_POSITION:{key}; restore pieces and restart')
                return
        player = placements.get(self.rules.player_id)
        if (self.phase == GamePhase.GUARD_MOVING and player and
                self.rules.grid.locate(player.position) != self.positions[self.rules.player_id]):
            self.fail('PLAYER_MOVED_DURING_GUARD_TURN; restore pieces and restart')

    def handle(self, event):
        if not self.active or event.piece_id != self.rules.player_id or self.phase != GamePhase.PLAYER_TURN:
            return
        if event.kind in ('piece_proximity_entered', 'piece_manipulation_started'):
            self.preview = True
        elif event.kind in ('piece_proximity_exited', 'piece_interaction_cancelled'):
            self.preview = False
        elif event.kind == 'piece_tracking_lost':
            self.fail(f'TRACKING_LOST:{event.piece_id}; uncover and restart')
        elif event.kind == 'piece_position_stabilized':
            self._place(event.position)

    def _place(self, position):
        key = self.rules.player_id
        original = self.positions[key]
        cell = self.rules.grid.locate(position, original)
        if cell == original:
            self.needs_return = False
            self.error = ''
            return
        if self.needs_return:
            return
        self.transition(GamePhase.PLAYER_MOVE_VALIDATION)
        # Compute against the same player-turn rules without changing occupancy.
        occupied = {value for piece, value in self.positions.items() if piece != key}
        legal = self.rules.grid.neighbours(original)-occupied
        if cell not in legal:
            self.needs_return = True
            self.error = 'INVALID_MOVE_RETURN_TO_ORIGIN'
            self.transition(GamePhase.PLAYER_TURN)
            return
        self.positions[key] = cell
        self.turn += 1
        self.preview = False
        self.error = ''
        self.transition(GamePhase.RESOLVE)
        if self._resolve():
            return
        self._steps_remaining = self.rules.guard_steps
        self._next_guard()

    def _resolve(self):
        player = self.positions[self.rules.player_id]
        if player in self.guard_vision:
            self.guard_state = 'DETECTED'
            self.transition(GamePhase.GAME_LOST)
            return True
        if player == self.rules.objective:
            self.objective_acquired = True
        if self.objective_acquired and player == self.rules.exit:
            self.transition(GamePhase.GAME_WON)
            return True
        return False

    def _next_guard(self):
        self.transition(GamePhase.GUARD_TURN)
        path = self.rules.patrol_path
        direction = self.path_direction
        index = self.path_index+direction
        if self.rules.patrol_mode == 'loop':
            index %= len(path)
        elif not 0 <= index < len(path):
            direction = -direction
            index = self.path_index+direction
        target = path[index]
        if target in {cell for key, cell in self.positions.items() if key != self.rules.guard_id}:
            self.guard_state = 'DETECTED'
            self.transition(GamePhase.GAME_LOST)
            return
        self._serial += 1
        request = MotionRequest(f'guard-{self._serial}', self.rules.guard_id, self.rules.grid.center(target))
        facing = facing_between(self.positions[self.rules.guard_id], target)
        self.pending = (request, target, index, direction, facing)
        self._requests.append(request)
        self.transition(GamePhase.GUARD_MOVING)

    def motion_finished(self, result):
        if not self.active or not self.pending or self.phase != GamePhase.GUARD_MOVING:
            return
        request, target, index, direction, facing = self.pending
        if result.token != request.token:
            return
        self.pending = None
        if not result.success:
            self.fail(result.error or 'GUARD_MOTION_FAILED')
            return
        self.positions[self.rules.guard_id] = target
        self.path_index, self.path_direction, self.guard_facing = index, direction, facing
        self._steps_remaining -= 1
        self.transition(GamePhase.RESOLVE)
        if self._resolve():
            return
        if self._steps_remaining:
            self._next_guard()
        else:
            self.transition(GamePhase.PLAYER_TURN)

    def fail(self, error):
        if self.phase in (GamePhase.GAME_WON, GamePhase.GAME_LOST):
            return
        self.error = error
        self.preview = False
        self.pending = None
        self._requests.clear()
        self.transition(GamePhase.ERROR_RECOVERY)

    def update(self, now):
        pass  # Events, rather than frames, advance turns.

    def take_requests(self):
        requests, self._requests = tuple(self._requests), []
        return requests
