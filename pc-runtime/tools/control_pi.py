"""Manual TCP control of Pi Bridge; requires a mechanically clear mechanism.

No servo angles are changed. No automatic reconnect or motion replay is used.
"""
import argparse
from collections import deque
import json
import math
from pathlib import Path
import secrets
import socket
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tabletop_os.frame_protocol import encode_frame, FRAME_BYTES, HEIGHT, WIDTH


class PanelDisplay:
    """Diagnostic map on one 64x32 panel or the complete 64x96 display."""
    def __init__(self, host, port, test=False, full=False, rotation=0):
        if rotation not in (0, 180):
            raise ValueError("显示旋转角度须为 0 或 180")
        self.rotation = rotation
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.udp.connect((host, port))
        except BaseException:
            self.udp.close()
            raise
        self.session = secrets.randbelow(0xFFFFFFFF) + 1
        self.sequence = 0
        self.requested = self.opened = self.received = False
        self.next_frame = 0
        self.test = test
        self.height = HEIGHT if full else 32
        self.status = {}
        self.status_at = 0
        self.trail = deque(maxlen=128)

    @staticmethod
    def point(x, y, height=32):
        # With the blue status edge at the bottom, home is bottom-left;
        # positive X moves right and positive Y moves up. Rotation is applied
        # later to pixels, without changing any motor coordinates.
        return (2 + round(max(0., min(1., x / 298.)) * 59),
                height - 8 - round(max(0., min(1., y / 466.)) * (height - 10)))

    def record(self, msg, now):
        display = msg.get("display", {})
        if display.get("mode") != "frames":
            raise ControlError("Pi 显示模式须为 frames")
        if (display.get("session") == self.session
                and display.get("frame_seq", 0) > 0
                and 0 <= display.get("frame_age_ms", -1) < 250):
            self.received = True
        self.status, self.status_at = msg["arduino"], now
        a = self.status
        if (a["referenced"] and 0 <= a["telemetry_age_ms"] < 500
                and a["mode"] in ("TASK_MOVE", "STOPPING")):
            point = self.point(a["actual_x_mm"], a["actual_y_mm"], self.height)
            if not self.trail or self.trail[-1] != point:
                self.trail.append(point)

    def render(self, now):
        rgb = bytearray(FRAME_BYTES)

        def pixel(x, y, green, blue):
            if 0 <= x < WIDTH and 0 <= y < self.height:
                if self.rotation == 180:
                    x, y = WIDTH - 1 - x, self.height - 1 - y
                offset = (y * WIDTH + x) * 3
                rgb[offset:offset + 3] = bytes((0, green, blue))

        def line(p0, p1, green, blue):
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            steps = max(abs(dx), abs(dy), 1)
            for step in range(steps + 1):
                pixel(round(p0[0] + dx * step / steps),
                      round(p0[1] + dy * step / steps), green, blue)

        for x in range(WIDTH):
            pixel(x, 0, 20, 35)
            pixel(x, self.height - 1, 20, 35)
        for y in range(self.height):
            pixel(0, y, 20, 35)
            pixel(WIDTH - 1, y, 20, 35)
        if self.test:
            # Large "1", plus green left and blue right corner markers.
            for y in range(6, 26):
                for x in range(30, 34):
                    pixel(x, y, 150, 180)
            line((25, 10), (31, 6), 150, 180)
            line((25, 26), (38, 26), 150, 180)
            for y in range(2, 6):
                for x in range(2, 6):
                    pixel(x, y, 150, 0)
                    pixel(WIDTH - 1 - x, y, 0, 180)
            if self.height >= 64:
                # A "2" on the lower panel distinguishes panel order.
                line((25, 38), (38, 38), 150, 180)
                line((38, 38), (38, 45), 150, 180)
                line((38, 45), (25, 53), 150, 180)
                line((25, 53), (25, 57), 150, 180)
                line((25, 57), (38, 57), 150, 180)
            if self.height >= 96:
                # A "3" on the third panel completes the three-panel order test.
                line((25, 70), (38, 70), 150, 180)
                line((38, 70), (38, 89), 150, 180)
                line((25, 79), (38, 79), 150, 180)
                line((25, 89), (38, 89), 150, 180)
            line((3, self.height - 4), (60, self.height - 4), 0, 160)
            return bytes(rgb)

        for x in range(12, WIDTH - 1, 12):
            for y in range(2, self.height - 7):
                pixel(x, y, 3, 10)
        for y in range(6, self.height - 7, 6):
            for x in range(2, 62):
                pixel(x, y, 3, 10)
        a = self.status
        fresh = (a and a["fault"] == "NONE" and a["ready"]
                 and 0 <= a["telemetry_age_ms"]
                 and now - self.status_at + a["telemetry_age_ms"] / 1000 < .5)
        if fresh and a["referenced"]:
            previous = None
            for point in self.trail:
                if previous:
                    line(previous, point, 0, 100)
                previous = point
            if a["mode"] == "TASK_MOVE":
                x, y = self.point(a["target_x_mm"], a["target_y_mm"], self.height)
                line((x - 2, y), (x + 2, y), 180, 0)
                line((x, y - 2), (x, y + 2), 180, 0)
            x, y = self.point(a["actual_x_mm"], a["actual_y_mm"], self.height)
            line((x - 1, y), (x + 1, y), 180, 220)
            line((x, y - 1), (x, y + 1), 180, 220)
        # Bottom status bar: blue while homing, green when referenced/idle.
        if fresh:
            green = 120 if a["referenced"] and a["mode"] == "IDLE" else 0
            blue = 160 if a["mode"] in ("HOMING", "TASK_MOVE") else 35
            line((3, self.height - 4), (60, self.height - 4), green, blue)
        else:
            offset = (self.height - 32) // 2
            line((27, 10 + offset), (36, 19 + offset), 0, 140)
            line((27, 19 + offset), (36, 10 + offset), 0, 140)
        return bytes(rgb)

    def tick(self, now):
        if self.opened and now >= self.next_frame:
            self.sequence = (self.sequence + 1) & 0xFFFFFFFF
            for packet in encode_frame(self.render(now), self.session, self.sequence):
                self.udp.send(packet)
            self.next_frame = now + .05

    def close(self):
        self.udp.close()


