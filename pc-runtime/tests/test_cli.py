"""Unified application selection without opening cameras or network sessions."""
import contextlib
import importlib.util
import io
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

from tabletop_os import cli
from tabletop_os.config import RuntimeConfig


class UnifiedCliTests(unittest.TestCase):
    def run_cli(self, arguments):
        fake_ui = types.ModuleType('tabletop_os.gesture_ui')
        fake_ui.run_gesture_window = Mock()
        with patch.dict('sys.modules', {'tabletop_os.gesture_ui': fake_ui}), \
             patch.object(cli, 'load_config', return_value=RuntimeConfig()) as load, \
             patch.object(cli, 'configure_logging'), patch.object(cli, 'Runtime') as runtime:
            result = cli.main(arguments)
        return result, fake_ui.run_gesture_window, load, runtime

    def test_default_program_launches_preview_application_without_legacy_runtime(self):
        result, window, _, runtime = self.run_cli([])
        self.assertEqual(result, 0)
        runtime.assert_not_called()
        options = window.call_args.args[1]
        self.assertEqual(options.app, 'gesture-drag')
        self.assertEqual(options.input, 'camera')
        self.assertFalse(options.constant_engage)
        self.assertFalse(options.pick_place)
        self.assertFalse(options.allow_pickup_offset)
        self.assertEqual(options.speed, 10)

    def test_hardware_options_are_explicit_and_forwarded(self):
        result, window, load, _ = self.run_cli([
            '--app','gesture-drag','--config','my-config.json','--reuse-map',
            '--constant-engage','--allow-pickup-offset','--speed','50'])
        self.assertEqual(result, 0)
        load.assert_called_once_with(Path('my-config.json'))
        options = window.call_args.args[1]
        self.assertTrue(options.constant_engage)
        self.assertTrue(options.reuse_map)
        self.assertEqual(options.speed, 50)

    def test_calibrated_pick_place_mode_is_forwarded(self):
        result, window, _, _ = self.run_cli([
            '--app','gesture-drag','--reuse-map','--pick-place','--speed','45'])
        self.assertEqual(result, 0)
        options = window.call_args.args[1]
        self.assertTrue(options.pick_place)
        self.assertFalse(options.constant_engage)
        self.assertEqual(options.speed, 45)

    def test_check_config_does_not_open_application_or_devices(self):
        with contextlib.redirect_stdout(io.StringIO()):
            result, window, _, runtime = self.run_cli(['--check-config'])
        self.assertEqual(result, 0)
        window.assert_not_called()
        runtime.assert_not_called()

    def test_original_simulation_command_still_runs_original_application(self):
        with patch.object(cli,'load_config',return_value=RuntimeConfig()), \
             patch.object(cli,'configure_logging'), patch.object(cli,'Runtime') as runtime, \
             patch.object(cli,'run_demo',return_value={'completed_moves':1}) as demo, \
             contextlib.redirect_stdout(io.StringIO()):
            result = cli.main(['--simulate','--headless','--demo'])
        self.assertEqual(result,0)
        runtime.assert_called_once()
        self.assertTrue(runtime.call_args.args[1])
        demo.assert_called_once()

    def test_original_hardware_mode_is_selectable_explicitly(self):
        fake_ui = types.ModuleType('tabletop_os.ui')
        fake_ui.run_window = Mock()
        with patch.dict('sys.modules',{'tabletop_os.ui':fake_ui}), \
             patch.object(cli,'load_config',return_value=RuntimeConfig()), \
             patch.object(cli,'configure_logging'), patch.object(cli,'Runtime') as runtime:
            result = cli.main(['--app','single-piece','--input','camera'])
        self.assertEqual(result,0)
        runtime.assert_called_once()
        fake_ui.run_window.assert_called_once_with(runtime.return_value,True)

    def test_invalid_or_mixed_modes_fail_before_config_or_devices(self):
        cases = [
            ['--app','gesture-drag','--simulate'],
            ['--app','gesture-drag','--input','mouse'],
            ['--app','single-piece','--constant-engage'],
            ['--app','single-piece','--pick-place'],
            ['--app','single-piece','--reuse-map'],
            ['--pick-place','--allow-pickup-offset'],
            ['--pick-place','--constant-engage'],
            ['--speed','nan'], ['--speed','50.1'], ['--hold-seconds','inf'],
            ['--max-drag-mm','-1'],
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()), \
                 patch.object(cli,'load_config') as load:
                with self.assertRaises(SystemExit):
                    cli.main(arguments)
                load.assert_not_called()

    def test_legacy_script_only_forwards_to_unified_entry(self):
        path = Path(__file__).resolve().parents[1]/'tools/hand_pointer.py'
        spec = importlib.util.spec_from_file_location('legacy_gesture_launcher',path)
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        with patch.object(cli,'main',return_value=0) as entry:
            self.assertEqual(launcher.main(['--reuse-map','--speed','15']),0)
        entry.assert_called_once_with(['--app','gesture-drag','--reuse-map','--speed','15'])


if __name__ == '__main__':
    unittest.main()
