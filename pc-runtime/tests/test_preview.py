"""Focused checks for combined vision preview; no camera or device access."""
import importlib.util
from pathlib import Path
import unittest

from tabletop_os.config import InteractionConfig
from tabletop_os.frame_protocol import HEIGHT, WIDTH
from tabletop_os.model import InputSample, Observation, Point
from tabletop_os.preview import SelectionPreview


class SelectionPreviewTests(unittest.TestCase):
    def setUp(self):
        self.preview = SelectionPreview(InteractionConfig(),hold_s=3.0)
        self.piece = Point(20, 25)

    def update(self, now, ratio=.9, position=None, tags=None):
        if position is None:
            position = self.piece
        if tags is None:
            tags = (Observation(7, self.piece, now),)
        self.preview.update(InputSample(position, ratio, now), tags, now)

    def select(self):
        self.update(0)
        self.update(.07)
        self.update(.10, .1)
        self.assertEqual(self.preview.phase, 'IDLE')
        self.update(.17, .1)
        self.assertEqual(self.preview.phase, 'SELECTED')

    def test_two_pinches_require_debounced_open_and_release(self):
        # Arriving with pinched fingers cannot select until opened first.
        self.update(-.2, .1)
        self.update(-.1, .1)
        self.assertEqual(self.preview.phase, 'IDLE')
        self.select()
        self.assertEqual(self.preview.selected_id, 7)
        self.update(.24, .1)
        self.assertEqual(self.preview.phase, 'SELECTED')
        self.update(.30, .9)
        self.assertEqual(self.preview.phase, 'SELECTED')
        self.update(.37, .9)
        self.assertEqual(self.preview.phase, 'AIMING')
        target = Point(45, 48)
        self.update(.40, .9, target)
        self.assertEqual(self.preview.target, target)
        self.update(.50, .1, target)
        self.assertEqual(self.preview.phase, 'AIMING')
        self.update(.57, .1, target)
        self.assertEqual(self.preview.phase, 'CONFIRMED_PREVIEW')
        self.update(.70, .1, Point(50, 50))
        self.assertEqual(self.preview.target, target)

    def test_nearest_unique_id_wins_within_radius(self):
        near = Point(21, 25)
        far = Point(24, 25)
        for now, ratio in ((0,.9),(.07,.9),(.10,.1),(.17,.1)):
            self.update(now, ratio, tags=(Observation(4,far,now),Observation(7,near,now)))
        self.assertEqual(self.preview.selected_id, 7)
        self.preview.clear()
        self.update(.3,.9,Point(60,60))
        self.update(.37,.9,Point(60,60))
        self.update(.4,.1,Point(60,60))
        self.update(.47,.1,Point(60,60))
        self.assertEqual(self.preview.phase, 'IDLE')

    def test_selected_tag_timeout_cancels(self):
        self.select()
        self.update(.4, .1, tags=())
        self.assertEqual(self.preview.phase, 'SELECTED')
        self.update(3.16, .1, tags=())
        self.assertEqual(self.preview.phase, 'SELECTED')
        self.assertEqual(self.preview.tags[7].position, self.piece)
        self.assertEqual(self.preview.cached_ids, frozenset({7}))
        self.assertEqual(self.preview.display_tags, (self.preview.tags[7],))
        self.update(3.18, .1, tags=())
        self.assertEqual(self.preview.phase, 'IDLE')
        self.assertIsNone(self.preview.selected_id)
        self.assertIsNone(self.preview.target)
        self.assertEqual(self.preview.display_tags, ())
        self.assertEqual(self.preview.cached_ids, frozenset())

    def test_occluded_tag_stays_at_last_observation_not_hand_position(self):
        self.select()
        self.update(.3, .9, Point(40,45), tags=())
        self.update(.37, .9, Point(45,50), tags=())
        self.assertEqual(self.preview.phase, 'AIMING')
        self.assertEqual(self.preview.target, Point(45,50))
        self.assertEqual(self.preview.visible_tags, ())
        self.assertEqual(self.preview.display_tags[0].position, self.piece)
        self.assertEqual(self.preview.display_tags[0].timestamp, .17)

    def test_reacquisition_updates_position_and_restarts_hold_timer(self):
        self.select()
        self.update(2.0, .1, tags=())
        moved = Point(35,40)
        observation = Observation(7,moved,2.1)
        self.update(2.1, .1, tags=(observation,))
        self.assertEqual(self.preview.tags[7], observation)
        self.assertEqual(self.preview.visible_tags, (observation,))
        self.assertEqual(self.preview.display_tags, (observation,))
        self.assertEqual(self.preview.cached_ids, frozenset())
        self.update(4.0, .1, tags=())
        self.assertEqual(self.preview.phase, 'SELECTED')
        self.assertEqual(self.preview.tags[7].position, moved)
        self.assertEqual(self.preview.cached_ids, frozenset({7}))
        self.update(5.11, .1, tags=())
        self.assertEqual(self.preview.phase, 'IDLE')
        self.assertEqual(self.preview.display_tags, ())

    def test_live_and_held_tags_render_together_without_duplicate_observations(self):
        first = Observation(7,self.piece,0)
        self.update(0,tags=(first,))
        second = Observation(8,Point(45,45),1)
        self.update(1,tags=(second,))
        displayed = {tag.piece_id:tag for tag in self.preview.display_tags}
        self.assertEqual(displayed, {7:first,8:second})
        self.assertEqual(self.preview.cached_ids, frozenset({7}))
        self.update(3.01,tags=(Observation(8,Point(45,45),3.01),))
        self.assertEqual([tag.piece_id for tag in self.preview.display_tags], [8])

    def test_stale_observation_cannot_move_or_extend_held_position(self):
        self.select()
        stale = Observation(7,Point(50,50),.2)
        self.update(2.0,.1,tags=(stale,))
        self.assertEqual(self.preview.visible_tags, ())
        self.assertEqual(self.preview.tags[7].position,self.piece)
        self.assertEqual(self.preview.tags[7].timestamp,.17)
        self.update(3.18,.1,tags=(stale,))
        self.assertEqual(self.preview.display_tags, ())
        self.assertEqual(self.preview.phase,'IDLE')

    def test_default_retains_last_seen_position_and_selection_until_reacquired(self):
        self.preview = SelectionPreview(InteractionConfig())
        self.select()
        last_seen = self.preview.tags[7]
        for now in (5.0,60.0,3601.0):
            self.update(now,.1,Point(40,40),tags=())
            self.assertEqual(self.preview.phase,'SELECTED')
            self.assertEqual(self.preview.tags[7],last_seen)
            self.assertEqual(self.preview.tags[7].timestamp,.17)
            self.assertEqual(self.preview.display_tags,(last_seen,))
            self.assertEqual(self.preview.cached_ids,frozenset({7}))
        observation = Observation(7,Point(35,40),3601.1)
        self.update(3601.1,.1,tags=(observation,))
        self.assertEqual(self.preview.tags[7],observation)
        self.assertEqual(self.preview.display_tags,(observation,))
        self.assertEqual(self.preview.cached_ids,frozenset())

    def test_default_hand_loss_cancels_selection_but_retains_tag_cache(self):
        self.preview = SelectionPreview(InteractionConfig())
        self.select()
        last_seen = self.preview.tags[7]
        self.preview.update(InputSample(None,None,3601.0),(),3601.0)
        self.assertEqual(self.preview.phase,'IDLE')
        self.assertIsNone(self.preview.selected_id)
        self.assertEqual(self.preview.tags[7],last_seen)
        self.assertEqual(self.preview.display_tags,(last_seen,))
        self.assertEqual(self.preview.cached_ids,frozenset({7}))

    def test_hand_loss_and_outside_cancel_and_require_rearm(self):
        for position in (None, Point(-1,25), Point(WIDTH,25), Point(25,HEIGHT)):
            with self.subTest(position=position):
                self.preview = SelectionPreview(InteractionConfig())
                self.select()
                self.preview.update(InputSample(position,None,.2),(),.2)
                self.assertEqual(self.preview.phase,'IDLE')
                self.update(.3,.1)
                self.update(.4,.1)
                self.assertEqual(self.preview.phase,'IDLE')

    def test_duplicate_id_is_visible_but_remains_blocked_after_occlusion(self):
        duplicate = (Observation(0,Point(20,25),0),Observation(0,Point(45,45),0))
        self.update(0,tags=duplicate)
        self.assertEqual(len(self.preview.visible_tags),2)
        self.assertIn(0,self.preview.ambiguous_ids)
        # Only one copy is visible later, but it must not become selectable.
        for now,ratio in ((.07,.9),(.1,.1),(.17,.1),(.8,.9),(.87,.9),(.9,.1),(.97,.1)):
            self.update(now,ratio,tags=(Observation(0,self.piece,now),))
            self.assertEqual(self.preview.phase,'IDLE')
        self.assertNotIn(0,self.preview.tags)


