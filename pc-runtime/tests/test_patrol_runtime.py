from dataclasses import replace
import contextlib
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from tabletop_os import cli
from tabletop_os.application import ApplicationHost, MotionRequest
from tabletop_os.apps.patrol.game import GamePhase
from tabletop_os.apps.patrol.render import render_frame, cell_colors, COLORS
from tabletop_os.frame_protocol import FRAME_BYTES
from tabletop_os.model import InputSample, Observation, Point
from tabletop_os.patrol_simulation import run_patrol_demo
from tabletop_os.system.application_motion import ApplicationMotion, TablePlane
from tabletop_os.system.pieces import PieceRegistry, PieceObservation
from tabletop_os.system.session import MotionOptions
from patrol_support import Harness, config, raw_config, CONFIG_PATH


class PatrolIntegrationTests(unittest.TestCase):
    def harness(self, raw=None):
        harness = Harness(raw)
        self.addCleanup(harness.close)
        return harness

    def test_hover_cancel_grasp_and_drop_share_one_rules_engine(self):
        h = self.harness()
        h.hover()
        self.assertTrue(h.app.preview)
        self.assertEqual(h.app.turn, 0)
        h.sim.hidden.add('player')
        h.advance(.4)
        self.assertTrue(h.app.preview)
        h.sim.hidden.discard('player')
        h.sim.hand = None
        h.advance(1)
        self.assertFalse(h.app.preview)
        self.assertEqual(h.app.turn, 0)
        h.drop((5, 1))
        self.assertEqual(h.app.turn, 1)
        self.assertEqual(h.app.positions['player'], (5, 1))
        self.assertEqual(h.app.positions['guard'], (0, 4))
        h.advance(1)
        self.assertEqual(h.app.turn, 1)
        self.assertEqual(h.runtime.motion.request_count, 1)

    def test_player_tag_5_to_12_guard_independently_to_33(self):
        raw = raw_config()
        raw['pieces']['player']['tag_id'] = 12
        raw['pieces']['guard']['tag_id'] = 33
        h = self.harness(raw)
        h.hover()
        self.assertTrue(h.app.preview)
        h.drop((5, 1))
        self.assertEqual(h.app.turn, 1)
        self.assertEqual(h.runtime.registry.by_id['guard'].tag_id, 33)
        self.assertEqual(h.app.positions['guard'], (0, 4))

    def test_arbitrary_logical_ids_work_in_simulation_preview_and_motion(self):
        raw = raw_config()
        raw['pieces']['infiltrator-2'] = raw['pieces'].pop('player')
        raw['pieces']['sentry-west'] = raw['pieces'].pop('guard')
        raw['map']['initial_positions'] = {'infiltrator-2': [5, 0], 'sentry-west': [0, 3]}
        raw['game'].update(player_id='infiltrator-2', guard_id='sentry-west')
        h = self.harness(raw)
        h.hover()
        self.assertTrue(h.app.preview)
        h.drop((5, 1))
        self.assertEqual(h.app.positions['infiltrator-2'], (5, 1))
        self.assertEqual(h.app.positions['sentry-west'], (0, 4))
        self.assertEqual(h.app.turn, 1)

    def test_invalid_move_latches_until_return_and_does_not_send_guard_request(self):
        h = self.harness()
        h.drop((4, 1))
        self.assertTrue(h.app.needs_return)
        self.assertEqual(h.app.turn, 0)
        h.drop((5, 1))
        self.assertEqual(h.app.turn, 0)
        h.drop((5, 0))
        self.assertFalse(h.app.needs_return)
        h.drop((5, 1))
        self.assertEqual(h.app.turn, 1)

    def test_jitter_one_frame_jump_and_long_occlusion_cannot_commit(self):
        h = self.harness()
        h.hover()
        origin = h.sim.positions['player']
        h.sim.positions['player'] = h.app.rules.grid.center((5, 1))
        h.advance(.05)
        h.sim.positions['player'] = origin
        h.advance(.1)
        self.assertEqual(h.app.turn, 0)
        h.sim.hidden.add('player')
        h.advance(2)
        self.assertEqual(h.app.phase, GamePhase.ERROR_RECOVERY)
        self.assertEqual(h.app.positions['player'], (5, 0))
        self.assertEqual(h.app.turn, 0)

    def test_same_cell_offset_is_accepted_without_stuck_manipulation(self):
        h = self.harness()
        h.hover()
        origin = h.sim.positions['player']
        h.sim.positions['player'] = Point(origin.x+5, origin.y)
        h.sim.hand = None
        h.advance(1.2)
        self.assertEqual(h.app.turn, 0)
        self.assertFalse(h.runtime.proximity.busy)
        h.runtime.reload(h.config)
        h.advance(1)
        self.assertEqual(h.app.phase, GamePhase.PLAYER_TURN)

    def test_configuration_reload_discards_old_identity_positions_and_requires_setup(self):
        h = self.harness()
        raw = raw_config()
        raw['pieces']['player']['tag_id'] = 12
        h.runtime.reload(config(raw))
        self.assertEqual(h.runtime.registry.last, {})
        self.assertEqual(h.app.phase, GamePhase.SETUP)
        h.now += .05
        old = Observation(5, h.runtime.plane.display(h.app.confirmed['player']), h.now)
        h.runtime.tick(InputSample(None, None, h.now), (old,), (), h.now)
        self.assertNotIn('player', h.runtime.registry.last)
        self.assertEqual(h.app.turn, 0)
        h.advance(1)
        self.assertEqual(h.app.phase, GamePhase.PLAYER_TURN)

    def test_invalid_reload_leaves_existing_mapping_active(self):
        h = self.harness()
        old_registry = h.runtime.registry
        invalid = replace(h.config, bindings=replace(h.config.bindings, tag_family='tag25h9'))
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            h.runtime.reload(invalid)
        self.assertIs(h.runtime.registry, old_registry)
        self.assertEqual(h.app.phase, GamePhase.PLAYER_TURN)

    def test_reload_is_refused_during_manipulation_or_guard_motion(self):
        h = self.harness()
        h.hover()
        h.sim.hidden.add('player')
        h.advance(.1)
        with self.assertRaisesRegex(RuntimeError, 'RELOAD_BUSY'):
            h.runtime.reload(h.config)
        h.sim.hidden.clear()
        h.sim.hand = None
        h.advance(1)
        h.drop((5, 1), settle=.8)
        self.assertEqual(h.app.phase, GamePhase.GUARD_MOVING)
        with self.assertRaisesRegex(RuntimeError, 'RELOAD_BUSY'):
            h.runtime.reload(h.config)

    def test_guard_timeout_and_stop_do_not_update_logical_guard_or_replay(self):
        raw = raw_config()
        raw['motion_timeout_s'] = .6
        h = self.harness(raw)
        h.sim.stall = True
        h.drop((5, 1))
        self.assertEqual(h.app.phase, GamePhase.ERROR_RECOVERY)
        self.assertEqual(h.app.positions['guard'], (0, 3))
        self.assertIn('TIMEOUT', h.app.error)
        count = h.runtime.motion.request_count
        h.sim.stall = False
        h.advance(1)
        self.assertEqual(h.runtime.motion.request_count, count)
        self.assertIsNone(h.sim.pending)

    def test_stop_restart_close_release_display_and_application_resources(self):
        h = self.harness()
        h.hover()
        h.runtime.stop()
        self.assertFalse(h.app.preview)
        self.assertFalse(h.runtime.host.running)
        self.assertEqual(h.device.rgb, bytes(FRAME_BYTES))
        h.runtime.reload(h.config)
        h.advance(1)
        self.assertEqual(h.app.phase, GamePhase.PLAYER_TURN)
        self.assertEqual(h.app.turn, 0)
        h.runtime.close()
        h.runtime.close()
        self.assertTrue(h.device.closed)
        self.assertIsNone(h.runtime.host.app)
        self.assertEqual(h.device.rgb, bytes(FRAME_BYTES))

    def test_device_disconnect_stops_active_game(self):
        h = self.harness()
        with patch.object(h.device, 'poll', return_value=[{'type': 'connection.lost'}]):
            h.advance(.05)
        self.assertEqual(h.app.phase, GamePhase.ERROR_RECOVERY)
        self.assertEqual(h.app.error, 'connection.lost')

    def test_unexpected_guard_or_player_during_guard_turn_requires_recovery(self):
        h = self.harness()
        h.sim.positions['guard'] = h.app.rules.grid.center((0, 2))
        h.advance(.5)
        self.assertEqual(h.app.phase, GamePhase.ERROR_RECOVERY)
        self.assertIn('UNEXPECTED_PIECE_POSITION', h.app.error)

    def test_manual_mode_runs_full_turn_without_motor_commands(self):
        from tabletop_os.calibration import Calibration
        from tabletop_os.config import RuntimeConfig
        from tabletop_os.patrol_simulation import LocalDisplayDevice
        from tabletop_os.runtime import ApplicationRuntime
        from tabletop_os.system.session import TabletopSystem
        platform = RuntimeConfig()
        cfg = config()
        device = LocalDisplayDevice()
        device.request = Mock(side_effect=RuntimeError('MUST_NOT_SEND_COMMANDS'))
        mapping = Calibration(64, 96, 63, 95, ((1, 0, 0), (0, 1, 0), (0, 0, 1)))
        runtime = ApplicationRuntime(platform, cfg, TabletopSystem(platform, mapping, device=device), simulate=False)
        self.addCleanup(runtime.close)
        runtime.start()
        positions = runtime.host.app.confirmed.copy()
        now = 0

        def advance(seconds):
            nonlocal now
            for _ in range(round(seconds/.05)):
                now += .05
                observations = tuple(Observation(runtime.registry.by_id[key].tag_id,
                                                 runtime.plane.display(point), now) for key, point in positions.items())
                runtime.tick(InputSample(None, None, now), observations, (), now)

        advance(1)
        positions['player'] = cfg.rules.grid.center((5, 1))
        advance(.7)
        self.assertEqual(runtime.motion.mode, 'manual')
        self.assertEqual(runtime.host.app.phase, GamePhase.GUARD_MOVING)
        self.assertEqual(runtime.host.app.positions['guard'], (0, 3))
        positions['guard'] = cfg.rules.grid.center((0, 4))
        advance(.7)
        self.assertEqual(runtime.host.app.phase, GamePhase.PLAYER_TURN)
        self.assertEqual(runtime.host.app.positions['guard'], (0, 4))
        device.request.assert_not_called()

    def test_runtime_failure_result_stops_motion_and_does_not_move_player_again(self):
        h = self.harness()
        h.drop((5, 1), settle=.8)
        self.assertEqual(h.app.phase, GamePhase.GUARD_MOVING)
        h.sim.positions['player'] = h.app.rules.grid.center((5, 2))
        h.advance(.4)
        self.assertEqual(h.app.phase, GamePhase.ERROR_RECOVERY)
        self.assertEqual(h.app.positions['player'], (5, 1))
        self.assertIsNone(h.sim.pending)

    def test_render_layers_restore_and_current_danger_stays_a_legal_move(self):
        raw = raw_config()
        raw['map']['initial_positions']['player'] = [1, 0]
        raw['game']['guard_facing'] = 'W'
        h = self.harness(raw)
        base = render_frame(h.app)
        h.hover()
        self.assertNotEqual(render_frame(h.app), base)
        self.assertEqual(cell_colors(h.app)[(0, 0)], COLORS['danger'])
        self.assertIn((0, 0), h.app.get_legal_moves('player'))
        h.sim.hand = None
        h.advance(1)
        self.assertEqual(render_frame(h.app), base)
        fallback = render_frame(h.app, red_available=False)
        self.assertEqual(len(fallback), FRAME_BYTES)
        self.assertTrue(all(channel == 0 for channel in fallback[::3]))
        rotated = render_frame(h.app, rotation=180)
        pixels = [base[i:i+3] for i in range(0, len(base), 3)]
        self.assertEqual(rotated, b''.join(reversed(pixels)))

    def test_complete_shared_simulation_demo_wins_after_hover_and_occlusion(self):
        h = self.harness()
        h.runtime.reload(h.config)
        result = run_patrol_demo(h.runtime, 60)
        self.assertEqual(result['phase'], 'GAME_WON')
        self.assertTrue(result['hover_preview_verified'])
        self.assertTrue(result['occlusion_verified'])
        self.assertTrue(result['cancellation_verified'])
        self.assertTrue(result['objective_acquired'])
        self.assertEqual(result['guard_requests'], 6)


