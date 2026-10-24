"""Shared LED primitives; no device access or application decisions."""
from .tracking import in_display


def pointer_frame(position, rotation=180, flip_x=False, tags=(), selected_id=None,
                  target=None, confirmed=False, cached_ids=()):
    """Compose hand, tag and target feedback; remembered tags keep their brightness."""
    import cv2
    import numpy as np

    rgb = np.zeros((64, 64, 3), dtype=np.uint8)

    def pixel(point):
        return round(point.x), round(point.y)

    selected = next((tag for tag in tags if tag.piece_id == selected_id), None)
    if selected and in_display(target):
        cv2.line(rgb, pixel(selected.position), pixel(target), (0, 20, 100), 1)
        cv2.circle(rgb, pixel(target), 3, (0, 80 if confirmed else 20, 255), 1)
    for tag in tags:
        if in_display(tag.position):
            chosen = tag.piece_id == selected_id
            x, y = pixel(tag.position)
            radius = 4 if chosen else 2
            green = 255 if chosen else 140
            cv2.rectangle(rgb, (x-radius, y-radius), (x+radius, y+radius), (0, green, 40), 1)
    if in_display(position):
        p = pixel(position)
        cv2.circle(rgb, p, 2, (0, 180, 255), 1)
        cv2.circle(rgb, p, 0, (0, 255, 255), -1)
    if flip_x:
        rgb = rgb[:, ::-1]
    if rotation == 180:
        rgb = rgb[::-1, ::-1]
    return rgb.tobytes()
