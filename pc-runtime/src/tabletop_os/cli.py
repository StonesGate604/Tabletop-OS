"""CLI entry point; deterministic demo is explicitly limited to simulation."""
import argparse
import json
import logging
from pathlib import Path
from .config import load_config
from .frame_protocol import HEIGHT, WIDTH
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
                    frame_path.write_bytes(
                        f"P6\n{WIDTH} {HEIGHT}\n255\n".encode() + runtime.rgb)
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
    return local if application in ("gesture-drag", "patrol") and local.is_file() else directory/"runtime.json"


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
    parser = argparse.ArgumentParser(description="Tabletop OS PC runtime: system services and applications")
    parser.add_argument("--app", choices=("gesture-drag", "single-piece", "patrol"),
                        help="Defaults to gesture-drag; --simulate selects the original single-piece app")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--simulate", action="store_true", help="Use simulated motion and piece observations without connecting hardware")
    parser.add_argument("--input", choices=("mouse", "camera"))
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--demo", action="store_true", help="Run the scripted demo; requires --simulate --headless")
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--frame-output", type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--check-config", action="store_true")
    patrol = parser.add_argument_group('Patrol')
    patrol.add_argument('--patrol-config', type=Path,
                        default=Path(__file__).resolve().parents[2]/'config/patrol.json')
    patrol.add_argument('--hardware-motion', action='store_true', help='Explicitly enable safe Patrol pick-and-place motion; disabled by default')
    patrol.add_argument('--led', action='store_true', help='Connect to the Pi to publish Patrol LED frames without enabling motion')
    patrol.add_argument('--display-map', type=Path, help='Reuse a camera-to-display calibration file')
    gesture = parser.add_argument_group("Gesture-drag application")
    gesture.add_argument("--reuse-map", action="store_true", help="Reuse the current camera position and calibrated display area")
    gesture.add_argument("--rotation", type=int, choices=(0, 180))
    gesture.add_argument("--flip-x", action=argparse.BooleanOptionalAction, default=None)
    gesture.add_argument("--hold-seconds", type=float, default=0, help="Tag memory duration in seconds; 0 keeps positions indefinitely")
    actuation = gesture.add_mutually_exclusive_group()
    actuation.add_argument("--constant-engage", action="store_true", help="Enable motion with the magnet continuously engaged")
    actuation.add_argument("--pick-place", action="store_true",
                           help="Enable the calibrated release, seek, engage, drag and release sequence")
    gesture.add_argument("--allow-pickup-offset", action="store_true", help="Skip the piece-to-actuator alignment check for this test session")
    gesture.add_argument("--speed", type=float, default=None,
                         help="Motion speed in mm/s; gesture-drag uses interaction.speed_mm_s, Patrol defaults to 2")
    gesture.add_argument("--max-drag-mm", type=float, default=0, help="Maximum drag distance in mm; 0 uses only the mechanical workspace limits")
    args = parser.parse_args(argv)
    args.app = args.app or ("single-piece" if args.simulate or args.headless or args.demo else "gesture-drag")
    args.config = args.config or default_config('single-piece' if args.app == 'patrol' and args.simulate else args.app)
    args.input = args.input or ("camera" if args.app in ("gesture-drag", 'patrol') and not args.simulate else "mouse")
    if args.app != 'patrol' and (args.hardware_motion or args.led or args.display_map):
        parser.error('--hardware-motion / --led / --display-map require --app patrol')
    if args.app == "gesture-drag":
        if args.simulate or args.headless or args.demo:
            parser.error("gesture-drag uses a real camera; select --app single-piece for simulation")
        if args.input != "camera":
            parser.error("gesture-drag requires --input camera")
        import math
        if not ((args.speed is None or math.isfinite(args.speed) and 0.5 <= args.speed <= 50) and
                math.isfinite(args.max_drag_mm) and args.max_drag_mm >= 0 and
                math.isfinite(args.hold_seconds) and args.hold_seconds >= 0):
            parser.error("Speed must be 0.5–50 mm/s; distance and memory duration must be finite and nonnegative")
        if args.pick_place and args.allow_pickup_offset:
            parser.error("Pick-and-place automatically seeks the piece and cannot use --allow-pickup-offset")
    elif args.app == 'patrol':
        import math
        if args.constant_engage or args.pick_place or args.allow_pickup_offset or args.reuse_map or args.hold_seconds:
            parser.error('Patrol uses --hardware-motion; safety bypasses and gesture-only options are not supported')
        if args.simulate and (args.hardware_motion or args.led or args.display_map):
            parser.error('Patrol simulation cannot connect to a camera, LEDs or hardware motion')
        if not args.simulate and args.input != 'camera':
            parser.error('Patrol hardware mode requires --input camera')
        if not ((args.speed is None or math.isfinite(args.speed) and .5 <= args.speed <= 50)
                and math.isfinite(args.max_drag_mm) and args.max_drag_mm >= 0):
            parser.error('Speed must be 0.5–50 mm/s; motion distance must be finite and nonnegative')
    elif (args.constant_engage or args.pick_place or args.allow_pickup_offset or args.reuse_map or
          args.rotation is not None or args.flip_x is not None or args.hold_seconds != 0 or
          args.speed is not None or args.max_drag_mm != 0):
        parser.error("These display and motion options require --app gesture-drag")
    if args.headless != args.demo or (args.demo and not args.simulate):
        parser.error("--headless and --demo must be used together and require --simulate")
    if args.simulate and args.input == "camera":
        parser.error("Simulation uses mouse or scripted input; use hardware mode with motion disabled for camera validation")
    if not 1 <= args.duration <= 3600:
        parser.error("duration must be 1–3600 seconds")
    if args.app == "single-piece" and not args.simulate and args.input == "mouse" and not args.check_config:
        parser.error("Hardware motion requires AprilTag observations and --input camera; mouse input is simulation-only")
    try:
        config = load_config(args.config)
        if args.app == "gesture-drag" and args.speed is None:
            args.speed = config.interaction.speed_mm_s
        configure_logging(config, args.log, args.app == "gesture-drag")
        if args.app == 'patrol':
            from .apps.patrol.config import load_patrol
            from .system.pieces import PieceRegistry
            patrol_config = load_patrol(args.patrol_config, config)
            if args.check_config:
                PieceRegistry(patrol_config.bindings, config.camera.april_tag_dictionary).announce()
                print('Patrol configuration valid; hardware motion:', args.hardware_motion,
                      '; guard mode:', patrol_config.rules.guard_mode)
                return 0
            from .patrol_launch import launch_patrol
            return launch_patrol(config, patrol_config, args)
        if args.check_config:
            print("Configuration valid; servo calibrated:", config.gripper.calibrated)
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
        logging.error("Startup failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
