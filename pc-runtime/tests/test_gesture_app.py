"""Application-service boundary checks without camera or device access."""
from types import SimpleNamespace
import unittest

from tabletop_os.apps.gesture_drag import GestureDragApp
from tabletop_os.config import InteractionConfig
from tabletop_os.model import InputSample, Observation, Point
from tabletop_os.system.tracking import TagMemory


class FakeSystem:
    """Only the public application-facing services; no device adapter exists."""

    def __init__(self, interaction, enabled=True):
        self.tracking = TagMemory(interaction)
        self.motion_view = SimpleNamespace(enabled=enabled, phase='IDLE' if enabled else 'DISABLED',
                                           selected_id=None, target=None)
        self.requests = []
        self.cancels = 0
        self.stops = 0
        self.failure = None

    def request_move(self, piece_id, target, now):
        self.requests.append((piece_id, target, now))
        if self.failure:
            raise RuntimeError(self.failure)
        self.motion_view = SimpleNamespace(enabled=True, phase='MOVING',
                                           selected_id=piece_id, target=target)

    def cancel(self):
        self.cancels += 1

    def stop(self):
        self.stops += 1


class GestureApplicationTests(unittest.TestCase):
    def setUp(self):
        self.interaction = InteractionConfig()
        self.app = GestureDragApp(self.interaction)
        self.system = FakeSystem(self.interaction)
        self.piece = Point(20, 25)
        self.target = Point(40, 45)

    def step(self, now, ratio=.9, position=None, timestamp=None, observations=None):
        if position is None:
            position = self.piece
        if observations is None:
            observations = (Observation(7, self.piece, now),)
        self.system.tracking.update(observations, now)
        sample = InputSample(position, ratio, now if timestamp is None else timestamp)
        self.app.update(sample, observations, self.system, now)

    def confirm(self, offset=0):
        for now, ratio, position in (
                (0, .9, self.piece), (.07, .9, self.piece),
                (.10, .1, self.piece), (.17, .1, self.piece),
                (.30, .9, self.piece), (.37, .9, self.piece),
                (.40, .9, self.target), (.50, .1, self.target), (.57, .1, self.target)):
            self.step(now+offset, ratio, position)

    def test_confirmation_uses_only_service_and_is_not_repeated_while_held(self):
        self.confirm()
        self.assertEqual(self.system.requests, [(7, self.target, .57)])
        for now in (.7, .8, .9):
            self.step(now, .1, self.target)
        self.assertEqual(len(self.system.requests), 1)
        self.assertEqual(self.app.error, '')
        self.assertIs(self.app.selection.memory, self.system.tracking)

    def test_motor_owned_scene_keeps_target_after_hand_loss(self):
        self.confirm()
        self.system.tracking.update((), .8)
        self.app.update(InputSample(None, None, .8), (), self.system, .8)
        self.assertEqual(self.app.selection.phase, 'IDLE')
        for phase in ('MOVING', 'VERIFYING', 'DONE'):
            with self.subTest(phase=phase):
                self.system.motion_view.phase = phase
                scene = self.app.scene(self.system, None)
                self.assertEqual(scene.selected_id, 7)
                self.assertEqual(scene.target, self.target)
                self.assertTrue(scene.confirmed)
                self.assertIsNone(scene.cursor)
                self.assertEqual(scene.cached_ids, frozenset({7}))

    def test_blocked_request_needs_a_new_confirmation_edge(self):
        self.system.failure = 'UNCOVER_SELECTED_TAG_AND_CONFIRM_AGAIN'
        self.confirm()
        self.assertEqual(self.app.selection.phase, 'AIMING')
        self.assertEqual(self.app.error, self.system.failure)
        self.system.failure = None
        for now in (.7, .8, .9):
            self.step(now, .1, self.target)
        self.assertEqual(len(self.system.requests), 1)
        for now, ratio in ((1.0, .9), (1.07, .9), (1.1, .1), (1.17, .1)):
            self.step(now, ratio, self.target)
        self.assertEqual(len(self.system.requests), 2)
        self.assertEqual(self.system.motion_view.phase, 'MOVING')
        self.assertEqual(self.app.error, '')

    def test_stale_and_future_samples_are_lost_and_must_rearm(self):
        for timestamp in (0, 2):
            with self.subTest(timestamp=timestamp):
                self.setUp()
                self.step(0, .9)
                self.step(.07, .9)
                self.step(.1, .1)
                self.step(.17, .1)
                self.assertEqual(self.app.selection.phase, 'SELECTED')
                self.step(1, .9, timestamp=timestamp)
                self.assertEqual(self.app.selection.phase, 'IDLE')
                self.assertFalse(self.app.selection.pinch.armed)
                self.step(1.1, .1)
                self.step(1.2, .1)
                self.assertEqual(self.app.selection.phase, 'IDLE')
                self.assertEqual(self.system.requests, [])

    def test_preview_confirmation_never_requests_motion_or_replays_when_enabled(self):
        self.system = FakeSystem(self.interaction, enabled=False)
        self.confirm()
        self.assertEqual(self.app.selection.phase, 'CONFIRMED_PREVIEW')
        self.assertTrue(self.app.scene(self.system, self.target).confirmed)
        self.assertEqual(self.system.requests, [])
        self.system.motion_view.enabled = True
        self.system.motion_view.phase = 'IDLE'
        self.step(.8, .1, self.target)
        self.assertEqual(self.system.requests, [])

    def test_cancel_and_stop_delegate_without_clearing_observation_memory(self):
        self.confirm()
        remembered = self.system.tracking.tags[7]
        self.app.cancel(self.system)
        self.assertEqual(self.system.cancels, 1)
        self.assertEqual(self.app.selection.phase, 'IDLE')
        self.assertFalse(self.app.selection.pinch.armed)
        self.assertEqual(self.system.tracking.tags[7], remembered)
        self.app.stop(self.system)
        self.assertEqual(self.system.stops, 1)
        self.assertEqual(self.app.error, '')
        self.assertEqual(self.system.tracking.tags[7], remembered)
        self.app.reset_mapping()
        self.assertEqual(self.system.tracking.tags, {})

    def test_application_does_not_update_system_observation_memory_twice(self):
        observed = Observation(7, self.piece, 0)
        self.system.tracking.update((observed,), 0)
        self.app.update(InputSample(self.piece, .9, 0),
                        (Observation(99, self.target, 0),), self.system, 0)
        self.assertEqual(self.system.tracking.tags, {7: observed})
        self.assertEqual(self.system.tracking.visible_tags, (observed,))

    def test_attaching_another_system_discards_old_confirmation(self):
        self.confirm()
        new_system = FakeSystem(self.interaction)
        self.app.attach(new_system)
        self.assertEqual(self.app.selection.phase, 'IDLE')
        self.assertIsNone(self.app.selection.target)
        self.assertFalse(self.app.selection.pinch.armed)
        self.assertIs(self.app.selection.memory, new_system.tracking)
        self.assertEqual(new_system.requests, [])

    def test_busy_service_requires_explicit_reset_without_delayed_submission(self):
        self.system.motion_view.phase = 'DONE'
        self.confirm()
        self.assertEqual(self.app.error, 'CANCEL_OR_RESET_WITH_C_FIRST')
        self.assertEqual(self.system.requests, [])
        self.system.motion_view.phase = 'IDLE'
        self.step(.8, .1, self.target)
        self.assertEqual(self.system.requests, [])


if __name__ == '__main__':
    unittest.main()
