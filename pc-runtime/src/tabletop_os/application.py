"""Minimal application lifecycle driven by the owning runtime's tick."""
from dataclasses import dataclass

from .model import Point


@dataclass(frozen=True)
class MotionRequest:
    token: str
    piece_id: str
    target: Point


@dataclass(frozen=True)
class MotionResult:
    token: str
    success: bool
    error: str = ''


class ApplicationHost:
    def __init__(self):
        self.app = None
        self.running = False

    def load(self, app):
        self.unload()
        self.app = app
        app.initialize()

    def start(self):
        if self.app is None:
            raise RuntimeError('NO_APPLICATION_LOADED')
        if not self.running:
            self.app.start()
            self.running = True

    def tick(self, placements, events, motion_result, now):
        if not self.running:
            return ()
        self.app.observe(placements, now)
        if motion_result:
            self.app.motion_finished(motion_result)
        for event in events:
            self.app.handle(event)
        self.app.update(now)
        return self.app.take_requests()

    def pause(self):
        if self.app and self.running:
            self.app.pause()
        self.running = False

    def stop(self):
        if self.app:
            self.app.stop()
        self.running = False

    def unload(self):
        self.stop()
        if self.app:
            self.app.close()
        self.app = None