class ApplicationMotionTests(unittest.TestCase):
    def test_hardware_boundary_resolves_changed_guard_id_once(self):
        raw = raw_config()
        raw['pieces']['guard']['tag_id'] = 41
        cfg = config(raw)
        registry = PieceRegistry(cfg.bindings, 'DICT_APRILTAG_36h11')
        plane = TablePlane(cfg.rules.grid)
        system = types.SimpleNamespace(options=MotionOptions(enabled=True, pick_place=True),
                                       motion_view=types.SimpleNamespace(phase='IDLE'), request_move=Mock())
        motion = ApplicationMotion(system, registry, plane, cfg.proximity, mode='hardware')
        target = cfg.rules.grid.center((0, 4))
        request = MotionRequest('guard-1', 'guard', target)
        motion.begin(request, 1)
        system.request_move.assert_called_once_with(41, plane.display(target), 1)
        with self.assertRaisesRegex(RuntimeError, 'BUSY'):
            motion.begin(request, 1.1)
        # Motor acknowledgement alone cannot update the game; fresh tag settling is required.
        system.motion_view = types.SimpleNamespace(phase='DONE')
        self.assertIsNone(motion.update(InputSample(None, None, 1.1), 1.1))
        for i in range(12):
            now = 1.2+i*.05
            registry.visible = {'guard': PieceObservation('guard', target, now)}
            result = motion.update(InputSample(None, None, now), now)
            if result:
                break
        self.assertTrue(result.success)
        self.assertEqual(result.token, 'guard-1')
        self.assertEqual(system.request_move.call_count, 1)

    def test_manual_guard_needs_fresh_stable_target_and_release_no_hardware_commands(self):
        cfg = config()
        registry = PieceRegistry(cfg.bindings, 'DICT_APRILTAG_36h11')
        plane = TablePlane(cfg.rules.grid)
        system = types.SimpleNamespace(options=MotionOptions(), request_move=Mock())
        motion = ApplicationMotion(system, registry, plane, cfg.proximity, mode='manual')
        target = cfg.rules.grid.center((0, 4))
        motion.begin(MotionRequest('manual-1', 'guard', target), 1)
        for i in range(10):
            now = 1.1+i*.05
            registry.visible = {'guard': PieceObservation('guard', target, now)}
            self.assertIsNone(motion.update(InputSample(target, None, now), now))
        for i in range(10):
            now = 2+i*.05
            registry.visible = {'guard': PieceObservation('guard', target, now)}
            result = motion.update(InputSample(None, None, now), now)
            if result:
                break
        self.assertTrue(result.success)
        system.request_move.assert_not_called()

    def test_hardware_failure_and_timeout_cancel_without_success(self):
        cfg = config()
        registry = PieceRegistry(cfg.bindings, 'DICT_APRILTAG_36h11')
        plane = TablePlane(cfg.rules.grid)
        system = types.SimpleNamespace(options=MotionOptions(enabled=True, pick_place=True),
                                       motion_view=types.SimpleNamespace(phase='ERROR', error='LIMIT_FAULT'),
                                       request_move=Mock(), cancel=Mock())
        motion = ApplicationMotion(system, registry, plane, cfg.proximity, mode='hardware', timeout_s=1)
        request = MotionRequest('1', 'guard', cfg.rules.grid.center((0, 4)))
        motion.begin(request, 0)
        result = motion.update(InputSample(None, None, .1), .1)
        self.assertFalse(result.success)
        self.assertEqual(result.error, 'LIMIT_FAULT')
        system.motion_view.phase = 'MOVING'
        motion.begin(replace(request, token='2'), 1)
        result = motion.update(InputSample(None, None, 2.1), 2.1)
        self.assertFalse(result.success)
        self.assertIn('TIMEOUT', result.error)
        system.cancel.assert_called_once()


