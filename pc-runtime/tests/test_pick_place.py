"""Full calibrated pick-place sequencing without camera, Pi or motors."""
from dataclasses import replace
import unittest

from tabletop_os.config import RuntimeConfig
from tabletop_os.model import DeviceSnapshot, Observation, Point
from tabletop_os.pick_place import PickPlaceDrag


class FakeDevice:
    def __init__(self):
        self.requests = []
        self.state = DeviceSnapshot(
            connected=True, ready=True, referenced=True, mode='IDLE',
            position=Point(20,30), fault='NONE', lease_valid=True,
            commands_enabled=True, received_at=.1, telemetry_age_ms=0)

    def snapshot(self):
        return self.state

    def request(self, kind, **fields):
        request_id = f'request-{len(self.requests)+1}'
        self.requests.append((kind, fields, request_id))
        return request_id

    def refresh(self, now, **fields):
        self.state = replace(self.state, received_at=now, telemetry_age_ms=0, **fields)


class PickPlaceTests(unittest.TestCase):
    def setUp(self):
        base = RuntimeConfig()
        self.config = replace(base, gripper=replace(
            base.gripper, calibrated=True, release_angle=118, engage_angle=81))
        self.device = FakeDevice()
        self.sequence = PickPlaceDrag(self.config, self.device, speed_mm_s=45)
        self.start = Observation(7, Point(80,100), .09)
        self.target = Point(100,110)

    def update(self, now, event=None, observations=(), **status):
        self.device.refresh(now, **status)
        self.sequence.update([event] if event else [], tuple(observations), now)

    def test_complete_sequence_orders_release_seek_engage_drag_release_and_verifies(self):
        self.sequence.begin(self.start, self.target, .1)
        release = self.sequence.request_id
        self.assertEqual(self.device.requests[-1], ('actuator.servo', {'angle':118}, release))

        self.update(.2, {'type':'actuator.completed','reply_to':release})
        self.update(.81, observations=(Observation(7,self.start.position,.8),))
        seek = self.sequence.request_id
        self.assertEqual(self.device.requests[-1], ('motion.move_to',
                         {'x_mm':80,'y_mm':100,'speed_mm_s':45}, seek))

        self.update(1.0, {'type':'motion.completed','reply_to':seek},
                    (Observation(7,self.start.position,.99),), position=self.start.position)
        engage = self.sequence.request_id
        self.assertEqual(self.device.requests[-1], ('actuator.servo', {'angle':81}, engage))

        self.update(1.1, {'type':'actuator.completed','reply_to':engage},
                    position=self.start.position)
        self.update(1.71, observations=(Observation(7,self.start.position,1.7),),
                    position=self.start.position)
        drag = self.sequence.request_id
        self.assertEqual(self.device.requests[-1], ('motion.move_to',
                         {'x_mm':100,'y_mm':110,'speed_mm_s':45}, drag))

        self.update(2.0, {'type':'motion.completed','reply_to':drag},
                    (Observation(7,self.target,1.99),), position=self.target)
        final_release = self.sequence.request_id
        self.assertEqual(self.device.requests[-1],
                         ('actuator.servo', {'angle':118}, final_release))
        self.update(2.1, {'type':'actuator.completed','reply_to':final_release},
                    position=self.target)
        self.update(2.71, position=self.target)
        self.assertEqual(self.sequence.phase, 'VERIFYING')
        for now in (2.72,2.73,2.74):
            self.update(now, observations=(Observation(7,self.target,now),), position=self.target)
        self.assertEqual(self.sequence.phase, 'DONE')
        self.assertEqual([kind for kind,_,_ in self.device.requests],
                         ['actuator.servo','motion.move_to','actuator.servo',
                          'motion.move_to','actuator.servo'])

    def test_home_releases_before_motion_home(self):
        self.device.state = replace(self.device.state, referenced=False, mode='UNREFERENCED')
        self.sequence.home(.1)
        release = self.sequence.request_id
        self.update(.2, {'type':'actuator.completed','reply_to':release},
                    referenced=False, mode='UNREFERENCED')
        self.update(.81, referenced=False, mode='UNREFERENCED')
        home = self.sequence.request_id
        self.assertEqual(self.device.requests,
                         [('actuator.servo',{'angle':118},release),
                          ('motion.home',{},home)])
        self.update(1.0, {'type':'motion.completed','reply_to':home},
                    referenced=True, mode='IDLE', position=Point(2,2))
        self.assertEqual(self.sequence.phase, 'IDLE')

    def test_piece_must_still_be_observed_at_pickup(self):
        self.sequence.begin(self.start, self.target, .1)
        release = self.sequence.request_id
        self.update(.2, {'type':'actuator.completed','reply_to':release})
        self.update(.81)
        seek = self.sequence.request_id
        moved = Observation(7, Point(90,100), .99)
        self.update(1.0, {'type':'motion.completed','reply_to':seek},
                    (moved,), position=self.start.position)
        self.assertEqual(self.sequence.phase, 'ERROR')
        self.assertEqual(self.sequence.error, 'PIECE_MOVED_OR_NOT_OBSERVED_AT_PICKUP')
        self.assertEqual(self.device.requests[-1][0], 'motion.stop')

    def test_uncalibrated_gripper_is_rejected_before_any_command(self):
        with self.assertRaisesRegex(ValueError, 'GRIPPER_NOT_CALIBRATED'):
            PickPlaceDrag(RuntimeConfig(), self.device)
        self.assertEqual(self.device.requests, [])

    def test_fresh_observation_home_workspace_and_distance_gate_every_sequence(self):
        self.device.state = replace(self.device.state, referenced=False, mode='UNREFERENCED')
        with self.assertRaisesRegex(RuntimeError, 'DEVICE_NOT_READY'):
            self.sequence.begin(self.start,self.target,.1)
        self.device.state = replace(self.device.state, referenced=True, mode='IDLE')
        with self.assertRaisesRegex(RuntimeError, 'PIECE_OBSERVATION_STALE'):
            self.sequence.begin(replace(self.start,timestamp=-1),self.target,.1)
        with self.assertRaisesRegex(RuntimeError, 'POINT_OUTSIDE_WORKSPACE'):
            self.sequence.begin(self.start,Point(1,100),.1)
        capped = PickPlaceDrag(self.config,self.device,speed_mm_s=45,max_drag_mm=5)
        with self.assertRaisesRegex(RuntimeError, 'DRAG_DISTANCE_EXCEEDED'):
            capped.begin(self.start,self.target,.1)
        self.assertEqual(self.device.requests, [])

    def test_disconnect_stops_once_and_reconnect_does_not_replay(self):
        self.sequence.begin(self.start,self.target,.1)
        self.update(.2,{'type':'connection.lost'},connected=False)
        self.assertEqual(self.sequence.phase,'ERROR')
        self.assertEqual([kind for kind,_,_ in self.device.requests],
                         ['actuator.servo','motion.stop'])
        self.update(.3,connected=True)
        self.assertEqual([kind for kind,_,_ in self.device.requests],
                         ['actuator.servo','motion.stop'])


if __name__ == '__main__':
    unittest.main()
