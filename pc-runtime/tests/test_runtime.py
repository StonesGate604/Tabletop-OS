from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest

from tabletop_os.cli import run_demo
from tabletop_os.config import RuntimeConfig, load_config
from tabletop_os.gestures import PinchDetector
from tabletop_os.interaction import DemoController, Phase
from tabletop_os.model import Point, Observation, InputSample, DeviceSnapshot
from tabletop_os.runtime import Runtime


class PinchTests(unittest.TestCase):
    def test_hysteresis_debounce_and_no_repeated_begin(self):
        d = PinchDetector(.25, .4, 60)
        self.assertIsNone(d.update(.9, 0))
        self.assertIsNone(d.update(.9, .1))
        self.assertIsNone(d.update(.2, .2))
        self.assertEqual(d.update(.2, .3), 'begin')
        self.assertIsNone(d.update(.3, .4))
        self.assertIsNone(d.update(.1, .5))
        self.assertIsNone(d.update(.8, .6))
        self.assertEqual(d.update(.8, .7), 'end')

    def test_arrival_pinched_and_lost_hand_must_rearm(self):
        d = PinchDetector(.25, .4, 0)
        self.assertIsNone(d.update(.1, 0))
        self.assertEqual(d.update(None, 1), 'lost')
        self.assertIsNone(d.update(.1, 2))
        self.assertIsNone(d.update(.9, 3))
        self.assertEqual(d.update(.1, 4), 'begin')


class InteractionTests(unittest.TestCase):
    def setUp(self):
        self.c = DemoController(RuntimeConfig())
        self.p = self.c.piece

    def select_and_commit(self):
        self.c.observe(Observation(0, self.p, 1), 1)
        self.c.input(self.p, 'begin', 1)
        self.c.input(self.p, 'end', 1.1)
        self.c.input(Point(120, 150), 'begin', 1.2)
        self.c.submitted('move')

    def test_selection_requires_observation(self):
        self.c.input(self.p, 'begin', 1)
        self.assertEqual(self.c.phase, Phase.IDLE)
        self.c.observe(Observation(1, self.p, 1), 1)
        self.c.input(self.p, 'begin', 1)
        self.assertEqual(self.c.phase, Phase.IDLE)

    def test_second_pinch_creates_exactly_one_intent(self):
        self.select_and_commit()
        self.assertIsNotNone(self.c.take_intent())
        for t in range(10):
            self.c.input(Point(200, 200), 'begin', 1.2+t/100)
            self.assertIsNone(self.c.take_intent())

    def test_tracking_loss_cancels_before_commit(self):
        self.c.observe(Observation(0, self.p, 1), 1)
        self.c.input(self.p, 'begin', 1)
        self.c.input(None, 'lost', 1.1)
        self.assertEqual(self.c.phase, Phase.IDLE)
        self.assertIsNone(self.c.take_intent())

    def test_completed_waits_for_distinct_new_observations(self):
        self.select_and_commit()
        target = self.c.target
        self.c.event({'type':'motion.completed','reply_to':'other'}, 2)
        self.assertEqual(self.c.phase, Phase.COMMITTED)
        self.c.event({'type':'motion.completed','reply_to':'move'}, 2)
        self.assertEqual(self.c.phase, Phase.VERIFYING)
        self.assertEqual(self.c.piece, self.p)
        self.c.observe(Observation(0, target, 1.9), 2)
        for _ in range(5):
            self.c.observe(Observation(0, target, 2.1), 2.1)
        self.assertEqual(self.c.completed_moves, 0)
        self.c.observe(Observation(0, target, 2.2), 2.2)
        self.c.observe(Observation(0, target, 2.3), 2.3)
        self.assertEqual(self.c.completed_moves, 1)
        self.assertEqual(self.c.piece, target)

    def test_mismatch_or_lost_tag_never_commits(self):
        self.select_and_commit()
        self.c.event({'type':'motion.completed','reply_to':'move'}, 2)
        self.c.observe(Observation(0, self.p, 2.1), 2.1)
        self.c.tick(8)
        self.assertEqual(self.c.error, 'VERIFICATION_TIMEOUT')
        self.assertEqual(self.c.piece, self.p)

    def test_timeout_does_not_replay(self):
        self.select_and_commit()
        self.c.take_intent()
        self.c.tick(1000)
        self.assertEqual(self.c.phase, Phase.ERROR)
        self.c.reset()
        self.assertIsNone(self.c.take_intent())

    def test_gap_between_observations_resets_stability(self):
        self.select_and_commit()
        self.c.event({'type':'motion.completed','reply_to':'move'},2)
        for timestamp in (2.1,2.2,3.0):
            self.c.observe(Observation(0,self.c.target,timestamp),timestamp)
        self.assertEqual(self.c.completed_moves,0)
        self.assertEqual(self.c.verified_samples,1)


