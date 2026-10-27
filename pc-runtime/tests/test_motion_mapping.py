"""Independent LED and mechanical calibrations share camera pixels, not units."""
from dataclasses import replace
import importlib.util
from pathlib import Path
import unittest

from tabletop_os.calibration import Calibration, from_corners
from tabletop_os.config import BoardConfig, RuntimeConfig
from tabletop_os.constant_engage import ConstantEngageDrag
from tabletop_os.model import Observation, Point
from test_constant_engage import FakeDevice


HAS_VISION = all(importlib.util.find_spec(name) for name in ('cv2','numpy','mediapipe'))


@unittest.skipUnless(HAS_VISION,'Install vision dependencies')
class MotionMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1]/'tools/hand_pointer.py'
        spec = importlib.util.spec_from_file_location('motion_mapping_pointer',path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def setUp(self):
        # Camera right-bottom is mechanical home; X points up and Y left.
        size = (1000,800)
        self.mechanical = from_corners([(900,700),(900,100),(100,100),(100,700)],size,BoardConfig())
        # The panel covers only the central part of the mechanical workspace.
        self.display = from_corners([(300,250),(700,250),(700,550),(300,550)],size,
                                    BoardConfig(width_mm=63,height_mm=95))

    def assert_point(self,actual,expected):
        self.assertAlmostEqual(actual.x,expected.x,places=5)
        self.assertAlmostEqual(actual.y,expected.y,places=5)

    def test_panel_corners_map_to_mechanical_subset_with_correct_axes(self):
        cases = ((Point(0,0),Point(223.5,349.5)),
                 (Point(63,0),Point(223.5,116.5)),
                 (Point(63,95),Point(74.5,116.5)),
                 (Point(0,95),Point(74.5,349.5)),
                 (Point(31.5,47.5),Point(149,233)))
        for led,mechanical in cases:
            with self.subTest(led=led):
                self.assert_point(self.module.project_between(led,self.display,self.mechanical),mechanical)

    def test_perspective_mapping_roundtrip_returns_original_led_point(self):
        perspective = from_corners([(280,230),(720,260),(690,570),(320,540)],(1000,800),
                                   BoardConfig(width_mm=63,height_mm=95))
        for point in (Point(0,0),Point(12.5,39.5),Point(63,95)):
            mm = self.module.project_between(point,perspective,self.mechanical)
            self.assert_point(self.module.project_between(mm,self.mechanical,perspective),point)

    def test_output_mirror_and_rotation_do_not_change_mechanical_target(self):
        point = Point(12,25)
        expected = self.module.project_between(point,self.display,self.mechanical)
        frames = set()
        for rotation in (0,180):
            for flip_x in (False,True):
                frames.add(self.module.pointer_frame(point,rotation,flip_x))
                actual = self.module.project_between(point,self.display,self.mechanical)
                self.assert_point(actual,expected)
        self.assertEqual(len(frames),4)
        self.assert_point(point,Point(12,25))

    def test_drag_request_uses_reprojected_millimetres_instead_of_led_pixels(self):
        led_start,led_target = Point(31.5,47.5),Point(32,48)
        mm_start = self.module.project_between(led_start,self.display,self.mechanical)
        mm_target = self.module.project_between(led_target,self.display,self.mechanical)
        device = FakeDevice()
        device.state = replace(device.state,position=mm_start)
        drag = ConstantEngageDrag(RuntimeConfig(),device)
        drag.begin(Observation(7,mm_start,.09),mm_target,.1)
        self.assertEqual(len(device.requests),1)
        kind,fields,_ = device.requests[0]
        self.assertEqual(kind,'motion.move_to')
        self.assert_point(Point(fields['x_mm'],fields['y_mm']),mm_target)
        self.assertGreater(abs(fields['x_mm']-led_target.x),100)
        self.assertGreater(abs(fields['y_mm']-led_target.y),100)

    def test_centered_reachable_area_maps_to_the_correct_physical_led_pixels(self):
        # White light area is 320x480 mm.  The 298x466 mm carriage-centre
        # workspace is inset 11 mm horizontally and 7 mm vertically.
        display = Calibration(320,480,63,95,
                              ((63/320,0,0),(0,95/480,0),(0,0,1)))
        mechanical = Calibration(320,480,298,466,
                                 ((1,0,-11),(0,1,-7),(0,0,1)))
        cases = ((Point(0,0),Point(11/320*63,7/480*95)),
                 (Point(149,233),Point(31.5,47.5)),
                 (Point(298,466),Point(309/320*63,473/480*95)))
        for millimetres,pixels in cases:
            with self.subTest(millimetres=millimetres):
                self.assert_point(self.module.project_between(
                    millimetres,mechanical,display),pixels)


if __name__ == '__main__':
    unittest.main()
