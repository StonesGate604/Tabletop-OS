"""Safety and observed-result checks for constant-engagement dragging; no hardware."""
from dataclasses import replace
import unittest

from tabletop_os.config import RuntimeConfig
from tabletop_os.constant_engage import ConstantEngageDrag
from tabletop_os.model import DeviceSnapshot, Observation, Point


class FakeDevice:
    def __init__(self):
        self.requests = []
        self.state = DeviceSnapshot(
            connected=True, ready=True, referenced=True, mode='IDLE',
            position=Point(80,100), fault='NONE', lease_valid=True,
            commands_enabled=True, received_at=.1, telemetry_age_ms=0)

    def snapshot(self):
        return self.state

    def request(self, kind, **fields):
        request_id = f'request-{len(self.requests)+1}'
        self.requests.append((kind, fields, request_id))
        return request_id

    def refresh(self, now, **fields):
        self.state = replace(self.state, received_at=now, telemetry_age_ms=0, **fields)


class ConstantEngageTests(unittest.TestCase):
    def setUp(self):
        self.config = RuntimeConfig()
        self.device = FakeDevice()
        self.drag = ConstantEngageDrag(self.config,self.device)
        self.start = Observation(7,Point(80,100),.09)
        self.target = Point(90,100)

    def begin(self):
        self.drag.begin(self.start,self.target,.1)

    def update(self, now, events=(), observations=(), refresh=True):
        if refresh:
            self.device.refresh(now)
        self.drag.update(list(events),tuple(observations),now)

    def assert_only_one_move(self):
        self.assertEqual(sum(kind == 'motion.move_to' for kind,_,_ in self.device.requests),1)
        self.assertFalse(any(kind in ('motion.home','actuator.servo','actuator.servo_off')
                             for kind,_,_ in self.device.requests))

    def test_uncalibrated_gripper_is_allowed_and_exactly_one_low_speed_move_is_sent(self):
        self.assertFalse(self.config.gripper.calibrated)
        self.begin()
        self.assertEqual(self.drag.phase,'MOVING')
        self.assertEqual(self.drag.selected_id,7)
        self.assertEqual(self.drag.target,self.target)
        self.assertEqual(self.device.requests,[('motion.move_to',{'x_mm':90,'y_mm':100,'speed_mm_s':2},self.drag.request_id)])
        for now in (.2,.3,.4):
            self.update(now)
        self.assert_only_one_move()
        with self.assertRaises(RuntimeError):
            self.drag.begin(self.start,self.target,.4)
        self.assert_only_one_move()

    def test_not_homed_stale_telemetry_and_disabled_commands_reject_before_move(self):
        cases = (
            dict(referenced=False), dict(ready=False), dict(connected=False),
            dict(lease_valid=False), dict(commands_enabled=False),
            dict(in_flight=True), dict(fault='LIMIT'),
            dict(telemetry_age_ms=501), dict(received_at=-1),
        )
        for fields in cases:
            with self.subTest(fields=fields):
                device = FakeDevice()
                device.state = replace(device.state,**fields)
                drag = ConstantEngageDrag(self.config,device)
                with self.assertRaisesRegex(RuntimeError,'DEVICE_NOT_READY'):
                    drag.begin(self.start,self.target,.1)
                self.assertEqual(drag.phase,'IDLE')
                self.assertEqual(device.requests,[])

    def test_stale_cached_or_future_tag_cannot_authorize_motion(self):
        for timestamp in (-3600,.11):
            with self.subTest(timestamp=timestamp):
                observation = Observation(7,self.start.position,timestamp)
                with self.assertRaisesRegex(RuntimeError,'PIECE_OBSERVATION_STALE'):
                    self.drag.begin(observation,self.target,.1)
                self.assertEqual(self.device.requests,[])
                self.assertEqual(self.drag.phase,'IDLE')

    def test_alignment_workspace_and_drag_distance_are_checked_before_sending(self):
        self.drag = ConstantEngageDrag(self.config,self.device,max_drag_mm=20)
        cases = (
            (Observation(7,Point(90,100),.09),Point(95,100),'PICKUP_POSITION_MISMATCH'),
            (self.start,Point(1,100),'POINT_OUTSIDE_WORKSPACE'),
            (Observation(7,Point(0,100),.09),Point(10,100),'POINT_OUTSIDE_WORKSPACE'),
            (self.start,Point(101,100),'DRAG_DISTANCE_EXCEEDED'),
        )
        for observation,target,error in cases:
            with self.subTest(error=error):
                with self.assertRaisesRegex(RuntimeError,error):
                    self.drag.begin(observation,target,.1)
                self.assertEqual(self.device.requests,[])
                self.assertEqual(self.drag.phase,'IDLE')

    def test_default_allows_drag_beyond_twenty_millimetres_within_workspace(self):
        target = Point(180,100)
        self.drag.begin(self.start,target,.1)
        self.assertEqual(self.drag.phase,'MOVING')
        self.assertEqual(self.device.requests,[('motion.move_to',
                         {'x_mm':180,'y_mm':100,'speed_mm_s':2},self.drag.request_id)])
        self.assert_only_one_move()

    def test_uncapped_drag_still_rejects_outside_workspace_without_command(self):
        for target in (Point(-1,100),Point(343,100),Point(80,516)):
            with self.subTest(target=target):
                with self.assertRaisesRegex(RuntimeError,'POINT_OUTSIDE_WORKSPACE'):
                    self.drag.begin(self.start,target,.1)
                self.assertEqual(self.drag.phase,'IDLE')
                self.assertEqual(self.device.requests,[])

    def test_pickup_offset_is_rejected_by_default(self):
        observation = Observation(7,Point(250,100),.09)
        with self.assertRaisesRegex(RuntimeError,'PICKUP_POSITION_MISMATCH'):
            self.drag.begin(observation,self.target,.1)
        self.assertEqual(self.drag.phase,'IDLE')
        self.assertEqual(self.device.requests,[])

    def test_pickup_offset_opt_in_sends_only_one_move_directly_to_target(self):
        self.drag = ConstantEngageDrag(self.config,self.device,allow_pickup_offset=True)
        observation = Observation(7,Point(250,100),.09)
        self.drag.begin(observation,self.target,.1)
        self.assertEqual(self.drag.phase,'MOVING')
        self.assertEqual(self.device.requests,[('motion.move_to',
                         {'x_mm':90,'y_mm':100,'speed_mm_s':2},self.drag.request_id)])
        self.assert_only_one_move()

    def test_pickup_offset_opt_in_keeps_fresh_observation_home_and_workspace_gates(self):
        cases = (
            (Observation(7,Point(250,100),-3600),self.target,True,'PIECE_OBSERVATION_STALE'),
            (Observation(7,Point(250,100),.09),self.target,False,'DEVICE_NOT_READY'),
            (Observation(7,Point(250,100),.09),Point(350,100),True,'POINT_OUTSIDE_WORKSPACE'),
            (Observation(7,Point(350,100),.09),self.target,True,'POINT_OUTSIDE_WORKSPACE'),
        )
        for observation,target,referenced,error in cases:
            with self.subTest(error=error,position=observation.position):
                device = FakeDevice()
                device.state = replace(device.state,referenced=referenced)
                drag = ConstantEngageDrag(self.config,device,allow_pickup_offset=True)
                with self.assertRaisesRegex(RuntimeError,error):
                    drag.begin(observation,target,.1)
                self.assertEqual(drag.phase,'IDLE')
                self.assertEqual(device.requests,[])

    def test_large_pickup_offset_still_uses_actuator_distance_for_timeout(self):
        self.drag = ConstantEngageDrag(self.config,self.device,allow_pickup_offset=True)
        # Tag is 10 mm from target, but the actuator must travel 220 mm.
        self.drag.begin(Observation(7,Point(290,100),.09),Point(300,100),.1)
        for now in (90.2,119.9):
            self.update(now)
            self.assertEqual(self.drag.phase,'MOVING')
        self.update(120.2)
        self.assertEqual(self.drag.phase,'ERROR')
        self.assertEqual(self.drag.error,'MOTION_TIMEOUT')
        self.assert_only_one_move()
        self.assertEqual(sum(kind == 'motion.stop' for kind,_,_ in self.device.requests),1)

    def test_explicit_cap_covers_actual_motor_travel_when_pickup_offset_is_allowed(self):
        self.drag = ConstantEngageDrag(self.config,self.device,max_drag_mm=20,allow_pickup_offset=True)
        # Tag-to-target is only 10 mm, but the motor would travel 200 mm.
        observation = Observation(7,Point(270,100),.09)
        with self.assertRaisesRegex(RuntimeError,'DRAG_DISTANCE_EXCEEDED'):
            self.drag.begin(observation,Point(280,100),.1)
        self.assertEqual(self.drag.phase,'IDLE')
        self.assertEqual(self.device.requests,[])

    def test_long_drag_uses_distance_and_speed_deadline_instead_of_old_ninety_seconds(self):
        # Actuator-to-target distance is 220 mm: 110 s at 2 mm/s plus 10 s slack.
        # The observed tag is 2 mm ahead but must not shorten this deadline.
        observation = Observation(7,Point(82,100),.09)
        self.drag.begin(observation,Point(300,100),.1)
        for now in (90.2,119.2,120.0):
            self.update(now)
            self.assertEqual(self.drag.phase,'MOVING')
        self.update(120.2)
        self.assertEqual(self.drag.phase,'ERROR')
        self.assertEqual(self.drag.error,'MOTION_TIMEOUT')
        self.assert_only_one_move()
        self.assertEqual(sum(kind == 'motion.stop' for kind,_,_ in self.device.requests),1)

    def test_completion_requires_three_new_tag_observations_before_success(self):
        self.begin()
        request_id = self.drag.request_id
        self.update(.2,events=({'type':'motion.completed','reply_to':'other'},))
        self.assertEqual(self.drag.phase,'MOVING')
        self.update(.3,events=({'type':'motion.completed','reply_to':request_id},),
                    observations=(Observation(7,self.target,.29),))
        self.assertEqual(self.drag.phase,'VERIFYING')
        self.assertEqual(self.drag.verified_samples,0)
        for index,now in enumerate((.31,.32,.33),1):
            self.update(now,observations=(Observation(7,self.target,now),))
            self.assertEqual(self.drag.verified_samples,index)
            self.assertEqual(self.drag.phase,'DONE' if index == 3 else 'VERIFYING')
        self.assert_only_one_move()

    def test_verification_rejects_repeated_timestamps_wrong_ids_and_wrong_positions(self):
        self.begin()
        self.update(.2,events=({'type':'motion.completed','reply_to':self.drag.request_id},))
        self.update(.21,observations=(Observation(7,self.target,.21),))
        self.assertEqual(self.drag.verified_samples,1)
        self.update(.22,observations=(Observation(7,self.target,.21),))
        self.assertEqual(self.drag.verified_samples,1)
        self.update(.23,observations=(Observation(99,self.target,.23),))
        self.assertNotEqual(self.drag.phase,'DONE')
        self.update(.24,observations=(Observation(7,Point(110,100),.24),))
        self.assertEqual(self.drag.verified_samples,0)
        self.update(.25,observations=(Observation(7,self.target,.25),Observation(7,self.target,.25)))
        self.assertEqual(self.drag.verified_samples,0)
        self.assertNotEqual(self.drag.phase,'DONE')
        self.assert_only_one_move()

    def test_disconnect_stops_once_and_reconnect_does_not_replay(self):
        self.begin()
        self.update(.2,events=({'type':'connection.lost'},))
        self.assertEqual(self.drag.phase,'ERROR')
        self.update(.3)
        self.update(.4,events=({'type':'connection.lost'},))
        self.assert_only_one_move()
        self.assertEqual(sum(kind == 'motion.stop' for kind,_,_ in self.device.requests),1)
        self.drag.reset()
        self.assertEqual(self.drag.phase,'IDLE')
        self.update(.5)
        self.assert_only_one_move()

    def test_fault_stale_telemetry_and_motion_timeout_stop_active_drag(self):
        for failure in ('fault','stale','timeout'):
            with self.subTest(failure=failure):
                self.setUp()
                self.begin()
                if failure == 'fault':
                    self.device.state = replace(self.device.state,fault='LIMIT')
                    self.update(.2,refresh=False)
                elif failure == 'stale':
                    self.update(.7,refresh=False)
                else:
                    self.update(self.config.interaction.command_timeout_s+.2)
                self.assertEqual(self.drag.phase,'ERROR')
                self.assertTrue(self.drag.error)
                self.assert_only_one_move()
                self.assertEqual(sum(kind == 'motion.stop' for kind,_,_ in self.device.requests),1)

    def test_no_tag_after_completion_times_out_instead_of_using_display_cache(self):
        self.begin()
        self.update(.2,events=({'type':'motion.completed','reply_to':self.drag.request_id},))
        self.update(.3,observations=(self.start,))
        self.assertEqual(self.drag.phase,'VERIFYING')
        self.update(self.config.interaction.verification_timeout_s+.3)
        self.assertEqual(self.drag.phase,'ERROR')
        self.assert_only_one_move()
        self.assertEqual(sum(kind == 'motion.stop' for kind,_,_ in self.device.requests),1)


if __name__ == '__main__':
    unittest.main()
