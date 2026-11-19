from __future__ import annotations

import pytest

from ng_flood.areas import Areas, Lga
from ng_flood.basins import Basins
from ng_flood.geometry import Polygon


def box(west: float, south: float, east: float, north: float) -> tuple[Polygon, ...]:
    return (Polygon(((west, south), (east, south), (east, north), (west, north))),)


@pytest.fixture(autouse=True)
def basins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two basins that do not meet: one holds the test site and gauge g1, one lies north."""
    fake = Basins([(1, 0, box(6.5, 7.5, 7.5, 8.4)), (2, 0, box(6.5, 8.4, 7.5, 9.0))])
    monkeypatch.setattr("ng_flood.client.bundled", lambda: fake)


@pytest.fixture(autouse=True)
def lgas(monkeypatch: pytest.MonkeyPatch) -> None:
    """One LGA around the test site."""
    fake = Areas([(Lga("Kogi", "Lokoja", "NG023012"), box(6.5, 7.5, 7.5, 8.4))])
    monkeypatch.setattr("ng_flood.client.lgas", lambda: fake)


@pytest.fixture(autouse=True)
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Retries wait no time in tests; the waits asked for are recorded."""
    asked: list[float] = []
    monkeypatch.setattr("ng_flood.client.sleep", asked.append)
    return asked
