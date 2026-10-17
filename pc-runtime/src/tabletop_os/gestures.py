"""Pinch hysteresis and debounce independent of the vision implementation."""
import math


class PinchDetector:
    def __init__(self, on: float, off: float, debounce_ms: int):
        if not 0 < on < off:
            raise ValueError("Pinch thresholds require 0 < on < off")
        self.on, self.off, self.debounce = on, off, debounce_ms / 1000
        self.pinched = False
        self.armed = False
        self.candidate = None
        self.since = 0.0

    def update(self, ratio: float | None, now: float) -> str | None:
        if ratio is None or not math.isfinite(ratio) or ratio < 0:
            self.pinched = self.armed = False
            self.candidate = None
            return "lost"
        desired = (ratio < self.off) if self.pinched else (ratio <= self.on)
        if not self.armed:
            desired = False
            if ratio < self.off:
                self.candidate = None
                return None
        if desired == self.pinched and self.armed:
            self.candidate = None
            return None
        if self.candidate != desired:
            self.candidate, self.since = desired, now
        if now - self.since + 1e-9 < self.debounce:
            return None
        self.candidate = None
        if not self.armed:
            self.armed = True
            return None
        self.pinched = desired
        return "begin" if desired else "end"