class PatrolCliTests(unittest.TestCase):
    def test_config_only_reads_actual_patrol_document_without_devices(self):
        with contextlib.redirect_stdout(io.StringIO()), patch('tabletop_os.patrol_launch.launch_patrol') as launch:
            self.assertEqual(cli.main(['--app', 'patrol', '--simulate', '--check-config']), 0)
        launch.assert_not_called()

    def test_invalid_hardware_combinations_fail_before_configuration(self):
        cases = [
            ['--app', 'patrol', '--simulate', '--hardware-motion'],
            ['--app', 'patrol', '--simulate', '--led'],
            ['--app', 'patrol', '--input', 'mouse'],
            ['--app', 'patrol', '--allow-pickup-offset'],
            ['--app', 'patrol', '--pick-place'],
            ['--app', 'patrol', '--speed', 'nan'],
            ['--hardware-motion'],
        ]
        for args in cases:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), patch.object(cli, 'load_config') as load:
                with self.assertRaises(SystemExit):
                    cli.main(args)
                load.assert_not_called()


class HostLifecycleTests(unittest.TestCase):
    def test_load_switch_pause_resume_and_unload(self):
        first, second = Mock(), Mock()
        first.take_requests.return_value = ()
        second.take_requests.return_value = ()
        host = ApplicationHost()
        host.load(first)
        first.initialize.assert_called_once()
        host.start()
        host.tick({}, [], None, 1)
        first.update.assert_called_once_with(1)
        host.pause()
        host.tick({}, [], None, 2)
        first.update.assert_called_once()
        host.start()
        host.load(second)
        first.stop.assert_called()
        first.close.assert_called_once()
        host.start()
        host.tick({}, [], None, 3)
        second.update.assert_called_once_with(3)
        host.unload()
        second.close.assert_called_once()
        self.assertIsNone(host.app)


if __name__ == '__main__':
    unittest.main()