class ControlError(RuntimeError):
    pass


class Session:
    def __init__(self, host, port, display=False, frame_port=7422, panel_test=False,
                 full_display=False, rotation=0):
        self.sock = socket.create_connection((host, port), timeout=3)
        self.sock.settimeout(0.1)
        self.buffer = b""
        self.heartbeat = False
        self.last_ack = time.monotonic()
        self.next_beat = 0
        self.panel = None
        if display:
            try:
                self.panel = PanelDisplay(host, frame_port, panel_test, full_display, rotation)
                self.sock.settimeout(0.02)
            except BaseException:
                self.sock.close()
                raise

    def send(self, kind, **fields):
        request = uuid.uuid4().hex
        msg = {"v": 1, "type": kind, "request_id": request, **fields}
        self.sock.sendall((json.dumps(msg, allow_nan=False) + "\n").encode())
        return request

    def messages(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            now = time.monotonic()
            if self.heartbeat:
                if now - self.last_ack > 1.2:
                    raise ControlError("Pi 心跳回复超时，已中止测试")
                if now >= self.next_beat:
                    self.send("heartbeat")
                    self.send("status.get")
                    self.next_beat = now + 0.4
            if self.panel:
                self.panel.tick(now)
            try:
                data = self.sock.recv(8192)
            except socket.timeout:
                continue
            if not data:
                raise ControlError("Pi 已断开连接；不会自动重发运动")
            self.buffer += data
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                if len(line) > 4096:
                    raise ControlError("Pi 回复过长")
                msg = json.loads(line)
                if not isinstance(msg, dict) or msg.get("v") != 1:
                    raise ControlError("Pi 回复协议不兼容")
                if msg.get("type") == "heartbeat.ack":
                    self.last_ack = time.monotonic()
                    if self.panel and not self.panel.requested:
                        self.send("display.open", session=self.panel.session)
                        self.panel.requested = True
                if self.panel:
                    if (msg.get("type") == "display.opened"
                            and msg.get("session") == self.panel.session):
                        self.panel.opened = True
                    elif msg.get("type") == "status":
                        self.panel.record(msg, time.monotonic())
                if msg.get("type") in (
                    "error", "arduino.error", "motion.interlock",
                    "motion.cancelled", "control.lease_expired",
                ):
                    raise ControlError(json.dumps(msg, ensure_ascii=False))
                yield msg
            if len(self.buffer) > 4096:
                raise ControlError("Pi 回复缺少换行或过长")
        raise ControlError("等待 Pi 回复或动作完成超时")

    def stop(self):
        # An ACK only confirms dispatch. Wait for the Arduino STOPPED event.
        self.heartbeat = False
        if self.panel:
            self.panel.opened = False
        self.send("motion.stop")
        for msg in self.messages(3):
            if msg.get("type") == "motion.stopped":
                return msg

    def close(self):
        try:
            self.sock.close()
        finally:
            if self.panel:
                self.panel.close()


def ready_to_move(msg, command):
    a = msg["arduino"]
    if not msg["motion_commands_enabled"]:
        raise ControlError("Pi 运动锁仍开启：motion.allow_network_commands=false")
    if a["fault"] != "NONE":
        raise ControlError("Arduino 故障：" + a["fault"])
    ready = (
        a["connected"] and a["ready"] and a["firmware_compatible"]
        and a["config_confirmed"] and a["watchdog_enabled"]
        and 0 <= a["telemetry_age_ms"] < 500
        and msg["command_id_synchronized"] and msg["control_lease_valid"]
        and not msg["motion_command_in_flight"]
    )
    if not ready:
        return False
    if command == "move" and not a["referenced"]:
        raise ControlError("Arduino 尚未回零，请先执行 home")
    return a["mode"] in (("IDLE", "UNREFERENCED") if command == "home" else ("IDLE",))


def run(args):
    full_display = getattr(args, "display_all", False)
    rotation = getattr(args, "rotation", None)
    if rotation is None:
        rotation = 180 if full_display else 0
    display = getattr(args, "display_first", False) or full_display or args.command == "panel-test"
    session = Session(args.host, args.port, display,
                      getattr(args, "frame_port", 7422), args.command == "panel-test", full_display,
                      rotation)
    motion_sent = False
    completed = False
    try:
        if args.command == "status":
            request = session.send("status.get")
            for msg in session.messages(5):
                if msg.get("type") == "status" and msg.get("reply_to") == request:
                    print(json.dumps(msg, indent=2, ensure_ascii=False), flush=True)
                    return msg
        if args.command == "stop":
            msg = session.stop()
            print("Arduino 已回复停止：", msg.get("reason"), flush=True)
            return msg

        session.heartbeat = True
        if args.command == "panel-test":
            started_at = None
            if full_display:
                print("整屏测试：数字 1、2、3 和蓝条已整体旋转 180°。" if rotation else
                      "整屏测试：三块板依次显示 1、2、3，蓝条位于整屏最下方。", flush=True)
            else:
                print("第一块灯板测试：应显示数字 1，第二、三块保持黑屏。", flush=True)
            for msg in session.messages(args.seconds + 10):
                if session.panel.received:
                    if started_at is None:
                        started_at = time.monotonic()
                        print("Pi 已确认收到显示帧，请观察灯板。", flush=True)
                    if time.monotonic() - started_at >= args.seconds:
                        print("显示测试结束，画面自动清除。", flush=True)
                        return msg
                elif time.monotonic() - session.last_ack > 1.2:
                    raise ControlError("Pi 未回复心跳")
        for msg in session.messages(10):
            if (msg.get("type") == "status" and ready_to_move(msg, args.command)
                    and (not session.panel or session.panel.received)):
                break
        fields = {} if args.command == "home" else {
            "x_mm": args.x, "y_mm": args.y, "speed_mm_s": args.speed,
        }
        if args.command == "move":
            a = msg["arduino"]
            dx, dy = args.x - a["actual_x_mm"], args.y - a["actual_y_mm"]
            print(f'绝对目标 ({args.x:.2f}, {args.y:.2f}) mm；'
                  f'机构报告起点 ({a["actual_x_mm"]:.2f}, {a["actual_y_mm"]:.2f}) mm；'
                  f'本次 X 变化 {dx:+.2f}、Y 变化 {dy:+.2f} mm；'
                  f'速度 {args.speed:g} mm/s', flush=True)
        # Treat a failed send as potentially delivered; request STOP on cleanup.
        motion_sent = True
        request = session.send("motion.home" if args.command == "home" else "motion.move_to", **fields)
        last_report = 0
        for msg in session.messages(args.timeout):
            kind = msg.get("type")
            if kind == "motion.stopped":
                raise ControlError("动作提前停止：" + str(msg))
            if kind == "status":
                a = msg["arduino"]
                if a["fault"] != "NONE":
                    raise ControlError("Arduino 故障：" + a["fault"])
                if time.monotonic() - last_report >= 1:
                    print(f'{a["mode"]}: X={a["actual_x_mm"]:.2f}, Y={a["actual_y_mm"]:.2f} mm', flush=True)
                    last_report = time.monotonic()
            if msg.get("reply_to") == request:
                if kind in ("motion.accepted", "motion.started"):
                    print(kind, flush=True)
                if kind == "motion.completed":
                    completed = True
                    print("回零完成" if args.command == "home" else "机构移动完成", flush=True)
                    if session.panel:
                        # Show the completed telemetry for one second before clearing.
                        until = time.monotonic() + 1
                        for final in session.messages(2):
                            if time.monotonic() >= until:
                                break
                    return msg
    finally:
        try:
            if motion_sent and not completed:
                try:
                    session.stop()
                    print("Arduino 已回复停止", file=sys.stderr, flush=True)
                except (OSError, ValueError, ControlError) as exc:
                    print("未能确认停止，请检查实物：" + str(exc), file=sys.stderr, flush=True)
        finally:
            session.close()


def finite_number(text):
    number = float(text)
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError("必须是有限数字")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description="电脑通过 Pi Bridge 手动控制 Arduino；磁铁须已抬起并固定")
    parser.add_argument("--host", required=True, help="树莓派 IP")
    parser.add_argument("--port", type=int, default=7420)
    display_options = parser.add_mutually_exclusive_group()
    display_options.add_argument("--display-first", action="store_true", help="只在第一块灯板显示")
    display_options.add_argument("--display-all", action="store_true", help="使用三块拼接的完整 64×96 灯板")
    parser.add_argument("--rotation", type=int, choices=(0, 180), default=None,
                        help="仅旋转显示；整屏默认 180°，单块默认 0°")
    parser.add_argument("--frame-port", type=int, default=7422)
    parser.add_argument("--timeout", type=finite_number, default=180, help="动作超时秒数")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "home", "stop"):
        commands.add_parser(name)
    panel = commands.add_parser("panel-test", help="测试灯板，不发送运动命令；可搭配 --display-all")
    panel.add_argument("--seconds", type=finite_number, default=10)
    move = commands.add_parser("move", help="移动到绝对坐标，单位 mm")
    move.add_argument("--x", type=finite_number, required=True)
    move.add_argument("--y", type=finite_number, required=True)
    move.add_argument("--speed", type=finite_number, default=2, help="速度 mm/s，默认 2")
    args = parser.parse_args(argv)
    if args.timeout <= 0 or (args.command == "move" and not 0.5 <= args.speed <= 50):
        parser.error("超时须大于 0，移动速度须为 0.5–50 mm/s")
    if args.command == "panel-test" and not 0 < args.seconds <= 300:
        parser.error("显示测试时间须大于 0 且不超过 300 秒")
    if (args.display_first or args.display_all) and args.command in ("status", "stop"):
        parser.error("status/stop 不开启显示；请用 panel-test 或在 home/move 前加 --display-first/--display-all")
    try:
        run(args)
    except KeyboardInterrupt:
        print("测试已中断", file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError, ControlError) as exc:
        print("控制失败：" + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
