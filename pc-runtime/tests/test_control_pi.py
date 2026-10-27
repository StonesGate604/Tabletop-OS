"""Loopback protocol tests: no Pi, serial port or motors are used."""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import socket
import threading
import time
import unittest

spec = importlib.util.spec_from_file_location(
    "control_pi", Path(__file__).resolve().parents[1] / "tools/control_pi.py")
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class Bridge:
    def __init__(self, referenced=True, failure=False, complete=True, ack_only=False,
                 reject_display=False):
        self.referenced, self.failure = referenced, failure
        self.complete, self.ack_only = complete, ack_only
        self.reject_display = reject_display
        self.requests, self.errors = [], []
        from tabletop_os.frame_protocol import FrameAssembler
        self.assembler = FrameAssembler()
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("127.0.0.1", 0))
        self.frame_port = self.udp.getsockname()[1]
        self.udp.setblocking(False)
        self.frames = []
        self.last_frame_at = 0
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.listener.listen()
        self.listener.settimeout(3)
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.thread.join(4)
        self.listener.close()
        self.udp.close()
        if self.thread.is_alive() or self.errors:
            raise AssertionError(str(self.errors) or "fake bridge did not stop")

    def serve(self):
        try:
            with self.listener.accept()[0] as conn:
                conn.settimeout(.05)
                pending, due, buffer, lease = None, 0, b"", False

                def send(msg):
                    data = (json.dumps({"v": 1, **msg}) + "\n").encode()
                    # Exercise TCP fragmentation, rather than one recv per line.
                    conn.sendall(data[:7])
                    conn.sendall(data[7:])

                while True:
                    while True:
                        try:
                            packet = self.udp.recv(2048)
                        except BlockingIOError:
                            break
                        pixels = self.assembler.feed(packet, time.monotonic())
                        if pixels is not None:
                            self.frames.append(pixels)
                            self.last_frame_at = time.monotonic()
                    if pending and self.complete and time.monotonic() >= due:
                        send({"type": "motion.completed", "reply_to": pending})
                        pending = None
                    try:
                        data = conn.recv(8192)
                    except socket.timeout:
                        continue
                    if not data:
                        return
                    buffer += data
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        msg = json.loads(line)
                        self.requests.append(msg)
                        kind, request = msg["type"], msg["request_id"]
                        if kind == "heartbeat":
                            lease = True
                            send({"type": "heartbeat.ack", "reply_to": request})
                        elif kind == "status.get":
                            send({"type": "status", "reply_to": request,
                                  "motion_commands_enabled": True,
                                  "command_id_synchronized": True,
                                  "control_lease_valid": lease,
                                  "motion_command_in_flight": bool(pending),
                                  "display": {"mode": "frames", "session": self.assembler.session,
                                      "frame_seq": self.assembler.frame_id if self.frames else 0,
                                      "frame_age_ms": int((time.monotonic() - self.last_frame_at) * 1000)
                                          if self.frames else -1},
                                  "arduino": {"connected": True, "ready": True,
                                      "firmware_compatible": True, "config_confirmed": True,
                                      "watchdog_enabled": True, "telemetry_age_ms": 25,
                                      "referenced": self.referenced, "fault": "NONE",
                                      "mode": "HOMING" if pending else "IDLE",
                                      "actual_x_mm": 20, "actual_y_mm": 20,
                                      "target_x_mm": 22, "target_y_mm": 20}})
                        elif kind == "display.open":
                            if self.reject_display:
                                send({"type": "error", "code": "BAD_MESSAGE", "reply_to": request})
                            else:
                                self.assembler.set_session(msg["session"])
                                send({"type": "display.opened", "session": msg["session"],
                                      "reply_to": request})
                        elif kind in ("motion.home", "motion.move_to"):
                            if self.failure:
                                send({"type": "arduino.error", "code": "HOST_TIMEOUT",
                                      "reply_to": request})
                            else:
                                pending, due = request, time.monotonic() + .9
                                send({"type": "motion.accepted", "reply_to": request})
                        elif kind == "motion.stop":
                            pending = None
                            send({"type": "ack", "reply_to": request, "status": "STOP_SENT"})
                            if not self.ack_only:
                                send({"type": "motion.stopped", "reason": "USER"})
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass
        except Exception as exc:
            self.errors.append(exc)


