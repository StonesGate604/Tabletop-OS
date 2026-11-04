"""Patrol drawing data and dependency-free HUB75 composition, 64 x 96 RGB."""
from ...frame_protocol import WIDTH, HEIGHT
from .game import GamePhase

COLORS = {
    'base': (0, 6, 10), 'obstacle': (0, 35, 40), 'objective': (0, 140, 140),
    'exit': (0, 45, 160), 'vision': (85, 24, 0), 'safe': (0, 140, 15),
    'danger': (200, 75, 0), 'player': (0, 60, 230), 'guard': (190, 12, 15),
    'neutral': (60, 60, 80), 'invalid': (220, 0, 50), 'target': (110, 100, 0),
    'won': (0, 140, 55), 'lost': (210, 0, 0), 'recovery': (160, 55, 0),
}
HAND_CENTER = (180, 220, 220)
HAND_CROSS = (0, 160, 220)


HARDWARE_COLORS = {**COLORS, 'vision': (0, 18, 45), 'danger': (0, 140, 170),
                   'guard': (0, 170, 130), 'invalid': (0, 30, 180),
                   'target': (0, 90, 90), 'lost': (0, 170, 170), 'recovery': (0, 50, 180)}


def cell_colors(app):
    grid = app.rules.grid
    colors = {(r, c): COLORS['base'] for r in range(grid.rows) for c in range(grid.columns)}
    for cell in grid.obstacles:
        colors[cell] = COLORS['obstacle']
    if not app.objective_acquired:
        colors[app.rules.objective] = COLORS['objective']
    colors[app.rules.exit] = COLORS['exit']
    for cell in app.guard_vision-grid.obstacles:
        colors[cell] = COLORS['vision']
    if app.preview and app.phase == GamePhase.PLAYER_TURN:
        for cell in app.get_legal_moves(app.rules.player_id):
            colors[cell] = COLORS['danger' if cell in app.guard_vision else 'safe']
    if app.pending:
        colors[app.pending[1]] = COLORS['target']
    for key, cell in app.positions.items():
        colors[cell] = COLORS['player' if key == app.rules.player_id else
                              'guard' if key == app.rules.guard_id else 'neutral']
    if app.needs_return:
        colors[app.positions[app.rules.player_id]] = COLORS['invalid']
    return colors


def render_frame(app, rotation=0, flip_x=False, red_available=True, cursor=None):
    colors = cell_colors(app)
    grid = app.rules.grid
    result = bytearray(WIDTH*HEIGHT*3)
    overlay = {GamePhase.GAME_WON: COLORS['won'], GamePhase.GAME_LOST: COLORS['lost'],
               GamePhase.ERROR_RECOVERY: COLORS['recovery']}.get(app.phase)
    fallback = {COLORS[key]: value for key, value in HARDWARE_COLORS.items()}
    hand_pixel = None
    if (cursor is not None and cursor.finite() and
            0 <= cursor.x <= grid.width_mm and 0 <= cursor.y <= grid.height_mm):
        hand_pixel = (round(cursor.x/grid.width_mm*(WIDTH-1)),
                      round(cursor.y/grid.height_mm*(HEIGHT-1)))
    for y in range(HEIGHT):
        for x in range(WIDTH):
            row, column = min(grid.rows-1, y*grid.rows//HEIGHT), min(grid.columns-1, x*grid.columns//WIDTH)
            color = colors[(row, column)]
            if x == column*WIDTH//grid.columns or y == row*HEIGHT//grid.rows:
                color = (0, 0, 0)
            # Small landmarks remain readable under vision/preview overlays.
            cell = (row, column)
            cx, cy = int((column+.5)*WIDTH/grid.columns), int((row+.5)*HEIGHT/grid.rows)
            if abs(x-cx) <= 1 and y == cy and cell not in app.positions.values():
                if cell == app.rules.objective and not app.objective_acquired:
                    color = COLORS['objective']
                elif cell == app.rules.exit:
                    color = COLORS['exit']
            # Draw the live hand after map/preview layers, in the same calibrated
            # plane and before output orientation. This is feedback only.
            if hand_pixel:
                dx, dy = x-hand_pixel[0], y-hand_pixel[1]
                if (dy == 0 and abs(dx) <= 2) or (dx == 0 and abs(dy) <= 2):
                    color = HAND_CENTER if dx == dy == 0 else HAND_CROSS
            if overlay and (x < 2 or y < 2 or x >= WIDTH-2 or y >= HEIGHT-2):
                color = overlay
            if not red_available:
                color = fallback.get(color, color)
                color = (0, color[1], color[2])
            ox, oy = (WIDTH-1-x if flip_x else x), y
            if rotation == 180:
                ox, oy = WIDTH-1-ox, HEIGHT-1-oy
            offset = (oy*WIDTH+ox)*3
            result[offset:offset+3] = bytes(color)
    return bytes(result)
