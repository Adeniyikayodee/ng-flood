"""KML polygons and a point-in-polygon test. Pure: no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.etree import ElementTree

Ring = tuple[tuple[float, float], ...]
"""Vertices as (lng, lat), the order KML writes them."""


@dataclass(frozen=True, slots=True)
class Polygon:
    outer: Ring
    holes: tuple[Ring, ...] = ()
    box: tuple[float, float, float, float] = field(init=False)
    """West, south, east, north: a cheap test before the ring is walked."""

    def __post_init__(self) -> None:
        xs, ys = zip(*self.outer, strict=True)
        object.__setattr__(self, "box", (min(xs), min(ys), max(xs), max(ys)))


def parse_kml(kml: str) -> tuple[Polygon, ...] | None:
    """Every polygon in a KML document. None when it cannot be read or holds no
    polygon: an area we cannot read is unknown, not empty."""
    try:
        root = ElementTree.fromstring(kml)
    except ElementTree.ParseError:
        return None
    polygons = []
    for node in root.iter():
        if _name(node) != "Polygon":
            continue
        outer = _rings(node, "outerBoundaryIs")
        holes = _rings(node, "innerBoundaryIs")
        if outer is None or holes is None or len(outer) != 1:
            return None
        polygons.append(Polygon(outer[0], tuple(holes)))
    return tuple(polygons) or None


def contains(polygons: tuple[Polygon, ...], lat: float, lng: float) -> bool:
    return any(
        p.box[0] <= lng <= p.box[2]
        and p.box[1] <= lat <= p.box[3]
        and _inside(p.outer, lat, lng)
        and not any(_inside(h, lat, lng) for h in p.holes)
        for p in polygons
    )


def _inside(ring: Ring, lat: float, lng: float) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True):
        if (y1 > lat) != (y2 > lat) and lng < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _name(node: ElementTree.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _rings(polygon: ElementTree.Element, boundary: str) -> list[Ring] | None:
    rings = []
    for edge in polygon:
        if _name(edge) != boundary:
            continue
        text = next((n.text for n in edge.iter() if _name(n) == "coordinates"), None)
        ring = _ring(text or "")
        if ring is None:
            return None
        rings.append(ring)
    return rings


def _ring(text: str) -> Ring | None:
    try:
        ring = tuple((float(x), float(y)) for x, y, *_ in (t.split(",") for t in text.split()))
    except ValueError:
        return None
    return ring if len(ring) >= 3 else None
