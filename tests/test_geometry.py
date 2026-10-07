from __future__ import annotations

from ng_flood.geometry import contains, parse_kml

SQUARE = "6,7 8,7 8,9 6,9 6,7"
HOLE = "6.5,7.5 7.5,7.5 7.5,8.5 6.5,8.5 6.5,7.5"


def kml(*polygons: str) -> str:
    return f'<kml xmlns="http://www.opengis.net/kml/2.2"><Document>{"".join(polygons)}</Document></kml>'


def polygon(outer: str, hole: str | None = None) -> str:
    def ring(c: str) -> str:
        return f"<LinearRing><coordinates>{c}</coordinates></LinearRing>"

    inner = f"<innerBoundaryIs>{ring(hole)}</innerBoundaryIs>" if hole else ""
    return f"<Polygon><outerBoundaryIs>{ring(outer)}</outerBoundaryIs>{inner}</Polygon>"


def test_a_point_is_inside_only_within_the_ring() -> None:
    area = parse_kml(kml(polygon(SQUARE)))
    assert area is not None
    assert area[0].box == (6.0, 7.0, 8.0, 9.0)
    assert contains(area, lat=8.0, lng=7.0)
    assert not contains(area, lat=8.0, lng=9.0)
    assert not contains(area, lat=10.0, lng=7.0)


def test_a_hole_is_outside() -> None:
    area = parse_kml(kml(polygon(SQUARE, HOLE)))
    assert area is not None
    assert not contains(area, lat=8.0, lng=7.0)
    assert contains(area, lat=8.8, lng=7.0)


def test_any_polygon_of_several_counts() -> None:
    area = parse_kml(kml(polygon("0,0 1,0 1,1 0,1 0,0"), polygon(SQUARE)))
    assert area is not None
    assert len(area) == 2
    assert contains(area, lat=8.0, lng=7.0)


def test_altitude_and_newlines_are_accepted() -> None:
    area = parse_kml(kml(polygon("6,7,0\n 8,7,0\n 8,9,0\n 6,9,0\n 6,7,0")))
    assert area is not None
    assert contains(area, lat=8.0, lng=7.0)


def test_unreadable_kml_is_unknown_not_empty() -> None:
    assert parse_kml("<kml><Polygon>") is None
    assert parse_kml(kml()) is None
    assert parse_kml(kml(polygon("6,7 x,7 8,9"))) is None
    assert parse_kml(kml(polygon("6,7 8,7"))) is None
    assert parse_kml(kml("<Polygon></Polygon>")) is None
