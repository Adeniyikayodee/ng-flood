"""Named areas that hold a point: Nigeria's states and LGAs, bundled. Pure."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Generic, TypeVar

from .geometry import Polygon, contains, scaled

T = TypeVar("T")

SOURCE = "OCHA COD-AB Nigeria"


@dataclass(frozen=True, slots=True)
class Lga:
    state: str
    name: str
    pcode: str


class Areas(Generic[T]):
    def __init__(self, areas: Sequence[tuple[T, tuple[Polygon, ...]]]) -> None:
        self._areas = areas

    def at(self, lat: float, lng: float) -> T | None:
        return next((label for label, shape in self._areas if contains(shape, lat, lng)), None)


@cache
def lgas() -> Areas[Lga]:
    data = json.loads(files("ng_flood").joinpath("areas.json").read_text(encoding="utf-8"))
    scale = data["scale"]
    return Areas(
        [
            (Lga(state, name, pcode), tuple(Polygon(scaled(r, scale)) for r in rings))
            for state, name, pcode, rings in data["lgas"]
        ]
    )
