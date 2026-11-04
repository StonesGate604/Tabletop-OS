"""Patrol startup policy; all hardware access remains explicit and reusable."""
import json

from .calibration import Calibration
from .config import BoardConfig
from .patrol_simulation import LocalDisplayDevice, run_patrol_demo
from .runtime import ApplicationRuntime
from .system.session import MotionOptions, TabletopSystem


def launch_patrol(config, patrol_config, args):
    if args.simulate and (args.hardware_motion or args.led):
        raise ValueError('Simulation cannot connect to hardware')
    if patrol_config.rules.guard_mode == 'virtual' and args.hardware_motion:
        raise ValueError('Virtual guard cannot use --hardware-motion; use --led for the physical board')
    camera = None
    system = None
    runtime = None
    try:
        if args.simulate:
            mapping = Calibration(64, 96, 63, 95, ((1, 0, 0), (0, 1, 0), (0, 0, 1)))
            mechanical = None
            rotation, flip_x = args.rotation or 0, bool(args.flip_x)
        else:
            mapping = Calibration.load(args.display_map or config.base_dir/'local-display-map.json',
                                       BoardConfig(width_mm=63, height_mm=95))
            mechanical = (Calibration.load(config.path(config.camera.calibration_path), config.board)
                          if args.hardware_motion else None)
            view_path = config.base_dir/'local-pointer-view.json'
            saved = json.loads(view_path.read_text(encoding='utf-8')) if view_path.exists() else {}
            if not isinstance(saved, dict):
                raise ValueError('Invalid saved display orientation')
            rotation = args.rotation if args.rotation is not None else saved.get('rotation', 180)
            flip_x = args.flip_x if args.flip_x is not None else saved.get('flip_x', False)
            from .system.camera import CameraSession
            camera = CameraSession(config)
            camera.start()
            camera.validate_calibration(mapping)
            if mechanical:
                camera.validate_calibration(mechanical)
        options = MotionOptions(enabled=args.hardware_motion, pick_place=args.hardware_motion,
                                speed_mm_s=args.speed or 2, max_drag_mm=args.max_drag_mm or None)
        # Camera-only and simulation never construct a NetworkDevice.
        device = None if args.led or args.hardware_motion else LocalDisplayDevice()
        system = TabletopSystem(config, mapping, mechanical, options, device,
                                rotation=rotation, flip_x=flip_x)
        runtime = ApplicationRuntime(config, patrol_config, system, args.simulate)
        if args.demo:
            result = run_patrol_demo(runtime, args.duration, args.frame_output)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if (result['phase'] == 'GAME_WON' and result['hover_preview_verified']
                         and result['occlusion_verified'] and result['cancellation_verified']) else 1
        from .patrol_ui import run_patrol_window
        run_patrol_window(runtime, args.patrol_config, camera)
        return 0
    finally:
        try:
            if runtime:
                runtime.close()
            elif system:
                system.close()
        finally:
            if camera:
                camera.close()
