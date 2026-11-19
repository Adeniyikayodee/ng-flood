from __future__ import annotations

from ng_flood.basins import Basins, bundled
from ng_flood.geometry import Polygon


def square(x: float, y: float) -> tuple[Polygon, ...]:
    return (Polygon(((x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1))),)


def test_reach_is_the_basin_all_upstream_and_the_next_down() -> None:
    basins = Basins(
        [(1, 0, square(0, 0)), (2, 1, square(1, 0)), (3, 2, square(2, 0)), (4, 0, square(3, 0))]
    )
    assert basins.at(0.5, 2.5) == 3
    assert basins.at(5.0, 5.0) is None
    assert basins.reach(2) == {1, 2, 3}
    assert basins.reach(1) == {1, 2, 3}
    assert basins.reach(None) == frozenset()


def test_the_bundle_follows_the_niger_and_the_benue() -> None:
    basins = bundled()
    at = {
        name: basins.at(lat, lng)
        for name, (lat, lng) in {
            "onitsha": (6.15, 6.78),
            "lokoja": (7.80, 6.74),
            "makurdi": (7.735, 8.53),
            "jebba": (9.14, 4.80),
            "garoua": (9.30, 13.40),
            "kano": (12.0, 8.52),
            "lagos": (6.45, 3.40),
        }.items()
    }
    below = basins.reach(at["onitsha"])
    assert {at["lokoja"], at["makurdi"], at["jebba"], at["garoua"]} <= below
    assert not {at["kano"], at["lagos"]} & below
    assert at["makurdi"] not in basins.reach(at["lokoja"])
