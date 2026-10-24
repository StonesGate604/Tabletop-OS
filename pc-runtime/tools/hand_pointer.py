"""Combined hand/AprilTag display preview, independent of mechanical coordinates."""
import argparse
import json
import logging
import math
import time
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np

from tabletop_os.calibration import from_corners
from tabletop_os.calibration import Calibration
from tabletop_os.constant_engage import ConstantEngageDrag
from tabletop_os.config import BoardConfig, load_config
from tabletop_os.device import NetworkDevice
from tabletop_os.model import InputSample
from tabletop_os.preview import SelectionPreview, in_display
from tabletop_os.vision import AprilTagDetector, crop_frame, hand_sample, read_camera_frame


def project_between(position, source, destination):
    """Reproject through camera pixels; LED output orientation is not involved."""
    pixel = np.linalg.solve(np.asarray(source.matrix),np.array([position.x,position.y,1.0]))
    if abs(pixel[2]) < 1e-12:
        raise ValueError('Point maps to camera infinity')
    return destination.map(float(pixel[0]/pixel[2]),float(pixel[1]/pixel[2]))


def pointer_frame(position, rotation=180, flip_x=False, tags=(), selected_id=None, target=None, confirmed=False, cached_ids=()):
    rgb = np.zeros((64, 64, 3), dtype=np.uint8)
    def pixel(point):
        return round(point.x), round(point.y)
    selected = next((tag for tag in tags if tag.piece_id == selected_id),None)
    if selected and in_display(target):
        cv2.line(rgb,pixel(selected.position),pixel(target),(0,20,100),1)
        cv2.circle(rgb,pixel(target),3,(0,80 if confirmed else 20,255),1)
    for tag in tags:
        if in_display(tag.position):
            chosen = tag.piece_id == selected_id
            x,y = pixel(tag.position)
            radius = 4 if chosen else 2
            green = 255 if chosen else 140
            cv2.rectangle(rgb,(x-radius,y-radius),(x+radius,y+radius),(0,green,40),1)
    if in_display(position):
        p = pixel(position)
        cv2.circle(rgb, p, 2, (0, 180, 255), 1)
        cv2.circle(rgb, p, 0, (0, 255, 255), -1)
    if flip_x:
        rgb = rgb[:,::-1]
    if rotation == 180:
        rgb = rgb[::-1, ::-1]
    return rgb.tobytes()


