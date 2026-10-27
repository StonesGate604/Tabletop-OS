"""Operator-host tests using fake windows and a fake system; no real I/O."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch

from tabletop_os.calibration import Calibration
from tabletop_os.config import RuntimeConfig
from tabletop_os.gesture_ui import run_gesture_window
from tabletop_os.model import DeviceSnapshot, InputSample
from tabletop_os.system.tracking import TagMemory


@unittest.skipUnless(importlib.util.find_spec('numpy'), 'Vision dependencies unavailable')
class GestureWindowTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.config = RuntimeConfig()
        self.frame = np.ones((10,20,3),dtype=np.uint8)
        self.mapping = Calibration(20,10,63,63,((1,0,0),(0,1,0),(0,0,1)))
        self.camera = Mock()
        self.camera.size = (20,10)
        self.camera.read.return_value = self.frame
        self.camera.analyze.side_effect = lambda *args: (
            InputSample(None,None,time.monotonic()), (), (), None)
        self.service = Mock()
        self.service.tracking = TagMemory(self.config.interaction)
        self.service.update.side_effect = lambda observations, mechanical, now: self.service.tracking.update(observations,now)
        self.service.motion_view = SimpleNamespace(enabled=False,phase='DISABLED',error='',selected_id=None,target=None)
        self.service.snapshot.return_value = DeviceSnapshot()
        self.service.display_status.return_value = {}
        self.service.publish.return_value = bytes(64*64*3)
        self.cv = SimpleNamespace(
            namedWindow=Mock(),setMouseCallback=Mock(),putText=Mock(),imshow=Mock(),
            waitKey=Mock(side_effect=[27]),getWindowProperty=Mock(return_value=1),
            destroyAllWindows=Mock(),cvtColor=lambda image,code:image,
            resize=lambda image,size,**kwargs:image,circle=Mock(),rectangle=Mock(),polylines=Mock(),
            FONT_HERSHEY_SIMPLEX=0,COLOR_RGB2BGR=0,INTER_NEAREST=0,EVENT_LBUTTONDOWN=1,
            WND_PROP_VISIBLE=0)
        self.options = SimpleNamespace(
            constant_engage=False,pick_place=False,speed=2,max_drag_mm=0,allow_pickup_offset=False,
            rotation=None,flip_x=None,hold_seconds=0,reuse_map=True)
        self.enterContext(patch.dict('sys.modules',{'cv2':self.cv}))
        self.enterContext(patch('tabletop_os.gesture_ui.CameraSession',return_value=self.camera))
        self.factory = self.enterContext(patch('tabletop_os.gesture_ui.TabletopSystem',return_value=self.service))
        self.enterContext(patch.object(Calibration,'load',return_value=self.mapping))
        self.enterContext(patch.object(Path,'exists',return_value=False))
        self.enterContext(patch.object(Path,'write_text',return_value=0))

    def assert_cleanup(self):
        self.camera.close.assert_called_once_with()
        self.cv.destroyAllWindows.assert_called_once_with()

    def test_existing_mapping_runs_app_and_publishes_scene_then_closes(self):
        run_gesture_window(self.config,self.options)
        self.service.start.assert_called_once_with()
        self.service.update.assert_called_once()
        self.service.publish.assert_called_once()
        scene = self.service.publish.call_args.args[0]
        self.assertIsNone(scene.cursor)
        self.assertEqual(scene.tags,())
        self.assertFalse(self.factory.call_args.args[3].enabled)
        self.assertEqual(self.factory.call_args.kwargs['rotation'],180)
        self.service.close.assert_called_once_with()
        self.assert_cleanup()

    def test_remap_closes_old_session_without_creating_another_connection(self):
        self.cv.waitKey.side_effect = [ord('r'),27]
        run_gesture_window(self.config,self.options)
        self.assertEqual(self.factory.call_count,1)
        self.service.close.assert_called_once_with()
        self.service.publish.assert_called_once()
        self.assert_cleanup()

    def test_capture_failure_during_active_window_closes_system_and_camera(self):
        self.camera.analyze.side_effect = RuntimeError('camera disconnected')
        with self.assertRaisesRegex(RuntimeError,'camera disconnected'):
            run_gesture_window(self.config,self.options)
        self.service.close.assert_called_once_with()
        self.service.publish.assert_not_called()
        self.assert_cleanup()

    def test_failure_after_remap_does_not_close_old_session_twice(self):
        self.cv.waitKey.side_effect = [ord('r')]
        self.camera.read.side_effect = [self.frame,RuntimeError('read failed')]
        with self.assertRaisesRegex(RuntimeError,'read failed'):
            run_gesture_window(self.config,self.options)
        self.service.close.assert_called_once_with()
        self.assert_cleanup()

    def test_unsupported_mechanical_calibration_rejected_before_connection(self):
        self.options.constant_engage = True
        self.camera.validate_calibration.side_effect = ValueError('plane-only calibration required')
        with self.assertRaisesRegex(ValueError,'plane-only'):
            run_gesture_window(self.config,self.options)
        self.factory.assert_not_called()
        self.service.start.assert_not_called()
        self.assert_cleanup()

    def test_connection_start_failure_closes_partial_service(self):
        self.service.start.side_effect = RuntimeError('connection failed')
        with self.assertRaisesRegex(RuntimeError,'connection failed'):
            run_gesture_window(self.config,self.options)
        self.service.close.assert_called_once_with()
        self.assert_cleanup()

    def test_system_close_failure_still_closes_camera_and_windows(self):
        self.service.close.side_effect = RuntimeError('service cleanup failed')
        with self.assertRaisesRegex(RuntimeError,'service cleanup failed'):
            run_gesture_window(self.config,self.options)
        self.assert_cleanup()

    def test_cancel_and_stop_use_application_system_api(self):
        self.cv.waitKey.side_effect = [ord('c'),ord(' '),27]
        run_gesture_window(self.config,self.options)
        self.service.cancel.assert_called_once_with()
        self.service.stop.assert_called_once_with()
        self.assertEqual(self.service.publish.call_count,3)
        self.assert_cleanup()

    def test_pick_place_home_key_uses_system_service(self):
        self.options.pick_place = True
        self.cv.waitKey.side_effect = [ord('h'),27]
        run_gesture_window(self.config,self.options)
        self.service.home.assert_called_once()
        self.assertTrue(self.factory.call_args.args[3].pick_place)
        self.assert_cleanup()

    def test_four_click_mapping_is_saved_before_starting_one_session(self):
        self.options.reuse_map = False
        callback = None
        def remember_callback(title,value):
            nonlocal callback
            callback = value
        self.cv.setMouseCallback.side_effect = remember_callback
        def key(delay):
            if self.factory.called:
                return 27
            for x,y in ((0,0),(19,0),(19,9),(0,9)):
                callback(self.cv.EVENT_LBUTTONDOWN,x,y,0,None)
            return ord('s')
        self.cv.waitKey.side_effect = key
        with patch('tabletop_os.gesture_ui.from_corners',return_value=self.mapping) as calibrate, patch.object(Calibration,'save') as save:
            run_gesture_window(self.config,self.options)
        self.assertEqual(calibrate.call_args.args[0],[(0,0),(19,0),(19,9),(0,9)])
        self.assertEqual(calibrate.call_args.args[1],(20,10))
        save.assert_called_once_with(self.config.base_dir/'local-display-map.json')
        self.factory.assert_called_once()
        self.service.start.assert_called_once_with()
        self.service.publish.assert_called_once()
        self.assert_cleanup()


if __name__ == '__main__':
    unittest.main()
