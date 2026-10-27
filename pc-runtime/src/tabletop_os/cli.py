"""CLI entry point; deterministic demo is explicitly limited to simulation."""
import argparse
import json
import logging
from pathlib import Path
from .config import load_config
from .interaction import Phase
from .model import InputSample, Point
from .runtime import Runtime


def scripted_input(now: float, start: Point, target: Point) -> InputSample:
    if now < 0.2:
        return InputSample(start, 0.9, now)
    if now < 0.5:
        return InputSample(start, 0.1, now)
    if now < 0.8:
        return InputSample(start, 0.9, now)
    if now < 1.2:
        return InputSample(target, 0.9, now)
    if now < 1.6:
        return InputSample(target, 0.1, now)
    return InputSample(target, 0.9, now)


def run_demo(runtime: Runtime, duration: float, frame_path: Path | None = None) -> dict:
    if not runtime.simulate:
        raise ValueError("Scripted demo cannot drive real hardware")
    cfg = runtime.config
    start = runtime.controller.piece
    target = Point(min(cfg.board.width_mm-10, start.x+60), min(cfg.board.height_mm-10, start.y+60))
    runtime.start()
    peak_trail_points = 0
    try:
        for tick in range(int(duration*60)):
            now = tick/60
            runtime.tick(scripted_input(now, start, target), (), now)
            peak_trail_points = max(peak_trail_points, len(runtime.compositor.trail))
            if runtime.controller.completed_moves or runtime.controller.phase == Phase.ERROR:
                if frame_path:
                    frame_path.parent.mkdir(parents=True, exist_ok=True)
                    frame_path.write_bytes(b"P6\n64 64\n255\n"+runtime.rgb)
                break
        c = runtime.controller
        return {"simulation": True, "completed_moves": c.completed_moves, "phase": c.phase.value,
                "error": c.error, "piece_mm": [c.piece.x, c.piece.y],
                "target_mm": [target.x, target.y], "motion_commands": runtime.device.move_count,
                "peak_trail_points": peak_trail_points, "virtual_elapsed_s": round(now, 3)}
    finally:
        runtime.close()


def default_config(application: str) -> Path:
    directory = Path(__file__).resolve().parents[2]/"config"
    local = directory/"local-runtime.json"
    return local if application == "gesture-drag" and local.is_file() else directory/"runtime.json"


def configure_logging(config, log_path=None, gesture=False):
    if log_path is None and gesture:
        log_path = config.base_dir.parent/"logs/hand-pointer.log"
    handlers = [logging.StreamHandler()]
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Tabletop OS PC 主程序：系统服务 + 可选应用")
    parser.add_argument("--app", choices=("gesture-drag", "single-piece"),
                        help="默认手势拖动应用；--simulate 自动选择原单棋子应用")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--simulate", action="store_true", help="虚拟机构与独立棋子观测，不连接硬件")
    parser.add_argument("--input", choices=("mouse", "camera"))
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--demo", action="store_true", help="自动双捏合，仅限 headless simulation")
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--frame-output", type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--check-config", action="store_true")
    gesture = parser.add_argument_group("手势拖动应用")
    gesture.add_argument("--reuse-map", action="store_true", help="沿用当前相机机位与灯板选区")
    gesture.add_argument("--rotation", type=int, choices=(0, 180))
    gesture.add_argument("--flip-x", action=argparse.BooleanOptionalAction, default=None)
    gesture.add_argument("--hold-seconds", type=float, default=0, help="标签记忆时间；0 一直保留")
    actuation = gesture.add_mutually_exclusive_group()
    actuation.add_argument("--constant-engage", action="store_true", help="启用持续吸合的磁吸滑台联动")
    actuation.add_argument("--pick-place", action="store_true",
                           help="启用双标定的释放→寻找→吸附→拖动→释放完整流程")
    gesture.add_argument("--allow-pickup-offset", action="store_true", help="本次测试跳过棋子与机构对齐检查")
    gesture.add_argument("--speed", type=float, default=None,
                         help="持续吸合模式速度 mm/s；默认读取配置中的 interaction.speed_mm_s")
    gesture.add_argument("--max-drag-mm", type=float, default=0, help="单次距离上限；0 仅受机械工作区限制")
    args = parser.parse_args(argv)
    args.app = args.app or ("single-piece" if args.simulate or args.headless or args.demo else "gesture-drag")
    args.config = args.config or default_config(args.app)
    args.input = args.input or ("camera" if args.app == "gesture-drag" else "mouse")
    if args.app == "gesture-drag":
        if args.simulate or args.headless or args.demo:
            parser.error("手势拖动应用使用真实摄像头；仿真请选择 --app single-piece")
        if args.input != "camera":
            parser.error("手势拖动应用要求 --input camera")
        import math
        if not ((args.speed is None or math.isfinite(args.speed) and 0.5 <= args.speed <= 50) and
                math.isfinite(args.max_drag_mm) and args.max_drag_mm >= 0 and
                math.isfinite(args.hold_seconds) and args.hold_seconds >= 0):
            parser.error("速度须为 0.5–50 mm/s，距离与记忆时间须为非负有限值")
        if args.pick_place and args.allow_pickup_offset:
            parser.error("完整取放流程会自动寻找棋子，不能使用 --allow-pickup-offset")
    elif (args.constant_engage or args.pick_place or args.allow_pickup_offset or args.reuse_map or
          args.rotation is not None or args.flip_x is not None or args.hold_seconds != 0 or
          args.speed is not None or args.max_drag_mm != 0):
        parser.error("这些灯板/持续吸合选项需要 --app gesture-drag")
    if args.headless != args.demo or (args.demo and not args.simulate):
        parser.error("--headless --demo 必须一起使用，且要求 --simulate")
    if args.simulate and args.input == "camera":
        parser.error("仿真使用鼠标/脚本输入；摄像头验证使用硬件模式并保持运动锁")
    if not 1 <= args.duration <= 3600:
        parser.error("duration 必须为 1–3600 秒")
    if args.app == "single-piece" and not args.simulate and args.input == "mouse" and not args.check_config:
        parser.error("真实动作要求 AprilTag 观测，请使用 --input camera；鼠标仅用于仿真")
    try:
        config = load_config(args.config)
        if args.app == "gesture-drag" and args.speed is None:
            args.speed = config.interaction.speed_mm_s
        configure_logging(config, args.log, args.app == "gesture-drag")
        if args.check_config:
            print("配置有效；舵机标定：", config.gripper.calibrated)
            return 0
        if args.app == "gesture-drag":
            from .gesture_ui import run_gesture_window
            run_gesture_window(config, args)
            return 0
        runtime = Runtime(config, args.simulate, mouse=args.input == "mouse")
        if args.demo:
            result = run_demo(runtime, args.duration, args.frame_output)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["completed_moves"] == 1 else 1
        from .ui import run_window
        run_window(runtime, args.input == "camera")
        return 0
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        logging.error("启动失败：%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