def main():
    parser = argparse.ArgumentParser(description="Hand + AprilTag preview: select LED area TL TR BR BL; S starts, C clears selection, R resets, Esc quits")
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1]/'config/local-runtime.json')
    parser.add_argument('--rotation', type=int, choices=(0, 180), default=None)
    parser.add_argument('--flip-x', action=argparse.BooleanOptionalAction, default=None, help='Mirror only pointer X; saved locally')
    parser.add_argument('--reuse-map', action='store_true', help='Reuse the last mapping only if camera, crop and LED area are unchanged')
    parser.add_argument('--hold-seconds', type=float, default=0, help='Last seen tag retention; 0 keeps until refreshed (default)')
    parser.add_argument('--constant-engage', action='store_true', help='Allow motor drags with magnet physically engaged; no servo or automatic pickup')
    parser.add_argument('--allow-pickup-offset', action='store_true', help='Temporarily skip tag-to-actuator alignment check for this test session')
    parser.add_argument('--speed', type=float, default=2, help='Constant-engagement drag speed in mm/s')
    parser.add_argument('--max-drag-mm', type=float, default=0, help='Optional single drag distance cap; 0 uses workspace bounds only (default)')
    args = parser.parse_args()
    if not math.isfinite(args.speed) or not 0.5 <= args.speed <= 25 or not math.isfinite(args.max_drag_mm) or args.max_drag_mm < 0:
        parser.error('Speed must be 0.5-25 mm/s and maximum drag distance must be nonnegative and finite')
    max_drag_mm = args.max_drag_mm or None
    config = load_config(args.config)
    selection = SelectionPreview(config.interaction,hold_s=None if args.hold_seconds == 0 else args.hold_seconds)
    mechanical = Calibration.load(config.path(config.camera.calibration_path),config.board) if args.constant_engage else None
    detector = AprilTagDetector(config.camera.april_tag_dictionary,config.camera.marker_min_area_px)
    view_path = config.base_dir/'local-pointer-view.json'
    view = json.loads(view_path.read_text(encoding='utf-8')) if view_path.exists() else {}
    rotation = args.rotation if args.rotation is not None else view.get('rotation',180)
    flip_x = args.flip_x if args.flip_x is not None else view.get('flip_x',False)
    if rotation not in (0,180) or type(flip_x) is not bool:
        raise ValueError('Invalid saved pointer orientation')
    view_path.write_text(json.dumps({'rotation':rotation,'flip_x':flip_x},indent=2)+'\n',encoding='utf-8')
    log_path = config.base_dir.parent/'logs/hand-pointer.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(), logging.FileHandler(log_path, encoding='utf-8')])
    capture = device = landmarker = mover = None
    motion_error = ''
    title = 'Hands + AprilTag | C cancel | Space STOP | Esc quit | '+('CONSTANT MAGNET DRAG' if args.constant_engage else 'NO MOTION')
    corners = []
    mapping = None
    frozen = None
    try:
        capture = cv2.VideoCapture(config.camera.index)
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, config.camera.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, config.camera.height)
        frame = crop_frame(read_camera_frame(capture), config.camera)
        size = (frame.shape[1], frame.shape[0])
        if mechanical and (mechanical.width,mechanical.height) != size:
            raise ValueError('Mechanical calibration differs from camera crop; recalibrate before motion')
        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(config.path(config.camera.model_path))),
            running_mode=mp.tasks.vision.RunningMode.VIDEO, num_hands=1,
            min_hand_detection_confidence=config.camera.min_confidence,
            min_hand_presence_confidence=config.camera.min_confidence,
            min_tracking_confidence=config.camera.min_confidence)
        landmarker = mp.tasks.vision.HandLandmarker.create_from_options(options)
        cv2.namedWindow(title)

        def click(event, x, y, flags, userdata):
            nonlocal frozen
            if event == cv2.EVENT_LBUTTONDOWN and mapping is None and len(corners) < 4:
                if not corners:
                    frozen = frame.copy()
                corners.append((x, y))

        cv2.setMouseCallback(title, click)
        if args.reuse_map:
            mapping = Calibration.load(config.base_dir/'local-display-map.json', BoardConfig(width_mm=63,height_mm=63))
            if (mapping.width,mapping.height) != size:
                raise ValueError('Saved display map size differs; select corners again')
            device = NetworkDevice(config.network)
            if args.constant_engage:
                mover = ConstantEngageDrag(config,device,args.speed,max_drag_mm,allow_pickup_offset=args.allow_pickup_offset)
            device.start()
        stamp = -1
        last_report = 0
        while True:
            now = time.monotonic()
            display = (frozen if frozen is not None else frame).copy()
            position = None
            if mapping is not None:
                observations = detector.detect(frame,mapping,now)
                events = device.poll()
                motion_observations = detector.detect(frame,mechanical,now) if mover else ()
                sample = InputSample(None,None,now)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                stamp = max(stamp+1, int(now*1000))
                result = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), stamp)
                if result.hand_landmarks:
                    hand = result.hand_landmarks[0]
                    sample = hand_sample(hand, size[0], size[1], mapping, now)
                    position = sample.position
                    pixel = (round((hand[4].x+hand[8].x)*size[0]/2), round((hand[4].y+hand[8].y)*size[1]/2))
                    cv2.circle(display, pixel, 8, (255,255,0), 2)
                previous_phase = selection.phase
                selection.update(sample,observations,now)
                if mover:
                    mover.update(events,motion_observations,time.monotonic())
                if mover and selection.phase == 'CONFIRMED_PREVIEW' and previous_phase != 'CONFIRMED_PREVIEW':
                    if mover.phase != 'IDLE':
                        motion_error = 'CANCEL_OR_RESET_WITH_C_FIRST'
                    else:
                        candidates = [tag for tag in motion_observations if tag.piece_id == selection.selected_id]
                        try:
                            if len(candidates) != 1:
                                raise RuntimeError('UNCOVER_SELECTED_TAG_AND_CONFIRM_AGAIN')
                            target = project_between(selection.target,mapping,mechanical)
                            mover.begin(candidates[0],target,time.monotonic())
                            motion_error = ''
                            logging.info('Drag submitted: tag=%s target_mm=%s',selection.selected_id,target)
                        except (ValueError,RuntimeError,OSError) as exc:
                            motion_error = str(exc)
                            if motion_error == 'PICKUP_POSITION_MISMATCH' and len(candidates) == 1:
                                reported = device.snapshot().position
                                seen = candidates[0].position
                                motion_error += f' motor=({reported.x:.1f},{reported.y:.1f}) tag=({seen.x:.1f},{seen.y:.1f}) mm'
                            logging.warning('Drag blocked: %s',motion_error)
                            selection.phase = 'AIMING'
                inverse = np.linalg.inv(np.asarray(mapping.matrix))
                for tag in selection.display_tags:
                    point = inverse @ np.array([tag.position.x,tag.position.y,1])
                    x,y = (point[:2]/point[2]).round().astype(int)
                    cached = tag.piece_id in selection.cached_ids
                    color = (0,255,0)
                    label = f'ID {tag.piece_id} HOLD {now-tag.timestamp:.1f}s' if cached else f'ID {tag.piece_id}'
                    cv2.rectangle(display,(x-12,y-12),(x+12,y+12),color,2)
                    cv2.putText(display,label,(x+14,y),cv2.FONT_HERSHEY_SIMPLEX,.55,color,1)
                selected_id, display_target = selection.selected_id, selection.target
                confirmed = selection.phase == 'CONFIRMED_PREVIEW'
                if mover and mover.phase in ('MOVING','VERIFYING','DONE'):
                    selected_id = mover.selected_id
                    display_target = project_between(mover.target,mechanical,mapping)
                    confirmed = True
                panel_rgb = pointer_frame(position, rotation, flip_x, selection.display_tags,
                                          selected_id,display_target,confirmed,selection.cached_ids)
                device.publish(panel_rgb)
                panel_preview = np.frombuffer(panel_rgb,dtype=np.uint8).reshape(64,64,3)
                cv2.imshow('LED output preview', cv2.resize(cv2.cvtColor(panel_preview,cv2.COLOR_RGB2BGR),(384,384),interpolation=cv2.INTER_NEAREST))
                status = device.display_status()
                inside = position is not None and 0 <= position.x <= 63 and 0 <= position.y <= 63
                hand_status = 'IN LED AREA' if inside else 'OUTSIDE LED AREA' if position else 'NO HAND'
                text = f"Pi: {device.snapshot().connected} | frame: {status.get('frame_seq', 0)} | {hand_status}"
                tag_ids = [tag.piece_id for tag in selection.visible_tags]
                cv2.putText(display,f'Tags: {tag_ids} | held: {sorted(selection.cached_ids)} | selected: {selection.selected_id} | {selection.phase}',
                            (10,44),cv2.FONT_HERSHEY_SIMPLEX,.48,(0,255,0),1)
                instruction = ('Pinch confirms DRAG | keep tag visible | C cancel | Space STOP' if mover else
                               'Open hand, pinch near tag, release, move, pinch: PREVIEW ONLY | C cancel')
                cv2.putText(display,instruction,
                            (10,66),cv2.FONT_HERSHEY_SIMPLEX,.43,(0,255,0),1)
                if selection.ambiguous_ids:
                    cv2.putText(display,f'DUPLICATE IDs {sorted(selection.ambiguous_ids)}: use different tags; restart after replacing',
                                (10,88),cv2.FONT_HERSHEY_SIMPLEX,.45,(255,200,0),1)
                if mover:
                    state = device.snapshot()
                    cv2.putText(display,f'Motor: {mover.phase} | home: {state.referenced} | mm: {state.position.x:.1f},{state.position.y:.1f}',
                                (10,110),cv2.FONT_HERSHEY_SIMPLEX,.45,(255,255,0),1)
                    distance_label = f'max {max_drag_mm:g} mm' if max_drag_mm is not None else 'workspace bounds'
                    cv2.putText(display,motion_error or mover.error or f'{args.speed:g} mm/s | {distance_label} | no servo commands',
                                (10,132),cv2.FONT_HERSHEY_SIMPLEX,.45,(255,255,0),1)
                    if args.allow_pickup_offset:
                        cv2.putText(display,'Test session: pickup alignment check OFF',
                                    (10,154),cv2.FONT_HERSHEY_SIMPLEX,.45,(255,255,0),1)
                if now-last_report >= 2:
                    logging.info('%s | pointer=%s | tags=%s held=%s selected=%s phase=%s motor=%s error=%s | display=%s',
                                 text,position,tag_ids,sorted(selection.cached_ids),selection.selected_id,selection.phase,
                                 mover.phase if mover else 'DISABLED',motion_error or (mover.error if mover else ''),status)
                    last_report = now
            else:
                text = 'Click LED area: top-left, top-right, bottom-right, bottom-left; S starts'
            for i, point in enumerate(corners):
                cv2.circle(display, point, 5, (0,255,0), 2)
                cv2.putText(display, str(i+1), point, cv2.FONT_HERSHEY_SIMPLEX, .65, (0,255,0), 2)
            if len(corners) == 4:
                cv2.polylines(display, [np.array(corners, dtype=np.int32)], True, (0,180,0), 1)
            cv2.putText(display, text, (10,22), cv2.FONT_HERSHEY_SIMPLEX, .45, (0,255,0), 1)
            cv2.imshow(title, display)
            key = cv2.waitKey(1) & 255
            if key == 27 or cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                break
            if key == ord('r'):
                if device:
                    if mover:
                        mover.cancel('REMAP')
                    device.publish(bytes(64*64*3))
                    device.close()
                    device = None
                    mover = None
                mapping, frozen = None, None
                corners.clear()
                selection.clear()
                selection.tags.clear()
                selection.pinch.update(None,now)
            if key == ord('c'):
                if mover:
                    if mover.active:
                        mover.cancel('USER_CANCEL')
                    else:
                        mover.reset()
                    motion_error = ''
                selection.clear()
                selection.pinch.update(None,now)
            if key == ord(' ') and mover:
                if mover.active:
                    mover.cancel('USER_STOP')
                else:
                    mover.phase, mover.error = 'ERROR','USER_STOP'
                    try:
                        device.request('motion.stop')
                    except RuntimeError:
                        pass
                selection.clear()
                selection.pinch.update(None,now)
            if key == ord('s') and len(corners) == 4 and mapping is None:
                try:
                    mapping = from_corners(corners, size, BoardConfig(width_mm=63, height_mm=63))
                except ValueError as exc:
                    print(exc, flush=True)
                    corners.clear()
                    frozen = None
                else:
                    mapping.save(config.base_dir/'local-display-map.json')
                    frozen = None
                    device = NetworkDevice(config.network)
                    if args.constant_engage:
                        mover = ConstantEngageDrag(config,device,args.speed,max_drag_mm,allow_pickup_offset=args.allow_pickup_offset)
                    device.start()
                    print('Display mapping ready; '+('constant-engagement drags enabled.' if mover else 'display preview only.'), flush=True)
            ok, raw = capture.read()
            if not ok:
                raise RuntimeError('Camera read failed')
            frame = crop_frame(raw, config.camera)
            if (frame.shape[1],frame.shape[0]) != size:
                raise RuntimeError('Camera size changed; restart mapping')
    finally:
        if device:
            if mover:
                mover.cancel('WINDOW_CLOSED')
            device.publish(bytes(64*64*3))
            device.close()
        if landmarker:
            landmarker.close()
        if capture:
            capture.release()
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
