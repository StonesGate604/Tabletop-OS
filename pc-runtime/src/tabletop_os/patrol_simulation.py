"""Equivalent camera samples for simulation; all decisions remain in shared services."""
from .model import DeviceSnapshot, InputSample, Observation


class LocalDisplayDevice:
    """No socket, motor, firmware or simulated hardware-success messages."""
    def __init__(self):
        self.rgb = b''
        self.started = self.closed = False

    def start(self):
        self.started = True

    def close(self):
        self.closed = True

    def publish(self, rgb):
        self.rgb = rgb

    def poll(self):
        return []

    def snapshot(self):
        return DeviceSnapshot()

    def display_status(self):
        return {'mode': 'local'}

    def request(self, *args, **kwargs):
        raise RuntimeError('LOCAL_MODE_CANNOT_SEND_HARDWARE_COMMANDS')


class SimulatedPieces:
    def __init__(self, config, plane):
        self.config, self.plane = config, plane
        physical_ids = {p.logical_id for p in config.bindings.pieces if p.enabled}
        self.positions = {key: config.rules.grid.center(cell) for key, cell in config.rules.initial_positions
                          if key in physical_ids}
        self.hidden = set()
        self.pending = None
        self.stall = False
        self.hand = None

    def schedule(self, logical_id, position, now):
        self.pending = (logical_id, position, now+.5)

    def cancel(self):
        self.pending = None

    def samples(self, now):
        if self.pending and not self.stall and now >= self.pending[2]:
            key, target, _ = self.pending
            self.positions[key] = target
            self.pending = None
        observations = tuple(Observation(p.tag_id, self.plane.display(self.positions[p.logical_id]), now)
                             for p in self.config.bindings.pieces if p.enabled and p.logical_id not in self.hidden)
        hand = self.plane.display(self.hand) if self.hand is not None else None
        return InputSample(hand, None, now), observations


def run_patrol_demo(runtime, duration=60, frame_path=None):
    """Drive hover, occlusion, release and a rule-planned winning path end to end."""
    if not runtime.simulate:
        raise ValueError('Patrol scripted demo requires simulation')
    from collections import deque
    from copy import deepcopy
    from .application import MotionResult
    from .apps.patrol.game import GamePhase
    from .system.proximity import InteractionEvent

    runtime.start()
    now = 0.0
    dt = .05

    def advance(seconds):
        nonlocal now
        for _ in range(round(seconds/dt)):
            now += dt
            runtime.tick_simulation(now)

    try:
        advance(1)
        app = runtime.host.app
        if app.phase != GamePhase.PLAYER_TURN:
            raise RuntimeError('Simulation setup did not stabilize')
        key = app.rules.player_id
        runtime.simulation.hand = runtime.simulation.positions[key]
        advance(.5)
        preview_verified = app.preview and app.turn == 0
        runtime.simulation.hidden.add(key)
        advance(.35)
        occlusion_verified = app.preview and app.turn == 0
        runtime.simulation.hidden.discard(key)
        runtime.simulation.hand = None
        advance(1)
        cancellation_verified = not app.preview and app.turn == 0

        # Search the same pure rules so custom maps use the same settlement rules.
        queue = deque([(deepcopy(app), [])])
        seen = set()
        route = None
        while queue and len(seen) < 30000:
            game, path = queue.popleft()
            signature = (game.positions[key], game.path_index, game.path_direction,
                         game.guard_facing, game.objective_acquired)
            if signature in seen:
                continue
            seen.add(signature)
            if game.phase == GamePhase.GAME_WON:
                route = path
                break
            if game.phase != GamePhase.PLAYER_TURN:
                continue
            for cell in sorted(game.get_legal_moves(key)):
                trial = deepcopy(game)
                trial.handle(InteractionEvent('piece_position_stabilized', key, trial.rules.grid.center(cell)))
                while trial.phase == GamePhase.GUARD_MOVING:
                    request = trial.take_requests()[0]
                    trial.motion_finished(MotionResult(request.token, True))
                queue.append((trial, path+[cell]))
        if route is None:
            raise ValueError('No winning route found for the configured map within the demo search limit')
        for cell in route:
            if now+2 > duration:
                break
            runtime.simulation.hand = runtime.simulation.positions[key]
            advance(.25)
            runtime.simulation.hidden.add(key)
            advance(.2)
            runtime.simulation.positions[key] = app.rules.grid.center(cell)
            runtime.simulation.hidden.discard(key)
            runtime.simulation.hand = None
            advance(1.7)
        if frame_path:
            frame_path.parent.mkdir(parents=True, exist_ok=True)
            frame_path.write_bytes(b'P6\n64 96\n255\n'+runtime.rgb)
        return {'simulation': True, 'guard_mode': runtime.motion.mode, 'phase': app.phase.value,
                'turns': app.turn, 'objective_acquired': app.objective_acquired,
                'hover_preview_verified': preview_verified, 'occlusion_verified': occlusion_verified,
                'cancellation_verified': cancellation_verified, 'guard_requests': runtime.motion.request_count,
                'logical_positions': app.positions, 'error': app.error, 'virtual_elapsed_s': round(now, 2)}
    finally:
        runtime.close()
