"""Patrol view in the existing Tk PC event loop, sharing all runtime decisions."""
import logging
import time
import tkinter as tk
from tkinter import ttk

from .apps.patrol.config import load_patrol
from .apps.patrol.render import cell_colors
from .model import Point

LOG = logging.getLogger(__name__)


def run_patrol_window(runtime, config_path, camera=None):
    root = tk.Tk()
    root.title('Tabletop OS | Patrol | '+runtime.motion.mode.upper())
    root.configure(bg='#10151d')
    canvas = tk.Canvas(root, width=640, height=480, bg='#071019', highlightthickness=0)
    canvas.grid(row=0, column=0, padx=16, pady=16, sticky='n')
    panel = ttk.Frame(root, padding=16)
    panel.grid(row=0, column=1, sticky='n')
    ttk.Label(panel, text='Patrol — A Physical Stealth Game', font=('Arial', 15)).pack(anchor='w')
    instructions = ('Hover near the player to preview legal moves.\nDrag to pick up and hide the tag; release to place.\nHovering or placing it back does not use a turn.'
                    if runtime.simulate else
                    'Bring your hand near the player to preview moves.\nMove the piece by hand, place it, then move away.\nProximity uses a 2D projection onto the table.')
    ttk.Label(panel, text=instructions, wraplength=380, justify='left').pack(anchor='w', pady=10)
    guard_hint = ttk.Label(panel, wraplength=380, justify='left')
    guard_hint.pack(anchor='w', pady=(0, 8))
    ttk.Label(panel, text='Green: legal and currently safe | Orange: in guard vision\nBlue: player | Red: guard | Cyan: objective\nCyan cross with white center: hand position',
              wraplength=380, justify='left').pack(anchor='w')
    status = ttk.Label(panel, width=53, justify='left', wraplength=380)
    status.pack(anchor='w', pady=10)
    local_error = ''
    dragged = None
    closed = False
    hidden = tk.BooleanVar(value=False)
    stalled = tk.BooleanVar(value=False)
    camera_preview = ttk.Label(panel)
    stall_control = None

    def reload_game():
        nonlocal local_error, dragged
        try:
            candidate = load_patrol(config_path, runtime.config)
            runtime.reload(candidate)
            hidden.set(False)
            stalled.set(False)
            dragged = None
            local_error = ''
        except (ValueError, RuntimeError, OSError) as exc:
            local_error = str(exc)

    def stop():
        nonlocal local_error, dragged
        runtime.stop()
        if runtime.simulation:
            runtime.simulation.hidden.clear()
            runtime.simulation.hand = None
        hidden.set(False)
        dragged = None
        local_error = 'Stopped. Restore the pieces, then press R to reload the configuration and restart.'

    ttk.Button(panel, text='Reload configuration / Restart (R)', command=reload_game).pack(fill='x')
    ttk.Button(panel, text='Stop (Space)', command=stop).pack(fill='x', pady=6)
    if runtime.simulate:
        def toggle_hidden():
            key = runtime.host.app.rules.player_id
            if hidden.get():
                runtime.simulation.hidden.add(key)
            else:
                runtime.simulation.hidden.discard(key)
        ttk.Checkbutton(panel, text='Simulate hidden player tag', variable=hidden, command=toggle_hidden).pack(anchor='w')
        stall_control = ttk.Checkbutton(panel, text='Simulate stalled physical guard (wait for timeout)', variable=stalled,
                                        command=lambda: setattr(runtime.simulation, 'stall', stalled.get()))
        stall_control.pack(anchor='w')
    ttk.Label(panel, text='H: home using the existing safety sequence (hardware motion mode only)',
              wraplength=380, justify='left').pack(anchor='w', pady=6)
    led_preview = ttk.Label(panel)
    led_preview.pack(anchor='w')
    camera_preview.pack(anchor='w', pady=8)

    def point(event):
        return Point(event.x/640*runtime.plane.width_mm, event.y/480*runtime.plane.height_mm)

    def hover(event):
        if runtime.simulation:
            runtime.simulation.hand = point(event)

    def press(event):
        nonlocal dragged
        if not runtime.simulation or not runtime.host.running:
            return
        hover(event)
        app = runtime.host.app
        selectable = set(app.actionable)
        if app.phase.value == 'SETUP':
            selectable = set(app.positions)
        if runtime.motion.mode == 'manual' and app.pending:
            selectable.add(app.rules.guard_id)
        selectable.intersection_update(runtime.simulation.positions)
        candidates = [(point(event).distance(runtime.simulation.positions[key]), key) for key in selectable]
        distance, key = min(candidates, default=(float('inf'), None))
        if distance <= runtime.application_config.proximity.proximity_enter_mm:
            dragged = key
            runtime.simulation.hidden.add(key)

    def release(event):
        nonlocal dragged
        if dragged and runtime.simulation:
            # Window-wide binding also permits deliberate out-of-map drops.
            position = Point((event.x_root-canvas.winfo_rootx())/640*runtime.plane.width_mm,
                             (event.y_root-canvas.winfo_rooty())/480*runtime.plane.height_mm)
            runtime.simulation.positions[dragged] = position
            runtime.simulation.hidden.discard(dragged)
            dragged = None
            runtime.simulation.hand = position

    canvas.bind('<Motion>', hover)
    canvas.bind('<B1-Motion>', hover)
    canvas.bind('<ButtonPress-1>', press)
    root.bind('<ButtonRelease-1>', release)
    canvas.bind('<Leave>', lambda _: setattr(runtime.simulation, 'hand', None) if runtime.simulation else None)
    root.bind('<space>', lambda _: stop())
    root.bind('r', lambda _: reload_game())

    def home(_=None):
        nonlocal local_error
        if not runtime.system.options.enabled:
            local_error = 'Hardware motion is disabled. No homing command was sent.'
            return
        if runtime.motion.active or runtime.proximity.busy:
            local_error = 'Stop the current interaction before homing.'
            return
        stop()
        try:
            runtime.system.home(time.monotonic())
            local_error = 'Homing. Wait for IDLE, then reload the game.'
        except (RuntimeError, ValueError, OSError) as exc:
            local_error = str(exc)
    root.bind('h', home)

    def draw():
        app = runtime.host.app
        grid = app.rules.grid
        canvas.delete('all')
        colors = cell_colors(app)
        for (r, c), color in colors.items():
            x0, y0 = c*640/grid.columns, r*480/grid.rows
            x1, y1 = (c+1)*640/grid.columns, (r+1)*480/grid.rows
            canvas.create_rectangle(x0, y0, x1, y1, fill='#%02x%02x%02x' % color, outline='#25313c')
            canvas.create_text(x0+4, y0+4, text=f'{r},{c}', fill='#8493a0', anchor='nw', font=('Arial', 8))
        for cell, label in ((app.rules.objective, 'O' if not app.objective_acquired else '✓'), (app.rules.exit, 'EXIT')):
            r, c = cell
            canvas.create_text((c+.5)*640/grid.columns, (r+.5)*480/grid.rows, text=label, fill='white', font=('Arial', 12))
        for key, cell in app.positions.items():
            center = grid.center(cell)
            x, y = center.x/grid.width_mm*640, center.y/grid.height_mm*480
            canvas.create_text(x, y, text=key+(' '+app.guard_facing if key == app.rules.guard_id else ''),
                               fill='white', font=('Arial', 11, 'bold'))
        observed_now = time.monotonic()
        hold_s = runtime.application_config.proximity.tag_loss_grace_s
        held = {key: tag for key, tag in runtime.registry.last.items()
                if key not in runtime.registry.visible and key not in runtime.registry.ambiguous
                and 0 <= observed_now-tag.timestamp <= hold_s}
        for key, observed in {**held, **runtime.registry.visible}.items():
            p = observed.position
            x, y = p.x/grid.width_mm*640, p.y/grid.height_mm*480
            if key in held:
                canvas.create_oval(x-8, y-8, x+8, y+8, outline='#ffce55', width=2, dash=(3, 2))
                canvas.create_text(x+12, y-12, text=f'{key} HOLD', anchor='sw', fill='#ffce55')
            else:
                canvas.create_oval(x-8, y-8, x+8, y+8, outline='white', width=2)
        hand = runtime.hand_position
        if hand is not None and runtime.plane.contains(hand):
            p = hand
            x, y = p.x/grid.width_mm*640, p.y/grid.height_mm*480
            canvas.create_oval(x-12, y-12, x+12, y+12, outline='#00dfff', width=2)
            canvas.create_line(x-18, y, x+18, y, fill='#00dfff', width=2)
            canvas.create_line(x, y-18, x, y+18, fill='#00dfff', width=2)
            canvas.create_oval(x-3, y-3, x+3, y+3, fill='white', outline='white')
        mapping = '\n'.join(f'{p.logical_id} ← Tag {p.tag_id} ({p.role})'
                            for p in runtime.application_config.bindings.pieces if p.enabled)
        virtual_guard = app.rules.guard_mode == 'virtual'
        if virtual_guard:
            mapping += f'\n{app.rules.guard_id} ← VIRTUAL (no tag)'
        guard_hint.configure(text=('Only the player needs a physical tag.\nThe virtual guard patrols automatically each turn.'
                                   if virtual_guard else 'The guard uses a physical piece.'))
        if stall_control:
            stall_control.configure(state='disabled' if virtual_guard else 'normal')
        missing = sorted(set(runtime.registry.by_id)-set(runtime.registry.visible)-set(held))
        view = runtime.system.motion_view
        hand_status = ('not detected' if hand is None else 'outside board' if not runtime.plane.contains(hand)
                       else f'({hand.x:.0f}, {hand.y:.0f}) mm')
        status.configure(text=f'Mode: {runtime.motion.mode.upper()}\nPhase: {app.phase.value}\n'
                         f'Guard: {app.rules.guard_mode.upper()}\n'
                         f'Turn: {app.turn}  Objective acquired: {"Yes" if app.objective_acquired else "No"}\n'
                         f'Interaction: {runtime.proximity.state.value}\nMotion: {view.phase}/{view.stage}\n'
                         f'Hand: {hand_status}\n'
                         f'Held: {sorted(held)}  Missing: {missing}\n'
                         f'Unregistered: {sorted(runtime.registry.unknown)}\n{mapping}\n'
                         f'{local_error or app.error or view.error}')
        if runtime.rgb:
            image = tk.PhotoImage(data=b'P6\n64 96\n255\n'+runtime.rgb, format='PPM').zoom(2)
            led_preview.image = image
            led_preview.configure(image=image)

    def close():
        nonlocal closed
        closed = True
        try:
            runtime.close()
        finally:
            try:
                if camera:
                    camera.close()
            finally:
                root.destroy()
    root.protocol('WM_DELETE_WINDOW', close)

    def update():
        nonlocal local_error
        if closed:
            return
        try:
            if runtime.simulate:
                runtime.tick_simulation(time.monotonic())
            else:
                frame = camera.read()
                now = time.monotonic()
                sample, observations, mechanical, hand_pixel = camera.analyze(
                    frame, runtime.system.display_mapping, runtime.system.mechanical_mapping, now)
                runtime.tick(sample, observations, mechanical, time.monotonic())
                import cv2
                camera_frame = frame.copy()
                if hand_pixel is not None:
                    cv2.circle(camera_frame, hand_pixel, 10, (255, 255, 0), 2)
                    cv2.circle(camera_frame, hand_pixel, 2, (255, 255, 255), -1)
                rgb = cv2.cvtColor(cv2.resize(camera_frame, (320, round(frame.shape[0]*320/frame.shape[1]))), cv2.COLOR_BGR2RGB)
                image = tk.PhotoImage(data=f'P6\n{rgb.shape[1]} {rgb.shape[0]}\n255\n'.encode()+rgb.tobytes(), format='PPM')
                camera_preview.image = image
                camera_preview.configure(image=image)
            draw()
        except Exception as exc:
            LOG.exception('Application tick failed')
            runtime.stop()
            runtime.host.app.fail('INPUT_OR_RUNTIME_FAILURE: '+str(exc))
            local_error = str(exc)
            status.configure(text=local_error+'\nStopped. Fix the issue and restart the program.')
            return  # no repeated camera failures or automatic motion replay
        root.after(max(1, 1000//runtime.config.render.fps), update)

    try:
        runtime.start()
        root.after(0, update)
        root.mainloop()
    finally:
        if not closed:
            try:
                runtime.close()
            finally:
                if camera:
                    camera.close()
                root.destroy()