def args(bridge, command, timeout=3):
    return argparse.Namespace(host="127.0.0.1", port=bridge.port,
                              command=command, timeout=timeout, x=22., y=20., speed=2.,
                              display_first=False, display_all=False,
                              frame_port=bridge.frame_port, seconds=.2, rotation=None)


class ManualControlTests(unittest.TestCase):
    def setUp(self):
        self.out = redirect_stdout(io.StringIO())
        self.err = redirect_stderr(io.StringIO())
        self.out.__enter__()
        self.err.__enter__()
        self.addCleanup(self.out.__exit__, None, None, None)
        self.addCleanup(self.err.__exit__, None, None, None)

    def test_fragmented_status_does_not_send_motion(self):
        with Bridge() as b:
            self.assertEqual(control.run(args(b, "status"))["type"], "status")
        self.assertEqual([m["type"] for m in b.requests], ["status.get"])

    def test_home_keeps_heartbeat_alive_until_completion(self):
        with Bridge(referenced=False) as b:
            self.assertEqual(control.run(args(b, "home"))["type"], "motion.completed")
        self.assertGreaterEqual(sum(m["type"] == "heartbeat" for m in b.requests), 3)
        self.assertEqual(sum(m["type"] == "motion.home" for m in b.requests), 1)

    def test_move_requires_home_and_does_not_send_target(self):
        with Bridge(referenced=False) as b:
            with self.assertRaisesRegex(control.ControlError, "尚未回零"):
                control.run(args(b, "move"))
        self.assertFalse(any(m["type"] == "motion.move_to" for m in b.requests))

    def test_move_sends_mm_target_and_low_speed(self):
        with Bridge() as b:
            control.run(args(b, "move"))
        move = next(m for m in b.requests if m["type"] == "motion.move_to")
        self.assertEqual((move["x_mm"], move["y_mm"], move["speed_mm_s"]), (22, 20, 2))

    def test_firmware_error_requests_stop_without_replaying(self):
        with Bridge(failure=True) as b:
            with self.assertRaisesRegex(control.ControlError, "HOST_TIMEOUT"):
                control.run(args(b, "home"))
        self.assertEqual(sum(m["type"] == "motion.home" for m in b.requests), 1)
        self.assertTrue(any(m["type"] == "motion.stop" for m in b.requests))

    def test_action_timeout_requests_stop(self):
        with Bridge(complete=False) as b:
            with self.assertRaisesRegex(control.ControlError, "超时"):
                control.run(args(b, "home", timeout=.2))
        self.assertTrue(any(m["type"] == "motion.stop" for m in b.requests))

    def test_stop_ack_is_not_stopped_confirmation(self):
        with Bridge(ack_only=True) as b:
            with self.assertRaisesRegex(control.ControlError, "超时"):
                control.run(args(b, "stop"))

    def test_panel_test_sends_valid_frames_without_any_motion_command(self):
        with Bridge() as b:
            control.run(args(b, "panel-test"))
        self.assertGreater(len(b.frames), 1)
        self.assertTrue(all(not any(frame[64 * 32 * 3:]) for frame in b.frames))
        self.assertTrue(all(not any(frame[::3]) for frame in b.frames))
        self.assertFalse(any(m["type"].startswith("motion.") for m in b.requests))

    def test_full_panel_test_streams_both_halves_and_bottom_bar_without_motion(self):
        with Bridge() as b:
            options = args(b, "panel-test")
            options.display_all = True
            control.run(options)
        self.assertGreater(len(b.frames), 1)
        for pixels in b.frames:
            self.assertTrue(any(pixels[:64 * 32 * 3]))
            self.assertTrue(any(pixels[64 * 32 * 3:]))
            self.assertFalse(any(pixels[::3]))
            # Full display defaults to 180 degrees: y=60 becomes y=3.
            offset = (3 * 64 + 60) * 3
            self.assertEqual(pixels[offset:offset + 3], bytes((0, 0, 160)))
        self.assertFalse(any(m["type"].startswith("motion.") for m in b.requests))

    def test_rotated_full_display_does_not_rotate_motor_coordinates(self):
        with Bridge() as b:
            options = args(b, "move")
            options.display_all, options.rotation = True, 180
            control.run(options)
        move = next(m for m in b.requests if m["type"] == "motion.move_to")
        self.assertEqual((move["x_mm"], move["y_mm"], move["speed_mm_s"]), (22, 20, 2))

    def test_home_with_display_waits_for_udp_confirmation_and_keeps_streaming(self):
        with Bridge() as b:
            options = args(b, "home")
            options.display_first = True
            control.run(options)
        kinds = [m["type"] for m in b.requests]
        self.assertLess(kinds.index("display.open"), kinds.index("motion.home"))
        self.assertGreater(len(b.frames), 10)
        self.assertTrue(all(not any(frame[64 * 32 * 3:]) for frame in b.frames))

    def test_display_refusal_prevents_motion(self):
        with Bridge(reject_display=True) as b:
            options = args(b, "home")
            options.display_first = True
            with self.assertRaisesRegex(control.ControlError, "BAD_MESSAGE"):
                control.run(options)
        self.assertFalse(any(m["type"] == "motion.home" for m in b.requests))


