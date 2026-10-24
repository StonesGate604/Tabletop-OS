"""System facade checks using simulated status and no camera or network."""
from dataclasses import replace
import unittest

from tabletop_os.calibration import Calibration
from tabletop_os.config import RuntimeConfig
from tabletop_os.model import DeviceSnapshot, Observation, Point
from tabletop_os.system.session import MotionOptions, TabletopSystem
from tabletop_os.system.tracking import TagMemory


class FakeDevice:
    def __init__(self):
        self.requests = []
        self.frames = []
        self.events = []
        self.poll_count = 0
        self.started = self.closed = False
        self.state = DeviceSnapshot(connected=True, ready=True, referenced=True, mode='IDLE',
                                    position=Point(20,30), fault='NONE', lease_valid=True,
                                    commands_enabled=True, received_at=1, telemetry_age_ms=0)

    def start(self):
        self.started = True

    def snapshot(self):
        return self.state

    def display_status(self):
        return {'mode':'frames'}

    def request(self, kind, **fields):
        request_id = f'request-{len(self.requests)+1}'
        self.requests.append((kind, fields, request_id))
        return request_id

    def poll(self):
        self.poll_count += 1
        events, self.events = self.events, []
        return events

    def publish(self, rgb):
        self.frames.append(rgb)

    def close(self):
        self.closed = True


class SystemServicesTests(unittest.TestCase):
    def setUp(self):
        self.config = RuntimeConfig()
        self.device = FakeDevice()
        self.display = Calibration(640,480,63,63,((1,0,0),(0,1,0),(0,0,1)))
        self.mechanical = Calibration(640,480,342,515,((2,0,0),(0,3,0),(0,0,1)))
        self.display_tag = Observation(7,Point(10,10),1)
        self.mechanical_tag = Observation(7,Point(20,30),1)

    def system(self, enabled=True):
        return TabletopSystem(self.config,self.display,self.mechanical,
                              MotionOptions(enabled=enabled),device=self.device)

    def test_preview_refuses_motion_and_consumes_device_events_once(self):
        system = self.system(enabled=False)
        system.update((self.display_tag,),(self.mechanical_tag,),1)
        self.assertEqual(self.device.poll_count,1)
        self.assertEqual(system.motion_view.phase,'DISABLED')
        with self.assertRaisesRegex(RuntimeError,'MOTION_DISABLED'):
            system.request_move(7,Point(20,10),1)
        self.assertEqual(self.device.requests,[])

    def test_cached_display_tag_never_becomes_a_motion_observation(self):
        system = self.system()
        system.update((self.display_tag,),(self.mechanical_tag,),1)
        self.device.state = replace(self.device.state,received_at=100)
        system.update((),(),100)
        self.assertEqual(system.tracking.cached_ids,frozenset({7}))
        with self.assertRaisesRegex(RuntimeError,'UNCOVER_SELECTED_TAG'):
            system.request_move(7,Point(20,10),100)
        self.assertEqual(self.device.requests,[])

    def test_facade_reprojects_target_without_led_flip_and_reports_display_target(self):
        system = TabletopSystem(self.config,self.display,self.mechanical,
                                 MotionOptions(enabled=True),self.device,rotation=180,flip_x=True)
        system.update((self.display_tag,),(self.mechanical_tag,),1)
        request_id = system.request_move(7,Point(20,10),1)
        self.assertEqual(self.device.requests,[('motion.move_to',
                          {'x_mm':40.0,'y_mm':30.0,'speed_mm_s':2},request_id)])
        view = system.motion_view
        self.assertEqual(view.phase,'MOVING')
        self.assertEqual(view.target,Point(20,10))
        system.update((self.display_tag,),(self.mechanical_tag,),1.1)
        self.assertEqual(len(self.device.requests),1)

    def test_idle_stop_latches_until_explicit_cancel_or_reset(self):
        system = self.system()
        system.update((self.display_tag,),(self.mechanical_tag,),1)
        system.stop()
        self.assertEqual(system.motion_view.phase,'ERROR')
        self.assertEqual(system.motion_view.error,'USER_STOP')
        self.assertEqual(self.device.requests[0][0],'motion.stop')
        with self.assertRaisesRegex(RuntimeError,'RESET_REQUIRED'):
            system.request_move(7,Point(20,10),1)
        system.cancel()
        self.assertEqual(system.motion_view.phase,'IDLE')
        system.request_move(7,Point(20,10),1)
        self.assertEqual(self.device.requests[-1][0],'motion.move_to')

    def test_close_is_idempotent_and_unstarted_device_needs_no_join(self):
        system = self.system()
        system.close()
        system.close()
        self.assertFalse(self.device.started)
        self.assertFalse(self.device.closed)
        self.assertEqual(self.device.requests,[])
        with self.assertRaisesRegex(RuntimeError,'SYSTEM_CLOSED'):
            system.start()

        other = self.system()
        other.start()
        other.update((self.display_tag,),(self.mechanical_tag,),1)
        other.request_move(7,Point(20,10),1)
        other.close()
        other.close()
        self.assertTrue(self.device.closed)
        self.assertEqual(sum(kind == 'motion.stop' for kind,_,_ in self.device.requests),1)
        self.assertEqual(self.device.frames[-1],bytes(64*64*3))

    def test_memory_clear_removes_cached_positions_and_duplicate_identity_state(self):
        memory = TagMemory(self.config.interaction)
        memory.update((self.display_tag,Observation(7,Point(30,30),1)),1)
        self.assertIn(7,memory.ambiguous_ids)
        memory.clear()
        self.assertEqual(memory.display_tags,())
        self.assertEqual(memory.ambiguous_ids,set())
        memory.update((self.display_tag,),1)
        memory.update((),3601)
        self.assertEqual(memory.display_tags,(self.display_tag,))

    def test_motion_requires_compatible_calibrations_before_device_start(self):
        with self.assertRaisesRegex(ValueError,'Mechanical calibration'):
            TabletopSystem(self.config,self.display,options=MotionOptions(enabled=True),device=self.device)
        bad = replace(self.mechanical,width=1280)
        with self.assertRaisesRegex(ValueError,'same camera crop'):
            TabletopSystem(self.config,self.display,bad,MotionOptions(enabled=True),self.device)
        self.assertFalse(self.device.started)


if __name__ == '__main__':
    unittest.main()
