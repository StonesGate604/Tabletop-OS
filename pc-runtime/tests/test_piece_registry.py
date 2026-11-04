from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from tabletop_os.config import RuntimeConfig
from tabletop_os.model import Observation, Point
from tabletop_os.system.pieces import PieceBindings, PieceDefinition, PieceRegistry
from patrol_support import raw_config, config


class PieceBindingsTests(unittest.TestCase):
    def test_duplicate_json_keys_are_rejected_before_mapping_is_created(self):
        from tabletop_os.apps.patrol.config import load_patrol
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'patrol.json'
            path.write_text('{"tag_family":"tag36h11","tag_family":"tag25h9"}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Duplicate JSON key'):
                load_patrol(path, RuntimeConfig())

    def test_real_detector_resolves_replaced_tags_and_ignores_old_id(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest('vision extras not installed')
        from tabletop_os.calibration import Calibration
        from tabletop_os.vision import AprilTagDetector
        raw = raw_config()
        raw['pieces']['player']['tag_id'] = 12
        raw['pieces']['guard']['tag_id'] = 33
        cfg = config(raw)
        image = np.full((300, 900), 255, dtype=np.uint8)
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
        for index, tag_id in enumerate((5, 12, 33)):
            image[50:250, index*300+50:index*300+250] = cv2.aruco.generateImageMarker(dictionary, tag_id, 200)
        calibration = Calibration(900, 300, 900, 300, ((1, 0, 0), (0, 1, 0), (0, 0, 1)))
        observations = AprilTagDetector('DICT_APRILTAG_36h11', 100).detect(image, calibration, 1)
        registry = PieceRegistry(cfg.bindings, 'DICT_APRILTAG_36h11')
        registry.update(observations, 1)
        self.assertEqual(set(registry.visible), {'player', 'guard'})
        self.assertAlmostEqual(registry.visible['player'].position.x, 449.5, delta=1)
        self.assertAlmostEqual(registry.visible['guard'].position.x, 749.5, delta=1)
        self.assertEqual(registry.unknown, {5})

    def test_family_id_limits_are_real_opencv_dictionary_sizes(self):
        try:
            import cv2
        except ImportError:
            self.skipTest('vision extras not installed')
        from tabletop_os.system.pieces import TAG_FAMILIES
        for family, (dictionary, count) in TAG_FAMILIES.items():
            self.assertEqual(cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary)).bytesList.shape[0], count)
            bindings = PieceBindings(family, (PieceDefinition('piece', count-1, 'prop'),))
            bindings.validate(dictionary)
            with self.assertRaisesRegex(ValueError, 'Invalid tag_id'):
                replace(bindings, pieces=(PieceDefinition('piece', count, 'prop'),)).validate(dictionary)

    def test_rejects_duplicate_ids_even_if_disabled(self):
        raw = raw_config()
        raw['pieces']['guard']['tag_id'] = raw['pieces']['player']['tag_id']
        for enabled in (True, False):
            raw['pieces']['guard']['enabled'] = enabled
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                config(raw)

    def test_invalid_ids_and_family_are_clear_errors(self):
        for invalid in (-1, 587, True, 1.2, '12', None):
            raw = raw_config()
            raw['pieces']['player']['tag_id'] = invalid
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'Invalid tag_id'):
                config(raw)
        for family in ('tag25h9', 'nonsense', {}, None):
            raw = raw_config()
            raw['tag_family'] = family
            with self.subTest(family=family), self.assertRaisesRegex(ValueError, 'family'):
                config(raw)

    def test_unknown_disabled_and_old_tags_cannot_resolve_to_player(self):
        raw = raw_config()
        raw['pieces']['player']['tag_id'] = 12
        raw['pieces']['prop'] = {'tag_id': 21, 'role': 'prop', 'enabled': False}
        cfg = config(raw)
        registry = PieceRegistry(cfg.bindings, RuntimeConfig().camera.april_tag_dictionary)
        registry.update(tuple(Observation(tag, Point(tag, 5), 1) for tag in (5, 12, 21, 99)), 1)
        self.assertEqual(set(registry.visible), {'player'})
        self.assertEqual(registry.visible['player'].position, Point(12, 5))
        self.assertEqual(registry.unknown, {5, 21, 99})

    def test_occlusion_never_reassigns_nearby_unknown_or_guard_to_player(self):
        cfg = config()
        registry = PieceRegistry(cfg.bindings, 'DICT_APRILTAG_36h11')
        registry.update((Observation(5, Point(20, 20), 1),), 1)
        registry.update((Observation(8, Point(20, 20), 1.1), Observation(99, Point(20, 20), 1.1)), 1.1)
        self.assertNotIn('player', registry.visible)
        self.assertEqual(registry.last['player'].timestamp, 1)
        self.assertEqual(registry.visible['guard'].piece_id, 'guard')

    def test_duplicate_visible_tag_is_latched_until_restart(self):
        registry = PieceRegistry(config().bindings, 'DICT_APRILTAG_36h11')
        registry.update((Observation(5, Point(1, 2), 1), Observation(5, Point(3, 4), 1)), 1)
        self.assertEqual(registry.ambiguous, {'player'})
        registry.update((Observation(5, Point(1, 2), 2),), 2)
        self.assertEqual(registry.visible, {})

    def test_stale_and_out_of_order_samples_do_not_replace_identity_state(self):
        registry = PieceRegistry(config().bindings, 'DICT_APRILTAG_36h11')
        registry.update((Observation(5, Point(1, 2), 1),), 1)
        registry.update((Observation(5, Point(3, 4), .95),), 1.1)
        self.assertNotIn('player', registry.visible)
        self.assertEqual(registry.last['player'].position, Point(1, 2))
        registry.update((Observation(8, Point(1, 2), 1),), 2)
        self.assertEqual(registry.visible, {})

    def test_registration_supports_arbitrary_logical_roles_and_more_than_two_pieces(self):
        raw = raw_config()
        raw['pieces']['loot-2'] = {'tag_id': 40, 'role': 'item', 'enabled': True}
        raw['map']['initial_positions']['loot-2'] = [4, 6]
        cfg = config(raw)
        registry = PieceRegistry(cfg.bindings, 'DICT_APRILTAG_36h11')
        registry.update((Observation(40, Point(1, 2), 1),), 1)
        self.assertEqual(registry.visible['loot-2'].piece_id, 'loot-2')


if __name__ == '__main__':
    unittest.main()