class EndToEndSimulationTests(unittest.TestCase):
    def test_complete_action_has_pickup_drag_release_and_trail(self):
        r = Runtime(RuntimeConfig(), simulate=True)
        result = run_demo(r, 60)
        self.assertEqual(result['completed_moves'], 1)
        self.assertEqual(result['piece_mm'], result['target_mm'])
        self.assertEqual(result['motion_commands'], 2)  # seek and drag
        self.assertGreater(result['peak_trail_points'], 0)
        kinds = [kind for kind, _ in r.device.history]
        self.assertEqual(kinds, ['actuator.servo','motion.move_to','actuator.servo','motion.move_to','actuator.servo'])

    def test_magnet_slip_detected_by_observation(self):
        r = Runtime(RuntimeConfig(), simulate=True)
        r.device.slip = True
        result = run_demo(r, 60)
        self.assertEqual(result['completed_moves'], 0)
        self.assertEqual(result['error'], 'VERIFICATION_TIMEOUT')

    def test_unmarked_real_gripper_cannot_run_sequence(self):
        # Use virtual device but hardware-mode Runtime: calibrated flag must not be overridden.
        from tabletop_os.simulation import SimulationDevice
        r = Runtime(RuntimeConfig(), simulate=False, mouse=True, device=SimulationDevice(RuntimeConfig()))
        for tick, ratio, point in [(0,.9,r.controller.piece),(1,.1,r.controller.piece),
                                   (2,.9,r.controller.piece),(3,.1,Point(120,150))]:
            now = tick/10
            r.device.tick(now)
            r.tick(InputSample(point, ratio, now), (Observation(0,r.controller.piece,now),), now)
        self.assertEqual(r.controller.error, 'GRIPPER_NOT_CALIBRATED')
        self.assertEqual(r.device.history, [])

    def test_stop_mid_motion_cancels_and_reset_does_not_replay(self):
        from tabletop_os.cli import scripted_input
        r = Runtime(RuntimeConfig(), simulate=True)
        start = r.controller.piece
        for tick in range(180):
            now = tick/60
            r.tick(scripted_input(now,start,Point(200,300)), (), now)
        count = r.device.move_count
        r.stop()
        r.tick(InputSample(None,None,3.1), (), 3.1)
        r.reset()
        for tick in range(10):
            r.tick(InputSample(None,None,4+tick/60), (), 4+tick/60)
        self.assertEqual(r.device.move_count, count)
        self.assertEqual(r.controller.completed_moves, 0)

    def test_home_releases_magnet_before_homing(self):
        r=Runtime(RuntimeConfig(),simulate=True)
        r.device.tick(1)
        r.home(1)
        for tick in range(2000):
            now=1+tick/60
            r.tick(InputSample(None,None,now),(),now)
            if not r.home_stage:
                break
        self.assertEqual(r.device.history[0],('actuator.servo',{'angle':60}))
        self.assertEqual(r.device.history[1][0],'motion.home')
        self.assertEqual(r.home_stage,'')
        self.assertEqual(r.controller.completed_moves,0)

    def test_one_stalled_input_frame_cannot_debounce_into_pinch(self):
        r=Runtime(RuntimeConfig(),simulate=True)
        p=r.controller.piece
        for t in (0,.1):
            r.tick(InputSample(p,.9,t),(),t)
        r.tick(InputSample(p,.1,.2),(),.2)
        for t in (.21,.25,.3,.35):
            r.tick(InputSample(p,.1,.2),(),t)
        self.assertEqual(r.controller.phase,Phase.IDLE)


class DeviceSnapshotTests(unittest.TestCase):
    def test_age_accounts_for_time_since_status_receipt(self):
        status=DeviceSnapshot(True,True,True,'IDLE',Point(2,2),Point(2,2),'NONE',True,True,False,1,400)
        self.assertTrue(status.can_move(1.05))
        self.assertFalse(status.can_move(1.2))

    def test_old_service_without_id_sync_cannot_enable_actuation(self):
        from tabletop_os.model import snapshot_from_message
        message={'control_lease_valid':True,'motion_commands_enabled':True,'motion_command_in_flight':False,
                 'arduino':{'connected':True,'ready':True,'firmware_compatible':True,'config_confirmed':True,
                            'watchdog_enabled':True,'referenced':True,'mode':'IDLE','active_id':0,
                            'actual_x_mm':20,'actual_y_mm':20,'target_x_mm':20,'target_y_mm':20,
                            'fault':'NONE','telemetry_age_ms':0}}
        self.assertFalse(snapshot_from_message(message,1).can_operate_servo(1))
        message['command_id_synchronized']=True
        self.assertTrue(snapshot_from_message(message,1).can_operate_servo(1))


class ConfigTests(unittest.TestCase):
    def load(self, value):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'runtime.json'
            path.write_text(json.dumps(value), encoding='utf-8')
            return load_config(path)

    def test_unknown_keys_and_invalid_numeric_types(self):
        for value in [{'unknown':{}}, {'board':{'widht_mm':20}}, {'network':{'control_port':True}},
                      {'interaction':{'pinch_on':.5,'pinch_off':.4}}, {'gripper':{'calibrated':True}},
                      {'board':{'width_mm':float('nan')}}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load(value)

    def test_fractional_measurements_and_valid_angles(self):
        config = self.load({'board':{'width_mm':342.5},'gripper':{'calibrated':True,'release_angle':70,'engage_angle':100}})
        self.assertEqual(config.board.width_mm,342.5)
        self.assertTrue(config.gripper.calibrated)


if __name__ == '__main__':
    unittest.main()
