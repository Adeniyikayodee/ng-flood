"""Named areas that hold a point: Nigeria's states and LGAs, bundled, or any areas in a
GeoJSON file. Pure."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any, Generic, TypeVar

from .geometry import Polygon, Ring, contains, scaled

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


def read_geojson(text: str, name: str) -> Areas[str] | None:
    """Every polygon in a FeatureCollection, labelled by its `name` property. None when
    the text is not one, or no polygon has the property."""
    try:
        features = json.loads(text)["features"]
        areas = [
            (str(f["properties"][name]), _polygons(f["geometry"]))
            for f in features
            if (f.get("properties") or {}).get(name) is not None
            and (f.get("geometry") or {}).get("type") in ("Polygon", "MultiPolygon")
        ]
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None
    return Areas(areas) if areas else None


def _polygons(geometry: dict[str, Any]) -> tuple[Polygon, ...]:
    coordinates = geometry["coordinates"]
    parts = [coordinates] if geometry["type"] == "Polygon" else coordinates
    return tuple(Polygon(_ring(outer), tuple(map(_ring, holes))) for outer, *holes in parts)


def _ring(points: list[list[float]]) -> Ring:
    ring = tuple((float(p[0]), float(p[1])) for p in points)
    if len(ring) < 3:
        raise ValueError(ring)
    return ring


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
