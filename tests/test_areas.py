from __future__ import annotations

from ng_flood.areas import lgas


def test_the_bundle_names_the_state_and_lga() -> None:
    found = {
        (lga.state, lga.name)
        for lat, lng in ((7.80, 6.74), (6.45, 3.40))
        if (lga := lgas().at(lat, lng))
    }
    assert found == {("Kogi", "Lokoja"), ("Lagos", "Lagos Island")}
    assert lgas().at(51.5, 0.0) is None
