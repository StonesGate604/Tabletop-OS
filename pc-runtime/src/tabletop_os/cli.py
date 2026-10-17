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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Tabletop OS V0.1 单棋子运行时")
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[2]/"config/runtime.json")
    parser.add_argument("--simulate", action="store_true", help="虚拟机构与独立棋子观测，不连接硬件")
    parser.add_argument("--input", choices=("mouse", "camera"), default="mouse")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--demo", action="store_true", help="自动双捏合，仅限 headless simulation")
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--frame-output", type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args(argv)
    if args.headless != args.demo or (args.demo and not args.simulate):
        parser.error("--headless --demo 必须一起使用，且要求 --simulate")
    if args.simulate and args.input == "camera":
        parser.error("仿真使用鼠标/脚本输入；摄像头验证使用硬件模式并保持运动锁")
    if not 1 <= args.duration <= 3600:
        parser.error("duration 必须为 1–3600 秒")
    if not args.simulate and args.input == "mouse" and not args.check_config:
        parser.error("真实动作要求 AprilTag 观测，请使用 --input camera；鼠标仅用于仿真")
    handlers = [logging.StreamHandler()]
    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(args.log, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, handlers=handlers, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        config = load_config(args.config)
        if args.check_config:
            print("配置有效；舵机标定：", config.gripper.calibrated)
            return 0
        runtime = Runtime(config, args.simulate, mouse=args.input == "mouse")
        if args.demo:
            result = run_demo(runtime, args.duration, args.frame_output)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["completed_moves"] == 1 else 1
        from .ui import run_window
        run_window(runtime, args.input == "camera")
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        logging.error("启动失败：%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
