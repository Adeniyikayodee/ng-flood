from __future__ import annotations

import json
from collections.abc import Mapping

from ng_flood.areas import lgas, read_geojson


def feature(name: object, geometry: Mapping[str, object]) -> dict[str, object]:
    return {"type": "Feature", "properties": {"ward": name}, "geometry": geometry}


def test_a_geojson_labels_points_by_a_property() -> None:
    square = [
        [[6, 7], [8, 7], [8, 9], [6, 9], [6, 7]],
        [[6.5, 7.5], [7.5, 7.5], [7.5, 8.5], [6.5, 7.5]],
    ]
    far = {"type": "MultiPolygon", "coordinates": [[[[0, 0], [1, 0], [1, 1], [0, 0]]]]}
    text = json.dumps(
        {
            "features": [
                feature("A", {"type": "Polygon", "coordinates": square}),
                feature(12, far),
                feature(None, far),
                feature("Line", {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}),
            ]
        }
    )
    wards = read_geojson(text, "ward")
    assert wards is not None
    assert (wards.at(8.8, 7.0), wards.at(8.0, 7.0), wards.at(0.2, 0.5)) == ("A", None, "12")
    assert read_geojson(text, "lga") is None
    assert read_geojson("{}", "ward") is None
    broken = feature("B", {"type": "Polygon", "coordinates": [[[0, 0], [1, 0]]]})
    assert read_geojson(json.dumps({"features": [broken]}), "ward") is None


def test_the_bundle_names_the_state_and_lga() -> None:
    found = {
        (lga.state, lga.name)
        for lat, lng in ((7.80, 6.74), (6.45, 3.40))
        if (lga := lgas().at(lat, lng))
    }
    assert found == {("Kogi", "Lokoja"), ("Lagos", "Lagos Island")}
    assert lgas().at(51.5, 0.0) is None