class FirstPanelRenderTests(unittest.TestCase):
    def test_full_rotation_reverses_pixel_positions_without_swapping_colors(self):
        normal = control.PanelDisplay("127.0.0.1", 9, test=True, full=True)
        rotated = control.PanelDisplay("127.0.0.1", 9, test=True, full=True, rotation=180)
        try:
            pixels = normal.render(10)
            expected = b"".join(pixels[i:i + 3] for i in range(len(pixels) - 3, -1, -3))
            self.assertEqual(rotated.render(10), expected)
        finally:
            normal.close()
            rotated.close()

    def test_single_panel_rotation_keeps_second_panel_black(self):
        panel = control.PanelDisplay("127.0.0.1", 9, test=True, rotation=180)
        try:
            pixels = panel.render(10)
            self.assertTrue(any(pixels[:64 * 32 * 3]))
            self.assertFalse(any(pixels[64 * 32 * 3:]))
        finally:
            panel.close()

    def test_position_and_target_are_clipped_to_first_panel(self):
        self.assertEqual(control.PanelDisplay.point(-20, -30), (2, 24))
        self.assertEqual(control.PanelDisplay.point(298, 466), (61, 2))
        self.assertEqual(control.PanelDisplay.point(1000, 1000), (61, 2))

    def test_full_display_home_is_bottom_left_and_positive_axes_are_right_up(self):
        self.assertEqual(control.PanelDisplay.point(0, 0, 64), (2, 56))
        self.assertEqual(control.PanelDisplay.point(298, 0, 64), (61, 56))
        self.assertEqual(control.PanelDisplay.point(0, 466, 64), (2, 2))
        start = control.PanelDisplay.point(20, 20, 64)
        right = control.PanelDisplay.point(40, 20, 64)
        up = control.PanelDisplay.point(20, 40, 64)
        self.assertGreater(right[0], start[0])
        self.assertEqual(right[1], start[1])
        self.assertEqual(up[0], start[0])
        self.assertLess(up[1], start[1])

    def test_render_has_no_red_or_second_panel_pixels(self):
        panel = control.PanelDisplay("127.0.0.1", 9, test=True)
        try:
            pixels = panel.render(10)
            self.assertEqual(len(pixels), 64 * 64 * 3)
            self.assertTrue(any(pixels[:64 * 32 * 3]))
            self.assertFalse(any(pixels[64 * 32 * 3:]))
            self.assertFalse(any(pixels[::3]))
        finally:
            panel.close()

    def test_full_display_status_bar_is_at_whole_display_bottom(self):
        panel = control.PanelDisplay("127.0.0.1", 9, full=True)
        try:
            panel.status = {"fault": "NONE", "ready": True, "telemetry_age_ms": 0,
                            "referenced": False, "mode": "HOMING"}
            panel.status_at = 10
            pixels = panel.render(10)
            def at(x, y):
                offset = (y * 64 + x) * 3
                return pixels[offset:offset + 3]
            self.assertEqual(at(3, 60), bytes((0, 0, 160)))
            self.assertEqual(at(3, 28), bytes((0, 0, 0)))
            self.assertEqual(control.PanelDisplay.point(298, 466, 64), (61, 2))
            self.assertFalse(any(pixels[::3]))
        finally:
            panel.close()


if __name__ == "__main__":
    unittest.main()
