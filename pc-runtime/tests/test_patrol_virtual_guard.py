"""Virtual guards share real game rules without physical observations or motion."""
from dataclasses import replace
import types
import unittest
from unittest.mock import Mock, patch

from tabletop_os.application import MotionRequest
from tabletop_os.apps.patrol.game import GamePhase, PatrolApp
from tabletop_os.apps.patrol.render import COLORS, render_frame
from tabletop_os.calibration import Calibration
from tabletop_os.config import RuntimeConfig
from tabletop_os.model import InputSample, Observation, Point
from tabletop_os.patrol_launch import launch_patrol
from tabletop_os.patrol_simulation import LocalDisplayDevice, run_patrol_demo
from tabletop_os.runtime import ApplicationRuntime
from tabletop_os.system.application_motion import ApplicationMotion, TablePlane
from tabletop_os.system.pieces import PieceObservation, PieceRegistry
from tabletop_os.system.session import MotionOptions, TabletopSystem
from patrol_support import Harness, config, raw_config


def virtual_config():
    raw = raw_config()
    raw['game']['guard_mode'] = 'virtual'
    raw['pieces']['guard']['enabled'] = False
    return raw


class VirtualGuardConfigTests(unittest.TestCase):
    def test_default_stays_physical_and_virtual_guard_can_have_no_tag_binding(self):
        self.assertEqual(config().rules.guard_mode, 'physical')
        raw = virtual_config()
        del raw['pieces']['guard']
        cfg = config(raw)
        self.assertEqual(cfg.rules.virtual_piece_ids, frozenset({'guard'}))
        self.assertEqual([p.logical_id for p in cfg.bindings.pieces], ['player'])
        self.assertIn('guard', dict(cfg.rules.initial_positions))

    def test_rejects_ambiguous_or_missing_required_bindings_and_initial_positions(self):
        mutations = [
            lambda r: r['game'].update(guard_mode='unknown'),
            lambda r: r['pieces']['guard'].update(enabled=True),
            lambda r: r['pieces']['player'].update(enabled=False),
            lambda r: r['pieces']['guard'].update(role='player'),
            lambda r: r['map']['initial_positions'].pop('guard'),
            lambda r: r['game'].update(guard_id='player'),
            lambda r: r['game'].update(guard_id=''),
        ]
        for mutate in mutations:
            raw = virtual_config()
            mutate(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                config(raw)

    def test_disabled_guard_still_requires_explicit_virtual_mode(self):
        raw = raw_config()
        raw['pieces']['guard']['enabled'] = False
        with self.assertRaisesRegex(ValueError, 'Physical guard'):
            config(raw)

    def test_virtual_mode_is_not_tied_to_default_logical_ids(self):
        raw = virtual_config()
        del raw['pieces']['guard']
        raw['pieces']['hero'] = raw['pieces'].pop('player')
        raw['game'].update(player_id='hero', guard_id='patrol-east')
        raw['map']['initial_positions'] = {'hero': [5, 0], 'patrol-east': [0, 3]}
        cfg = config(raw)
        self.assertEqual(cfg.rules.virtual_piece_ids, frozenset({'patrol-east'}))


class VirtualGuardRuntimeTests(unittest.TestCase):
    def harness(self, raw=None):
        h = Harness(raw or virtual_config())
        self.addCleanup(h.close)
        return h

    def test_occlusion_before_proximity_entry_keeps_preview_without_committing(self):
        raw = virtual_config()
        raw['proximity']['tag_loss_grace_s'] = 5
        h = self.harness(raw)
        original = h.sim.positions['player']
        h.sim.hand = original
        h.advance(.05)
        h.sim.hidden.add('player')
        h.advance(2)
        self.assertTrue(h.app.preview)
        self.assertEqual(h.app.phase, GamePhase.PLAYER_TURN)
        self.assertEqual(h.app.turn, 0)
        self.assertEqual(h.runtime.registry.last['player'].position, original)
        h.sim.hand = None
        h.sim.positions['player'] = h.app.rules.grid.center((5, 1))
        h.advance(.5)
        self.assertEqual(h.app.turn, 0)
        h.sim.hidden.clear()
        h.advance(1)
        self.assertEqual(h.app.turn, 1)
        self.assertEqual(h.app.positions['player'], (5, 1))
        self.assertEqual(h.runtime.motion.request_count, 1)

    def test_locked_position_expires_to_recovery_without_advancing_game(self):
        raw = virtual_config()
        raw['proximity']['tag_loss_grace_s'] = 5
        h = self.harness(raw)
        h.sim.hand = h.sim.positions['player']
        h.advance(.05)
        h.sim.hidden.add('player')
        h.advance(5.5)
        self.assertEqual(h.app.phase, GamePhase.ERROR_RECOVERY)
        self.assertEqual(h.app.positions['player'], (5, 0))
        self.assertEqual(h.app.turn, 0)
        self.assertEqual(h.runtime.motion.request_count, 0)

    def test_only_player_is_observed_and_guard_moves_once_then_player_can_move_again(self):
        h = self.harness()
        self.assertEqual(h.app.phase, GamePhase.PLAYER_TURN)
        self.assertEqual(h.runtime.motion.mode, 'virtual')
        self.assertEqual(set(h.sim.positions), {'player'})
        self.assertEqual(set(h.runtime.registry.last), {'player'})
        self.assertEqual(set(h.runtime.filters), {'player'})
        h.hover()
        self.assertTrue(h.app.preview)
        self.assertEqual(h.app.turn, 0)
        h.drop((5, 1))
        self.assertEqual(h.app.positions['guard'], (0, 4))
        self.assertEqual(h.app.phase, GamePhase.PLAYER_TURN)
        self.assertEqual(h.runtime.motion.request_count, 1)
        h.advance(2)
        self.assertEqual(h.runtime.motion.request_count, 1)
        self.assertEqual(set(h.runtime.registry.last), {'player'})
        h.drop((5, 2))
        self.assertEqual(h.app.turn, 2)
        self.assertEqual(h.app.positions['guard'], (0, 5))

    def test_camera_input_runs_with_only_player_and_publishes_red_without_motor_requests(self):
        platform, cfg = RuntimeConfig(), config(virtual_config())
        device = LocalDisplayDevice()
        device.request = Mock(side_effect=AssertionError('Unexpected hardware command'))
        mapping = Calibration(64, 96, 63, 95, ((1, 0, 0), (0, 1, 0), (0, 0, 1)))
        runtime = ApplicationRuntime(platform, cfg, TabletopSystem(platform, mapping, device=device, rotation=0))
        runtime.start()
        self.addCleanup(runtime.close)
        position = cfg.rules.grid.center((5, 0))
        now = 0

        def advance(seconds, include_player=True):
            nonlocal now
            for _ in range(round(seconds/.05)):
                now += .05
                tags = ((Observation(5, runtime.plane.display(position), now),) if include_player else ())
                runtime.tick(InputSample(None, None, now), tags, (), now)

        advance(2)
        app = runtime.host.app
        self.assertEqual(app.phase, GamePhase.PLAYER_TURN)
        point = runtime.plane.display(cfg.rules.grid.center(app.positions['guard']))
        offset = (round(point.y)*64+round(point.x))*3
        self.assertEqual(device.rgb[offset:offset+3], bytes(COLORS['guard']))
        self.assertGreater(device.rgb[offset], 0)
        position = cfg.rules.grid.center((5, 1))
        advance(.8)
        self.assertEqual(app.positions['guard'], (0, 4))
        self.assertEqual(app.phase, GamePhase.PLAYER_TURN)
        device.request.assert_not_called()
        # The physical player still needs genuine tracking and safe recovery.
        advance(2, include_player=False)
        self.assertEqual(app.phase, GamePhase.ERROR_RECOVERY)
        self.assertIn('TRACKING_LOST:player', app.error)

    def test_guard_vision_still_detects_player_after_virtual_patrol_step(self):
        raw = virtual_config()
        raw['map']['initial_positions']['player'] = [2, 5]
        raw['game']['guard_facing'] = 'N'
        h = self.harness(raw)
        h.drop((1, 5))
        self.assertEqual(h.app.positions['guard'], (0, 4))
        self.assertEqual(h.app.phase, GamePhase.GAME_LOST)
        self.assertEqual(h.app.guard_state, 'DETECTED')

    def test_invalid_player_move_still_requires_return_and_cannot_move_guard(self):
        h = self.harness()
        h.drop((4, 1))
        self.assertTrue(h.app.needs_return)
        self.assertEqual(h.app.positions['guard'], (0, 3))
        self.assertEqual(h.runtime.motion.request_count, 0)
        h.drop((5, 1))
        self.assertEqual(h.app.turn, 0)
        h.drop((5, 0))
        self.assertFalse(h.app.needs_return)
        h.drop((5, 1))
        self.assertEqual(h.app.turn, 1)

    def test_multiple_virtual_steps_keep_patrol_direction_and_detection_rules(self):
        raw = virtual_config()
        raw['game'].update(patrol_path=[[0, 3], [0, 4], [0, 5]], patrol_mode='reverse', guard_steps=2)
        h = self.harness(raw)
        h.drop((5, 1))
        self.assertEqual(h.app.positions['guard'], (0, 5))
        self.assertEqual(h.runtime.motion.request_count, 2)
        h.drop((5, 0))
        self.assertEqual(h.app.positions['guard'], (0, 3))
        self.assertEqual(h.app.guard_facing, 'W')

    def test_switching_between_physical_and_virtual_modes_clears_old_tracking(self):
        h = self.harness(raw_config())
        h.runtime.reload(config(virtual_config()))
        self.assertNotIn('guard', h.runtime.registry.by_id)
        self.assertEqual(h.runtime.registry.last, {})
        h.advance(2)
        self.assertEqual(h.app.phase, GamePhase.PLAYER_TURN)
        h.runtime.reload(config())
        self.assertIn('guard', h.runtime.registry.by_id)
        self.assertEqual(h.runtime.registry.last, {})
        h.advance(1)
        self.assertEqual(h.runtime.motion.mode, 'simulation')
        self.assertEqual(h.app.phase, GamePhase.PLAYER_TURN)

    def test_complete_game_wins_with_no_guard_detection_at_any_time(self):
        h = self.harness()
        h.runtime.reload(h.config)
        result = run_patrol_demo(h.runtime)
        self.assertEqual(result['guard_mode'], 'virtual')
        self.assertEqual(result['phase'], 'GAME_WON')
        self.assertEqual(result['guard_requests'], 6)
        self.assertTrue(result['hover_preview_verified'])
        self.assertTrue(result['occlusion_verified'])
        self.assertTrue(result['cancellation_verified'])
        self.assertNotIn('guard', h.runtime.registry.last)


class VirtualMotionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.config = config(virtual_config())
        self.registry = PieceRegistry(self.config.bindings, 'DICT_APRILTAG_36h11')
        self.system = types.SimpleNamespace(options=MotionOptions(), request_move=Mock(), cancel=Mock())

    def motion(self):
        return ApplicationMotion(self.system, self.registry, TablePlane(self.config.rules.grid), self.config.proximity,
                                 mode='virtual', virtual_piece_ids=self.config.rules.virtual_piece_ids)

    def test_completion_is_correlated_once_without_reading_tag_or_sending_commands(self):
        motion = self.motion()
        request = MotionRequest('virtual-1', 'guard', self.config.rules.grid.center((0, 4)))
        motion.begin(request, 1)
        result = motion.update(InputSample(None, None, 1.1), 1.1)
        self.assertTrue(result.success)
        self.assertEqual(result.token, request.token)
        self.assertIsNone(motion.update(InputSample(None, None, 1.2), 1.2))
        self.assertEqual(self.registry.last, {})
        self.system.request_move.assert_not_called()

    def test_cancellation_cannot_complete_virtual_action_or_send_physical_stop(self):
        motion = self.motion()
        motion.begin(MotionRequest('virtual-2', 'guard', self.config.rules.grid.center((0, 4))), 1)
        motion.cancel('USER_STOP')
        result = motion.update(InputSample(None, None, 1.1), 1.1)
        self.assertFalse(result.success)
        self.assertEqual(result.error, 'USER_STOP')
        self.system.request_move.assert_not_called()
        self.system.cancel.assert_not_called()

    def test_virtual_backend_cannot_move_a_physical_player(self):
        with self.assertRaisesRegex(ValueError, 'UNREGISTERED_LOGICAL_PIECE'):
            self.motion().begin(MotionRequest('wrong-piece', 'player', Point(20, 20)), 1)

    def test_virtual_and_hardware_combination_is_rejected_before_starting_camera_or_device(self):
        args = types.SimpleNamespace(simulate=False, hardware_motion=True, led=True)
        with patch('tabletop_os.patrol_launch.TabletopSystem') as system, \
             patch('tabletop_os.system.camera.CameraSession') as camera:
            with self.assertRaisesRegex(ValueError, 'Virtual guard cannot use'):
                launch_patrol(RuntimeConfig(), self.config, args)
            system.assert_not_called()
            camera.assert_not_called()

    def test_runtime_and_reload_cannot_silently_enable_physical_motion_for_virtual_guard(self):
        self.system.options = MotionOptions(enabled=True, pick_place=True)
        with self.assertRaisesRegex(ValueError, 'Virtual guard cannot enable'):
            ApplicationRuntime(RuntimeConfig(), self.config, self.system)
        with self.assertRaisesRegex(ValueError, 'Hardware motion must be explicitly enabled'):
            self.motion()


if __name__ == '__main__':
    unittest.main()
