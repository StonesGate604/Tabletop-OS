from dataclasses import replace
import unittest

from tabletop_os.application import MotionResult
from tabletop_os.apps.patrol.game import PatrolApp, GamePhase
from tabletop_os.apps.patrol.grid import Grid
from tabletop_os.model import Point
from tabletop_os.system.pieces import PieceObservation
from tabletop_os.system.proximity import InteractionEvent
from patrol_support import config, raw_config


def started(raw=None):
    app = PatrolApp(config(raw).rules)
    app.start()
    placements = {key: PieceObservation(key, position, 1) for key, position in app.confirmed.items()}
    app.observe(placements, 1)
    return app


def place(app, cell):
    app.handle(InteractionEvent('piece_position_stabilized', app.rules.player_id, app.rules.grid.center(cell)))


def finish_guard(app):
    while app.phase == GamePhase.GUARD_MOVING:
        request = app.take_requests()[0]
        app.motion_finished(MotionResult(request.token, True))


class PatrolRulesTests(unittest.TestCase):
    def test_start_waits_for_all_registered_pieces_at_start_cells(self):
        app = PatrolApp(config().rules)
        app.start()
        app.observe({}, 1)
        self.assertEqual(app.phase, GamePhase.SETUP)
        self.assertIn('SETUP', app.error)
        positions = {key: PieceObservation(key, point, 2) for key, point in app.confirmed.items()}
        app.observe(positions, 2)
        self.assertEqual(app.phase, GamePhase.PLAYER_TURN)

    def test_orthogonal_move_queues_one_guard_request_with_logical_identity(self):
        app = started()
        before = app.positions['guard']
        place(app, (5, 1))
        self.assertEqual(app.turn, 1)
        self.assertEqual(app.positions['player'], (5, 1))
        self.assertEqual(app.positions['guard'], before)
        self.assertEqual(app.phase, GamePhase.GUARD_MOVING)
        request = app.take_requests()[0]
        self.assertEqual(request.piece_id, 'guard')
        for i in range(5):
            place(app, (5, 1))
            app.update(i)
        self.assertEqual(app.take_requests(), ())
        app.motion_finished(MotionResult('stale-token', True))
        self.assertEqual(app.positions['guard'], before)
        app.motion_finished(MotionResult(request.token, True))
        self.assertEqual(app.positions['guard'], (0, 4))
        self.assertEqual(app.phase, GamePhase.PLAYER_TURN)
        app.motion_finished(MotionResult(request.token, True))
        self.assertEqual(app.turn, 1)

    def test_diagonal_obstacle_outside_and_occupied_are_illegal(self):
        raw = raw_config()
        raw['map']['obstacles'].append([4, 0])
        raw['pieces']['crate'] = {'tag_id': 18, 'role': 'neutral', 'enabled': True}
        raw['map']['initial_positions']['crate'] = [5, 1]
        for cell in ((4, 1), (4, 0), (6, 0), (5, 1)):
            app = started(raw)
            original = app.positions.copy()
            place(app, cell)
            self.assertEqual(app.phase, GamePhase.PLAYER_TURN)
            self.assertTrue(app.needs_return)
            self.assertEqual(app.turn, 0)
            self.assertEqual(app.positions, original)
            self.assertEqual(app.take_requests(), ())

    def test_invalid_drop_requires_stable_return_before_any_new_move(self):
        app = started()
        place(app, (4, 1))
        place(app, (5, 1))
        self.assertEqual(app.turn, 0)
        self.assertTrue(app.needs_return)
        place(app, (5, 0))
        self.assertFalse(app.needs_return)
        place(app, (5, 1))
        self.assertEqual(app.turn, 1)

    def test_boundary_and_center_tolerance_do_not_accept_uncertain_cells(self):
        grid = config().rules.grid
        edge = Point(grid.width_mm/8, grid.center((5, 0)).y)
        self.assertIsNone(grid.locate(edge, (5, 0)))
        self.assertIsNone(grid.locate(Point(-1, 100)))
        self.assertEqual(grid.locate(grid.center((5, 1)), (5, 0)), (5, 1))

    def test_hover_grasp_and_same_cell_events_do_not_consume_turn(self):
        app = started()
        for kind in ('piece_proximity_entered', 'piece_manipulation_started', 'piece_proximity_exited',
                     'piece_interaction_cancelled'):
            app.handle(InteractionEvent(kind, 'player'))
            self.assertEqual(app.turn, 0)
        place(app, (5, 0))
        self.assertEqual(app.phase, GamePhase.PLAYER_TURN)
        self.assertEqual(app.take_requests(), ())

    def test_guard_patrol_loop_and_heading(self):
        app = started()
        path = app.rules.patrol_path
        for step in range(1, len(path)+2):
            place(app, (5, step % 2))
            finish_guard(app)
            self.assertEqual(app.positions['guard'], path[step % len(path)])
        self.assertEqual(app.guard_facing, 'E')

    def test_guard_reverse_at_endpoint_and_multiple_steps(self):
        raw = raw_config()
        raw['game'].update(patrol_path=[[0, 3], [0, 4], [0, 5]], patrol_mode='reverse', guard_steps=2)
        app = started(raw)
        place(app, (5, 1))
        finish_guard(app)
        self.assertEqual(app.positions['guard'], (0, 5))
        place(app, (5, 0))
        finish_guard(app)
        self.assertEqual(app.positions['guard'], (0, 3))
        self.assertEqual(app.guard_facing, 'W')

    def test_vision_respects_facing_range_and_obstacle_occlusion(self):
        grid = Grid(6, 8, 298, 466, frozenset({(2, 3)}))
        vision = grid.vision((2, 1), 'E', 3)
        self.assertIn((2, 2), vision)
        self.assertNotIn((2, 4), vision)
        self.assertNotIn((2, 0), vision)
        self.assertNotIn((2, 5), vision)
        self.assertNotIn((0, 2), vision)

    def test_obstacle_corner_blocks_diagonal_ray(self):
        grid = Grid(6, 8, 298, 466, frozenset({(1, 2)}))
        self.assertFalse(grid.line_clear((1, 1), (2, 2)))

    def test_danger_is_legal_and_resolves_before_guard_motion(self):
        raw = raw_config()
        raw['map']['initial_positions']['player'] = [1, 0]
        raw['game']['guard_facing'] = 'W'
        app = started(raw)
        self.assertIn((0, 0), app.get_legal_moves('player'))
        app.handle(InteractionEvent('piece_proximity_entered', 'player'))
        self.assertEqual(app.phase, GamePhase.PLAYER_TURN)
        place(app, (0, 0))
        self.assertEqual(app.phase, GamePhase.GAME_LOST)
        self.assertEqual(app.guard_state, 'DETECTED')
        self.assertEqual(app.take_requests(), ())

    def test_guard_move_rechecks_detection_at_completion_only(self):
        raw = raw_config()
        raw['map']['initial_positions']['player'] = [2, 5]
        raw['game']['guard_facing'] = 'N'
        app = started(raw)
        place(app, (1, 5))
        self.assertEqual(app.phase, GamePhase.GUARD_MOVING)
        finish_guard(app)
        self.assertEqual(app.phase, GamePhase.GAME_LOST)

    def test_objective_then_exit_wins_immediately_without_extra_guard_request(self):
        app = started()
        for column in range(1, 8):
            place(app, (5, column))
            if column == 4:
                self.assertTrue(app.objective_acquired)
            if column == 7:
                self.assertEqual(app.take_requests(), ())
                break
            finish_guard(app)
        self.assertEqual(app.phase, GamePhase.GAME_WON)
        self.assertEqual(app.turn, 7)

    def test_exit_before_objective_does_not_win(self):
        raw = raw_config()
        raw['map']['exit'] = [5, 1]
        app = started(raw)
        place(app, (5, 1))
        self.assertEqual(app.phase, GamePhase.GUARD_MOVING)
        self.assertFalse(app.objective_acquired)

    def test_motion_failure_keeps_confirmed_guard_position_and_stops_turns(self):
        app = started()
        original = app.positions['guard']
        place(app, (5, 1))
        request = app.take_requests()[0]
        app.motion_finished(MotionResult(request.token, False, 'MOTOR_TIMEOUT'))
        self.assertEqual(app.phase, GamePhase.ERROR_RECOVERY)
        self.assertEqual(app.positions['guard'], original)
        place(app, (5, 2))
        self.assertEqual(app.turn, 1)
        self.assertEqual(app.take_requests(), ())

    def test_other_piece_cannot_act_during_player_turn(self):
        app = started()
        app.handle(InteractionEvent('piece_position_stabilized', 'guard', Point(50, 50)))
        self.assertEqual(app.turn, 0)
        self.assertEqual(app.get_legal_moves('guard'), frozenset())

    def test_map_validation_blocks_invalid_routes_and_placements(self):
        mutations = [
            lambda r: r['map'].update(rows=True),
            lambda r: r['map'].update(objective=[-1, 1]),
            lambda r: r['map']['obstacles'].append([5, 0]),
            lambda r: r['map']['initial_positions'].update(guard=[5, 0]),
            lambda r: r['game'].update(patrol_path=[[0, 3], [0, 5]]),
            lambda r: r['game'].update(patrol_path=[[0, 3], [0, 4], [0, 5]]),
            lambda r: r['game'].update(guard_facing={}),
            lambda r: r['game'].update(player_id='missing'),
            lambda r: r['map'].update(center_tolerance=float('nan')),
        ]
        for mutate in mutations:
            raw = raw_config()
            mutate(raw)
            with self.assertRaises(ValueError):
                config(raw)


if __name__ == '__main__':
    unittest.main()
