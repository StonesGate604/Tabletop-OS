"""Operator windows for the gesture application; hardware stays in system services."""
import json
import logging
import time

from .apps.gesture_drag import GestureDragApp
from .calibration import Calibration, from_corners
from .config import BoardConfig
from .system.camera import CameraSession
from .system.session import MotionOptions, TabletopSystem

LOG = logging.getLogger(__name__)


def run_gesture_window(config, options):
    """Run the same calibrated preview/drag workflow through the shared system API."""
    import cv2
    import numpy as np

    motion_options = MotionOptions(
        enabled=options.constant_engage,
        speed_mm_s=options.speed,
        max_drag_mm=options.max_drag_mm or None,
        allow_pickup_offset=options.allow_pickup_offset)
    mechanical = Calibration.load(config.path(config.camera.calibration_path), config.board) if motion_options.enabled else None
    view_path = config.base_dir / 'local-pointer-view.json'
    saved_view = json.loads(view_path.read_text(encoding='utf-8')) if view_path.exists() else {}
    if not isinstance(saved_view, dict):
        raise ValueError('Invalid saved pointer orientation')
    rotation = options.rotation if options.rotation is not None else saved_view.get('rotation', 180)
    flip_x = options.flip_x if options.flip_x is not None else saved_view.get('flip_x', False)
    if type(rotation) is not int or rotation not in (0, 180) or type(flip_x) is not bool:
        raise ValueError('Invalid saved pointer orientation')
    view_path.write_text(json.dumps({'rotation': rotation, 'flip_x': flip_x}, indent=2) + '\n', encoding='utf-8')
    hold_s = options.hold_seconds or None
    app = GestureDragApp(config.interaction)
    camera = CameraSession(config)
    system = None
    mapping = None
    frozen = None
    corners = []
    title = 'Tabletop OS | Gesture app | C cancel | Space STOP | R remap | Esc quit | ' + (
        'CONSTANT MAGNET DRAG' if motion_options.enabled else 'PREVIEW')

    def start_system(selected_mapping):
        service = TabletopSystem(config, selected_mapping, mechanical, motion_options,
                                rotation=rotation, flip_x=flip_x, hold_s=hold_s)
        try:
            app.attach(service)
            service.start()
        except BaseException:
            service.close()
            raise
        return service

    try:
        camera.start()
        frame = camera.read()
        if mechanical is not None:
            camera.validate_calibration(mechanical)
        cv2.namedWindow(title)

        def click(event, x, y, flags, userdata):
            nonlocal frozen
            if event == cv2.EVENT_LBUTTONDOWN and mapping is None and len(corners) < 4:
                if not corners:
                    frozen = frame.copy()
                corners.append((x, y))

        cv2.setMouseCallback(title, click)
        if options.reuse_map:
            mapping = Calibration.load(config.base_dir / 'local-display-map.json',
                                       BoardConfig(width_mm=63, height_mm=63))
            camera.validate_calibration(mapping)
            system = start_system(mapping)
        last_report = 0.0
        while True:
            now = time.monotonic()
            display = (frozen if frozen is not None else frame).copy()
            if mapping is not None:
                sample, observations, mechanical_observations, hand_pixel = camera.analyze(
                    frame, mapping, mechanical, now)
                decision_now = time.monotonic()
                system.update(observations, mechanical_observations, decision_now)
                app.update(sample, observations, system, decision_now)
                scene = app.scene(system, sample.position)
                rgb = system.publish(scene)
                if hand_pixel is not None:
                    cv2.circle(display, hand_pixel, 8, (255, 255, 0), 2)
                inverse = np.linalg.inv(np.asarray(mapping.matrix))
                for tag in system.tracking.display_tags:
                    point = inverse @ np.array([tag.position.x, tag.position.y, 1])
                    x, y = (point[:2] / point[2]).round().astype(int)
                    held = tag.piece_id in system.tracking.cached_ids
                    label = f'ID {tag.piece_id} HOLD {now-tag.timestamp:.1f}s' if held else f'ID {tag.piece_id}'
                    cv2.rectangle(display, (x-12, y-12), (x+12, y+12), (0, 255, 0), 2)
                    cv2.putText(display, label, (x+14, y), cv2.FONT_HERSHEY_SIMPLEX, .55, (0, 255, 0), 1)
                led = np.frombuffer(rgb, dtype=np.uint8).reshape(64, 64, 3)
                cv2.imshow('LED output preview', cv2.resize(cv2.cvtColor(led, cv2.COLOR_RGB2BGR),
                           (384, 384), interpolation=cv2.INTER_NEAREST))
                state = system.snapshot()
                motion = system.motion_view
                status = system.display_status()
                position = sample.position
                inside = position is not None and 0 <= position.x <= 63 and 0 <= position.y <= 63
                hand_status = 'IN LED AREA' if inside else 'OUTSIDE LED AREA' if position else 'NO HAND'
                text = f'Pi: {state.connected} | frame: {status.get("frame_seq", 0)} | {hand_status}'
                tag_ids = [tag.piece_id for tag in system.tracking.visible_tags]
                cv2.putText(display,
                            f'Tags: {tag_ids} | held: {sorted(system.tracking.cached_ids)} | selected: {app.selection.selected_id} | {app.selection.phase}',
                            (10, 44), cv2.FONT_HERSHEY_SIMPLEX, .48, (0, 255, 0), 1)
                instruction = ('Pinch confirms DRAG | keep tag visible | C cancel | Space STOP' if motion.enabled else
                               'Open hand, pinch near tag, release, move, pinch: PREVIEW ONLY | C cancel')
                cv2.putText(display, instruction, (10, 66), cv2.FONT_HERSHEY_SIMPLEX, .43, (0, 255, 0), 1)
                if system.tracking.ambiguous_ids:
                    cv2.putText(display,
                                f'DUPLICATE IDs {sorted(system.tracking.ambiguous_ids)}: use different tags; restart after replacing',
                                (10, 88), cv2.FONT_HERSHEY_SIMPLEX, .45, (255, 200, 0), 1)
                if motion.enabled:
                    cv2.putText(display,
                                f'Motor: {motion.phase} | home: {state.referenced} | mm: {state.position.x:.1f},{state.position.y:.1f}',
                                (10, 110), cv2.FONT_HERSHEY_SIMPLEX, .45, (255, 255, 0), 1)
                    distance_label = f'max {motion_options.max_drag_mm:g} mm' if motion_options.max_drag_mm is not None else 'workspace bounds'
                    cv2.putText(display, app.error or motion.error or
                                f'{motion_options.speed_mm_s:g} mm/s | {distance_label} | no servo commands',
                                (10, 132), cv2.FONT_HERSHEY_SIMPLEX, .45, (255, 255, 0), 1)
                    if motion_options.allow_pickup_offset:
                        cv2.putText(display, 'Test session: pickup alignment check OFF',
                                    (10, 154), cv2.FONT_HERSHEY_SIMPLEX, .45, (255, 255, 0), 1)
                if now - last_report >= 2:
                    LOG.info('%s | pointer=%s | tags=%s held=%s selected=%s phase=%s motor=%s error=%s | display=%s',
                             text, position, tag_ids, sorted(system.tracking.cached_ids), app.selection.selected_id,
                             app.selection.phase, motion.phase, app.error or motion.error, status)
                    last_report = now
            else:
                text = 'Click LED area: top-left, top-right, bottom-right, bottom-left; S starts'
            for index, point in enumerate(corners):
                cv2.circle(display, point, 5, (0, 255, 0), 2)
                cv2.putText(display, str(index+1), point, cv2.FONT_HERSHEY_SIMPLEX, .65, (0, 255, 0), 2)
            if len(corners) == 4:
                cv2.polylines(display, [np.asarray(corners, dtype=np.int32)], True, (0, 180, 0), 1)
            cv2.putText(display, text, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, .45, (0, 255, 0), 1)
            cv2.imshow(title, display)
            key = cv2.waitKey(1) & 255
            if key == 27 or cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                break
            if key == ord('r'):
                if system is not None:
                    system.close()
                    system = None
                app.reset_mapping()
                mapping, frozen = None, None
                corners.clear()
            if key == ord('c') and system is not None:
                app.cancel(system)
            if key == ord(' ') and system is not None:
                app.stop(system)
            if key == ord('s') and len(corners) == 4 and mapping is None:
                try:
                    chosen = from_corners(corners, camera.size, BoardConfig(width_mm=63, height_mm=63))
                    camera.validate_calibration(chosen)
                except ValueError as exc:
                    LOG.warning('Display mapping rejected: %s', exc)
                    corners.clear()
                    frozen = None
                else:
                    chosen.save(config.base_dir / 'local-display-map.json')
                    system = start_system(chosen)
                    mapping, frozen = chosen, None
                    LOG.info('Display mapping ready; %s', 'constant-engagement drags enabled' if motion_options.enabled else 'display preview only')
            frame = camera.read()
    finally:
        try:
            if system is not None:
                system.close()
        finally:
            try:
                camera.close()
            finally:
                cv2.destroyAllWindows()
