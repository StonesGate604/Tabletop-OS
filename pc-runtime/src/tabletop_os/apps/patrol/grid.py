"""Patrol-only grid and line of sight, expressed over a continuous table plane."""
from dataclasses import dataclass
import math

from ...model import Point

Cell = tuple[int, int]
DIRECTIONS = {'N': (-1, 0), 'E': (0, 1), 'S': (1, 0), 'W': (0, -1)}


@dataclass(frozen=True)
class Grid:
    rows: int
    columns: int
    width_mm: float
    height_mm: float
    obstacles: frozenset[Cell]
    center_tolerance: float = .32
    boundary_hysteresis: float = .06

    def contains(self, cell):
        return 0 <= cell[0] < self.rows and 0 <= cell[1] < self.columns

    def passable(self, cell):
        return self.contains(cell) and cell not in self.obstacles

    def center(self, cell):
        return Point((cell[1]+.5)*self.width_mm/self.columns,
                     (cell[0]+.5)*self.height_mm/self.rows)

    def locate(self, point, previous=None):
        if not point.finite() or not (0 <= point.x < self.width_mm and 0 <= point.y < self.height_mm):
            return None
        x, y = point.x/self.width_mm*self.columns, point.y/self.height_mm*self.rows
        candidate = (math.floor(y), math.floor(x))
        if previous is not None and candidate != previous:
            r, c = previous
            h = self.boundary_hysteresis
            if c-h <= x <= c+1+h and r-h <= y <= r+1+h:
                return None  # no new cell near the old boundary
        if max(abs(x-candidate[1]-.5), abs(y-candidate[0]-.5)) > self.center_tolerance:
            return None
        return candidate

    def neighbours(self, cell):
        return {(cell[0]+dr, cell[1]+dc) for dr, dc in DIRECTIONS.values()
                if self.passable((cell[0]+dr, cell[1]+dc))}

    def line_clear(self, start, target):
        """Supercover ray: touching an obstacle corner also blocks sight."""
        r, c = start
        tr, tc = target
        dr, dc = tr-r, tc-c
        sr, sc = (1 if dr > 0 else -1), (1 if dc > 0 else -1)
        ar, ac = abs(dr), abs(dc)
        ir = ic = 0
        while ir < ar or ic < ac:
            decision = (1+2*ic)*ar-(1+2*ir)*ac
            if decision == 0:
                if (r+sr, c) in self.obstacles or (r, c+sc) in self.obstacles:
                    return False
                r, c, ir, ic = r+sr, c+sc, ir+1, ic+1
            elif decision < 0:
                c, ic = c+sc, ic+1
            else:
                r, ir = r+sr, ir+1
            if (r, c) in self.obstacles:
                return False
        return True

    def vision(self, cell, facing, distance):
        fr, fc = DIRECTIONS[facing]
        result = {cell}
        for r in range(self.rows):
            for c in range(self.columns):
                dr, dc = r-cell[0], c-cell[1]
                forward, lateral = dr*fr+dc*fc, abs(dr*fc-dc*fr)
                if 1 <= forward <= distance and lateral <= forward and self.line_clear(cell, (r, c)):
                    result.add((r, c))
        return frozenset(result)


def facing_between(start, end):
    delta = (end[0]-start[0], end[1]-start[1])
    return next(name for name, vector in DIRECTIONS.items() if vector == delta)
