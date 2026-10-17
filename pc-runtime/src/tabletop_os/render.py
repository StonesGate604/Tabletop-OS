"""Pure RGB compositor. No hardware calls and no third-party dependencies."""
from collections import deque
from .config import RuntimeConfig
from .model import Point, DeviceSnapshot
from .interaction import DemoController, Phase


class Compositor:
    def __init__(self, config: RuntimeConfig):
        self.config = config
        self.trail = deque(maxlen=config.render.trail_max_points)
        self.last_telemetry_at = 0.0
        self.rgb = bytearray(config.board.width_px * config.board.height_px * 3)

    def pixel(self, p: Point) -> tuple[int, int]:
        b = self.config.board
        u, v = min(1, max(0, p.x/b.width_mm)), min(1, max(0, p.y/b.height_mm))
        if b.flip_x:
            u = 1-u
        if b.flip_y:
            v = 1-v
        return int(u*(b.width_px-1)+0.5), int(v*(b.height_px-1)+0.5)

    def put(self, x: int, y: int, green: int, blue: int, overwrite: bool = False):
        b = self.config.board
        if not (0 <= x < b.width_px and 0 <= y < b.height_px):
            return
        offset = (y*b.width_px+x)*3
        self.rgb[offset] = 0
        for index, value in ((offset+1, green), (offset+2, blue)):
            value = max(0, min(255, int(value+0.5)))
            self.rgb[index] = value if overwrite else max(self.rgb[index], value)

    def circle(self, p: Point, radius: int, green: int, blue: int):
        cx, cy = self.pixel(p)
        x, y, error = radius, 0, 1-radius
        while x >= y:
            for dx, dy in ((x,y),(y,x),(-y,x),(-x,y),(-x,-y),(-y,-x),(y,-x),(x,-y)):
                self.put(cx+dx, cy+dy, green, blue, True)
            y += 1
            if error < 0:
                error += 2*y+1
            else:
                x -= 1
                error += 2*(y-x)+1

    def record(self, status: DeviceSnapshot, now: float, dragging: bool):
        if not status.connected:
            self.trail.clear()
        if dragging and status.mode in ("TASK_MOVE", "STOPPING") and status.received_at > self.last_telemetry_at:
            if not self.trail or status.position.distance(self.trail[-1][0]) >= self.config.render.trail_min_step_mm:
                self.trail.append((status.position, now))
        self.last_telemetry_at = status.received_at

    def render(self, controller: DemoController, now: float, pinched: bool = False) -> bytes:
        self.rgb[:] = b"\0" * len(self.rgb)
        cfg = self.config.render
        lifetime = cfg.trail_lifetime_ms/1000
        while self.trail and now-self.trail[0][1] > lifetime:
            self.trail.popleft()
        previous = None
        for p, timestamp in self.trail:
            x, y = self.pixel(p)
            alpha = max(0, min(1, 1-(now-timestamp)/lifetime))
            if previous:
                x0, y0, a0 = previous
                steps = max(abs(x-x0), abs(y-y0), 1)
                for step in range(steps+1):
                    t = step/steps
                    self.put(int(x0+(x-x0)*t+0.5), int(y0+(y-y0)*t+0.5), 0,
                             cfg.trail_blue*(a0+(alpha-a0)*t))
            else:
                self.put(x, y, 0, cfg.trail_blue*alpha)
            previous = x, y, alpha
        self.circle(controller.piece, 2, 35, 25)
        if controller.phase in (Phase.SELECTED, Phase.AIMING):
            self.circle(controller.piece, 4, 220, 20)
        if controller.target:
            self.circle(controller.target, 2, 80, 70)
        if controller.cursor:
            self.circle(controller.cursor, cfg.cursor_radius_px, cfg.cursor_green, cfg.cursor_blue)
            if pinched:
                self.circle(controller.cursor, cfg.cursor_radius_px+2, cfg.cursor_green*0.7, cfg.cursor_blue*0.7)
                x, y = self.pixel(controller.cursor)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        self.put(x+dx, y+dy, cfg.cursor_green, cfg.cursor_blue, True)
        return bytes(self.rgb)
