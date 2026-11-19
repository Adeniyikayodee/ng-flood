"""River basins around Nigeria: the basin that holds a point, and the basins whose gauges
speak for it. Pure: reads only its bundled data."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from functools import cache
from importlib.resources import files

from .geometry import Polygon, contains

SOURCE = "HydroBASINS v1c level 8"

Basin = tuple[int, int, tuple[Polygon, ...]]
"""Id, the id it drains to (0 for none), and its outline."""


class Basins:
    def __init__(self, basins: Sequence[Basin]) -> None:
        self._shapes = [(b, shape) for b, _, shape in basins]
        self._down = {b: down for b, down, _ in basins}
        self._up: defaultdict[int, list[int]] = defaultdict(list)
        for b, down, _ in basins:
            self._up[down].append(b)

    def at(self, lat: float, lng: float) -> int | None:
        return next((b for b, shape in self._shapes if contains(shape, lat, lng)), None)

    def reach(self, basin: int | None) -> frozenset[int]:
        """The basin, every basin upstream of it, and the one it drains to, where a
        gauge on the main river often sits."""
        if basin is None:
            return frozenset()
        seen, todo = {basin}, [basin]
        while todo:
            for up in self._up[todo.pop()]:
                if up not in seen:
                    seen.add(up)
                    todo.append(up)
        return frozenset(seen | {self._down.get(basin, 0)} - {0})


@cache
def bundled() -> Basins:
    data = json.loads(files("ng_flood").joinpath("basins.json").read_text())
    scale = data["scale"]
    return Basins(
        [
            (b, down, tuple(Polygon(_ring(ring, scale)) for ring in rings))
            for b, down, rings in data["basins"]
        ]
    )


def _ring(flat: list[int], scale: int) -> tuple[tuple[float, float], ...]:
    return tuple((x / scale, y / scale) for x, y in zip(flat[::2], flat[1::2], strict=True))
