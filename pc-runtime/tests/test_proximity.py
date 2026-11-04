from dataclasses import replace
import unittest

from tabletop_os.model import InputSample, Point
from tabletop_os.system.pieces import PieceObservation
from tabletop_os.system.proximity import ProximityConfig, ProximityService, StablePosition, InteractionState


class ProximityTests(unittest.TestCase):
    def setUp(self):
        self.cfg = ProximityConfig()
        self.service = ProximityService(self.cfg)
        self.origin = Point(100, 100)
        self.now = 0
        self.last = {}
        self.events = []

    def step(self, hand, position='origin', actionable=frozenset({'hero'}), seconds=.05, extra=None):
        self.now += seconds
        position = self.origin if position == 'origin' else position
        visible = {'hero': PieceObservation('hero', position, self.now)} if position else {}
        if extra:
            visible.update(extra)
        self.last.update(visible)
        events = self.service.update(InputSample(hand, None, self.now), visible, self.last,
                                     actionable, {'hero': self.origin, 'other': Point(140, 100)}, self.now)
        self.events.extend(events)
        return events

    def run_for(self, seconds, hand, position='origin'):
        for _ in range(round(seconds/.05)):
            self.step(hand, position)

    def kinds(self):
        return [e.kind for e in self.events]

    def test_hover_enters_once_without_click_or_pinch(self):
        self.run_for(1, Point(101, 100))
        self.assertEqual(self.kinds().count('piece_proximity_entered'), 1)
        self.assertEqual(self.service.state, InteractionState.PROXIMITY)
        self.assertNotIn('piece_position_stabilized', self.kinds())

    def test_tag_hidden_before_dwell_completes_can_enter_at_last_position(self):
        self.step(self.origin)
        self.run_for(.5, self.origin, None)
        self.assertEqual(self.kinds().count('piece_proximity_entered'), 1)
        self.assertEqual(self.service.target, 'hero')
        self.assertEqual(self.last['hero'].position, self.origin)
        self.assertNotIn('piece_position_stabilized', self.kinds())

    def test_never_seen_or_expired_tag_cannot_be_selected_from_a_logical_start(self):
        self.run_for(.3, self.origin, None)
        self.assertNotIn('piece_proximity_entered', self.kinds())
        self.last['hero'] = PieceObservation('hero', self.origin, 0)
        self.now = self.cfg.tag_loss_grace_s+1
        self.run_for(.3, self.origin, None)
        self.assertNotIn('piece_proximity_entered', self.kinds())

    def test_occluded_candidate_does_not_follow_the_hand_or_commit(self):
        self.step(Point(160, 100))
        self.run_for(.3, self.origin, None)
        self.assertEqual(self.service.target, 'hero')
        self.run_for(.3, Point(170, 100), None)
        self.assertEqual(self.last['hero'].position, self.origin)
        self.assertNotIn('piece_position_stabilized', self.kinds())

    def test_leave_cancels_and_hysteresis_prevents_flicker(self):
        self.run_for(.3, self.origin)
        self.run_for(.3, Point(140, 100))
        self.assertEqual(self.service.target, 'hero')  # between enter and exit
        self.run_for(.4, Point(200, 100))
        self.assertEqual(self.service.state, InteractionState.CANCELLED)
        self.assertEqual(self.kinds().count('piece_proximity_exited'), 1)

    def test_short_hand_loss_holds_preview(self):
        self.run_for(.3, self.origin)
        self.run_for(.3, None)
        self.assertEqual(self.service.target, 'hero')
        self.assertNotIn('piece_proximity_exited', self.kinds())

    def test_grasp_occlusion_and_return_to_origin_do_not_make_a_move(self):
        self.run_for(.3, self.origin)
        self.run_for(.4, self.origin, None)
        self.assertEqual(self.service.state, InteractionState.MANIPULATING)
        self.run_for(.5, self.origin)
        self.assertNotIn('piece_position_stabilized', self.kinds())
        self.run_for(1, None)
        placements = [e.position for e in self.events if e.kind == 'piece_position_stabilized']
        self.assertEqual(placements, [self.origin])
        self.assertEqual(self.service.state, InteractionState.CANCELLED)

    def test_long_tag_loss_reports_once_and_never_places(self):
        self.run_for(.3, self.origin)
        self.run_for(3, None, None)
        self.assertEqual(self.kinds().count('piece_tracking_lost'), 1)
        self.assertNotIn('piece_position_stabilized', self.kinds())

    def test_new_position_requires_hand_clear_stability_then_emits_once(self):
        target = Point(150, 100)
        self.run_for(.3, self.origin)
        self.run_for(.2, self.origin, None)
        self.run_for(1, target, target)
        self.assertNotIn('piece_position_stabilized', self.kinds())
        self.run_for(1.5, None, target)
        placements = [e for e in self.events if e.kind == 'piece_position_stabilized']
        self.assertEqual(len(placements), 1)
        self.assertEqual(placements[0].piece_id, 'hero')
        self.assertEqual(placements[0].position, target)

    def test_target_does_not_switch_during_manipulation(self):
        self.run_for(.3, self.origin)
        self.step(self.origin, None)
        for _ in range(10):
            other = {'other': PieceObservation('other', Point(140, 100), self.now+.05)}
            self.step(Point(140, 100), None, frozenset({'hero', 'other'}), extra=other)
        self.assertEqual(self.service.target, 'hero')
        self.assertFalse(any(e.piece_id == 'other' for e in self.events))

    def test_only_actionable_pieces_can_receive_proximity(self):
        for _ in range(12):
            self.step(Point(140, 100), extra={'other': PieceObservation('other', Point(140, 100), self.now+.05)})
        self.assertNotIn('piece_proximity_entered', self.kinds())

    def test_fresh_frames_not_ticks_are_required_for_stability(self):
        stable = StablePosition(self.cfg)
        observation = PieceObservation('hero', self.origin, 1)
        for now in (1, 1.05, 1.1, 1.2, 1.4):
            self.assertFalse(stable.update(observation, now))
        self.assertFalse(stable.stable)

    def test_position_jitter_and_missing_frame_restart_stability(self):
        stable = StablePosition(self.cfg)
        for i in range(15):
            p = Point(100+(10 if i % 2 else 0), 100)
            self.assertFalse(stable.update(PieceObservation('hero', p, i*.05), i*.05))
        stable.update(None, 1)
        self.assertFalse(stable.update(PieceObservation('hero', self.origin, 1.1), 1.1))

    def test_stale_hand_data_cannot_confirm_release(self):
        target = Point(160, 100)
        for i in range(15):
            now = 1+i*.05
            tag = PieceObservation('hero', target, now)
            events = self.service.update(InputSample(None, None, 0), {'hero': tag}, {'hero': tag},
                                         {'hero'}, {'hero': self.origin}, now)
            self.assertFalse(any(e.kind == 'piece_position_stabilized' for e in events))

    def test_bad_thresholds_are_rejected(self):
        for values in ({'proximity_enter_mm': 50}, {'stability_s': float('nan')},
                       {'stability_samples': True}, {'release_s': 0}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(self.cfg, **values)


if __name__ == '__main__':
    unittest.main()
