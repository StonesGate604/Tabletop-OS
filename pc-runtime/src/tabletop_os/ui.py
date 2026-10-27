"""Local Tk operator console; simulation requires no imaging dependencies."""
import logging
import time
import tkinter as tk
from tkinter import ttk
from .model import InputSample, Point
from .vision import CameraWorker

LOG = logging.getLogger(__name__)


def run_window(runtime, camera: bool):
    root = tk.Tk()
    root.title("Tabletop OS V0.1 — "+("SIMULATION" if runtime.simulate else "HARDWARE"))
    root.configure(bg="#10151d")
    canvas_width = runtime.config.board.width_px * 8
    canvas_height = runtime.config.board.height_px * 8
    canvas = tk.Canvas(root, width=canvas_width, height=canvas_height,
                       bg="black", highlightthickness=0)
    canvas.grid(row=0, column=0, rowspan=3, padx=16, pady=16)
    image_item = canvas.create_image(0, 0, anchor="nw")
    panel = ttk.Frame(root, padding=16)
    panel.grid(row=0, column=1, sticky="n")
    title = "仿真模式：不会连接任何硬件" if runtime.simulate else "硬件模式：需标定、回零和设备联锁"
    ttk.Label(panel, text=title).pack(anchor="w")
    ttk.Label(panel, text="移动鼠标定位；左键两次：选中 → 目标确认\n第一次点击后先松开，再移动。\n相机模式用同样的两次捏合。\nSpace：停止；R：恢复；H：回零").pack(anchor="w", pady=10)
    status_label = ttk.Label(panel, width=55, justify="left")
    status_label.pack(anchor="w")
    ttk.Button(panel, text="STOP / 停止", command=runtime.stop).pack(fill="x", pady=6)
    ttk.Button(panel, text="恢复到待选状态（不重发任务）", command=runtime.reset).pack(fill="x")
    ttk.Button(panel, text="HOME / 回零", command=lambda: runtime.home(time.monotonic())).pack(fill="x", pady=6)
    if runtime.simulate:
        slip = tk.BooleanVar()
        missing = tk.BooleanVar()
        ttk.Checkbutton(panel, text="模拟棋子滑脱", variable=slip,
                        command=lambda: setattr(runtime.device, "slip", slip.get())).pack(anchor="w")
        ttk.Checkbutton(panel, text="模拟标签丢失", variable=missing,
                        command=lambda: setattr(runtime.device, "tag_visible", not missing.get())).pack(anchor="w")
    preview = ttk.Label(panel)
    preview.pack(pady=10)
    mouse = {"position": None, "down": False}
    def pointer(event):
        b = runtime.config.board
        x = event.x/canvas_width*b.width_mm
        y = event.y/canvas_height*b.height_mm
        if b.flip_x:
            x = b.width_mm-x
        if b.flip_y:
            y = b.height_mm-y
        mouse["position"] = Point(x, y)
    canvas.bind("<Motion>", pointer)
    canvas.bind("<ButtonPress-1>", lambda event: (pointer(event), mouse.update(down=True)))
    canvas.bind("<ButtonRelease-1>", lambda event: (pointer(event), mouse.update(down=False)))
    canvas.bind("<Leave>", lambda _: mouse.update(position=None, down=False))
    root.bind("<space>", lambda _: runtime.stop())
    root.bind("r", lambda _: runtime.reset())
    root.bind("h", lambda _: runtime.home(time.monotonic()))
    worker = CameraWorker(runtime.config) if camera else None
    closed = False
    last_vision_error = ""
    def close():
        nonlocal closed
        closed = True
        runtime.close()
        if worker:
            worker.close()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", close)
    runtime.start()
    if worker:
        worker.start()
    def update():
        nonlocal last_vision_error
        if closed:
            return
        now = time.monotonic()
        observations = ()
        if worker:
            result = worker.latest()
            sample, observations = result.sample, result.observations
            if result.error and result.error != last_vision_error:
                LOG.error("Vision: %s", result.error)
                last_vision_error = result.error
            if result.preview:
                preview.image = tk.PhotoImage(data=result.preview, format="PPM")
                preview.configure(image=preview.image)
        else:
            sample = InputSample(mouse["position"], 0.1 if mouse["down"] else 0.9, now)
        try:
            rgb = runtime.tick(sample, observations, now)
            header = f"P6\n{runtime.config.board.width_px} {runtime.config.board.height_px}\n255\n".encode()
            image = tk.PhotoImage(data=header+rgb, format="PPM").zoom(8)
            canvas.image = image
            canvas.itemconfigure(image_item, image=image)
            c, s = runtime.controller, runtime.device.snapshot()
            observed = c.observation.position if c.observation else None
            status_label.configure(text=f"状态：{c.phase.value} / {runtime.home_stage or runtime.sequence.stage}\n"
                f"执行器：({s.position.x:.1f}, {s.position.y:.1f}) mm\n"
                f"棋子观测：{observed}\n目标：{c.target}\n"
                f"连接：{s.connected}  回零：{s.referenced}  模式：{s.mode}\n"
                f"舵机标定：{runtime.config.gripper.calibrated}\n"
                f"已验证移动：{c.completed_moves}\n错误：{c.error or last_vision_error or '无'}")
        except Exception:
            LOG.exception("Runtime update failed")
            runtime.stop()
        root.after(max(1, int(1000/runtime.config.render.fps)), update)
    root.after(0, update)
    try:
        root.mainloop()
    finally:
        if not closed:
            runtime.close()
            if worker:
                worker.close()
