from dataclasses import replace
import random
import unittest
from tabletop_os.frame_protocol import FrameAssembler, encode_frame, FRAME_BYTES
from tabletop_os.render import Compositor
from tabletop_os.config import RuntimeConfig
from tabletop_os.interaction import DemoController
from tabletop_os.model import Point, DeviceSnapshot


class FrameProtocolTests(unittest.TestCase):
    def setUp(self):
        self.rgb = bytes(i % 251 for i in range(FRAME_BYTES))
        self.a = FrameAssembler()
        self.a.set_session(123)

    def test_out_of_order_duplicate_and_exact_payload(self):
        packets = encode_frame(self.rgb,123,10)
        random.Random(7).shuffle(packets)
        self.assertIsNone(self.a.feed(packets[0],0))
        self.assertIsNone(self.a.feed(packets[0],0))
        output = None
        for packet in packets[1:]:
            output = self.a.feed(packet,.01) or output
        self.assertEqual(output,self.rgb)
        self.assertIsNone(self.a.feed(packets[-1],.02))

    def test_partial_timeout_and_old_frames_never_present(self):
        packets = encode_frame(self.rgb,123,10)
        for packet in packets[:-1]:
            self.assertIsNone(self.a.feed(packet,0))
        self.assertIsNone(self.a.feed(packets[-1],.11))
        for packet in packets:
            self.assertIsNone(self.a.feed(packet,.12))
        next_packets = encode_frame(self.rgb,123,11)
        outputs = [self.a.feed(p,.13) for p in next_packets]
        self.assertEqual(outputs[-1],self.rgb)
        self.assertIsNone(self.a.feed(packets[0],.14))

    def test_bad_crc_session_and_packet_length(self):
        for packet in encode_frame(self.rgb,999,10):
            self.assertIsNone(self.a.feed(packet,0))
        packets = encode_frame(self.rgb,123,10)
        self.assertIsNone(self.a.feed(packets[0][:-1],0))
        damaged = bytearray(packets[-1]); damaged[-1] ^= 1
        outputs = [self.a.feed(p,0) for p in packets[:-1]+[damaged]]
        self.assertTrue(all(x is None for x in outputs))

    def test_newer_frame_supersedes_incomplete_and_handles_wrap(self):
        first = encode_frame(self.rgb,123,0xFFFFFFFF)
        self.a.feed(first[0],0)
        next_packets = encode_frame(self.rgb,123,0)
        outputs = [self.a.feed(p,.01) for p in next_packets]
        self.assertEqual(outputs[-1],self.rgb)
        self.assertIsNone(self.a.feed(first[-1],.02))


class RendererTests(unittest.TestCase):
    def test_mapping_and_red_mask(self):
        cfg = RuntimeConfig()
        renderer = Compositor(cfg)
        self.assertEqual(renderer.pixel(Point(171,257.5)),(32,32))
        c = DemoController(cfg); c.cursor = Point(171,257.5)
        rgb = renderer.render(c,0,True)
        self.assertFalse(any(rgb[0::3]))
        self.assertGreater(max(rgb[1::3]),0)

    def test_trail_fades_and_only_records_drag_telemetry(self):
        cfg = RuntimeConfig()
        renderer = Compositor(cfg)
        status = DeviceSnapshot(connected=True,mode='TASK_MOVE',position=Point(200,200),received_at=1)
        renderer.record(status,1,False)
        self.assertEqual(len(renderer.trail),0)
        renderer.record(replace(status,received_at=2),2,True)
        c = DemoController(cfg)
        x,y = renderer.pixel(status.position)
        offset=(y*64+x)*3+2
        self.assertEqual(renderer.render(c,2)[offset],220)
        self.assertEqual(renderer.render(c,2.75)[offset],110)
        self.assertEqual(renderer.render(c,3.6)[offset],0)
        self.assertEqual(len(renderer.trail),0)


if __name__ == '__main__':
    unittest.main()
