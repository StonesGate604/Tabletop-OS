"""Deterministic camera-equivalent fixtures; no network or physical devices."""
import json
from pathlib import Path

from tabletop_os.apps.patrol.config import parse_patrol
from tabletop_os.calibration import Calibration
from tabletop_os.config import RuntimeConfig
from tabletop_os.patrol_simulation import LocalDisplayDevice
from tabletop_os.runtime import ApplicationRuntime
from tabletop_os.system.session import TabletopSystem

CONFIG_PATH = Path(__file__).resolve().parents[1]/'config/patrol.json'


def raw_config():
    return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))


def config(raw=None, runtime=None):
    return parse_patrol(raw or raw_config(), runtime or RuntimeConfig())


class Harness:
    def __init__(self, raw=None):
        self.platform = RuntimeConfig()
        self.config = config(raw, self.platform)
        self.device = LocalDisplayDevice()
        mapping = Calibration(64, 96, 63, 95, ((1, 0, 0), (0, 1, 0), (0, 0, 1)))
        self.system = TabletopSystem(self.platform, mapping, device=self.device, rotation=0)
        self.runtime = ApplicationRuntime(self.platform, self.config, self.system, simulate=True)
        self.runtime.start()
        self.now = 0
        self.advance(1)

    @property
    def app(self):
        return self.runtime.host.app

    @property
    def sim(self):
        return self.runtime.simulation

    def advance(self, seconds):
        for _ in range(round(seconds/.05)):
            self.now += .05
            self.runtime.tick_simulation(self.now)

    def hover(self):
        self.sim.hand = self.sim.positions[self.app.rules.player_id]
        self.advance(.4)

    def drop(self, cell, settle=1.8):
        key = self.app.rules.player_id
        self.hover()
        self.sim.hidden.add(key)
        self.advance(.2)
        self.sim.positions[key] = self.app.rules.grid.center(cell)
        self.sim.hidden.discard(key)
        self.sim.hand = None
        self.advance(settle)

    def close(self):
        self.runtime.close()