@unittest.skipUnless(importlib.util.find_spec('cv2') and importlib.util.find_spec('mediapipe'), 'Vision dependencies unavailable')
class CombinedRenderTests(unittest.TestCase):
    def test_cached_square_keeps_same_location_and_brightness(self):
        import numpy as np
        path = Path(__file__).resolve().parents[1]/'tools/hand_pointer.py'
        spec = importlib.util.spec_from_file_location('held_pointer_test',path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tags = (Observation(7,Point(10,12),1),Observation(8,Point(45,40),1))
        def image(cached_ids):
            return np.frombuffer(module.pointer_frame(None,0,False,tags=tags,cached_ids=cached_ids),dtype=np.uint8).reshape(HEIGHT,WIDTH,3)
        live, held = image(()), image({7})
        self.assertGreater(held[10,8,1],0)
        self.assertTrue(np.array_equal(held,live))
        self.assertTrue(np.array_equal(held.any(axis=2),live.any(axis=2)))
        self.assertTrue(np.array_equal(held[38:43,43:48],live[38:43,43:48]))

    def test_tags_hand_and_target_share_final_mirror_and_rotation(self):
        import numpy as np
        path = Path(__file__).resolve().parents[1]/'tools/hand_pointer.py'
        spec = importlib.util.spec_from_file_location('combined_pointer_test',path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tags = (Observation(7,Point(10,12),1),Observation(8,Point(45,40),1))
        fields = dict(tags=tags,selected_id=7,target=Point(30,35),confirmed=True)
        def image(rotation,flip):
            return np.frombuffer(module.pointer_frame(Point(20,22),rotation,flip,**fields),dtype=np.uint8).reshape(HEIGHT,WIDTH,3)
        original = image(0,False)
        self.assertTrue(np.array_equal(image(0,True),original[:,::-1]))
        self.assertTrue(np.array_equal(image(180,False),original[::-1,::-1]))
        self.assertTrue(np.array_equal(image(180,True),original[::-1,:]))
        self.assertFalse(original[:,:,0].any())
        self.assertTrue(original[22,20].any())
        self.assertTrue(original[12,14].any())
        self.assertTrue(original[40,47].any())


if __name__ == '__main__':
    unittest.main()
