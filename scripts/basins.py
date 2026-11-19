# /// script
# requires-python = ">=3.11"
# dependencies = ["pyshp>=2.3", "shapely>=2.0"]
# ///
"""Build src/ng_flood/basins.json from HydroBASINS v1c level 8 for Africa (CC BY 4.0):
every basin within 150 km of Nigeria, simplified to 0.01 degrees, with the basin it drains to.

    curl -LO https://data.hydrosheds.org/file/hydrobasins/standard/hybas_af_lev08_v1c.zip
    unzip hybas_af_lev08_v1c.zip && uv run scripts/basins.py hybas_af_lev08_v1c.shp
"""

import json
import sys
from pathlib import Path
from xml.etree import ElementTree

import shapefile
from shapely.geometry import Polygon, shape

ROOT = Path(__file__).parent.parent / "src" / "ng_flood"
MARGIN = 1.4
"""Degrees, about 150 km: no gauge further away is read."""

ns = {"k": "http://www.opengis.net/kml/2.2"}
text = ElementTree.parse(ROOT / "nigeria.kml").find(".//k:coordinates", ns).text
border = Polygon([tuple(map(float, p.split(","))) for p in text.split()])
region = border.buffer(MARGIN)

kept = {}
for record in shapefile.Reader(sys.argv[1]).iterShapeRecords():
    geometry = shape(record.shape.__geo_interface__)
    if geometry.intersects(region):
        kept[record.record["HYBAS_ID"]] = (record.record["NEXT_DOWN"], geometry)

basins = []
for hybas, (down, geometry) in sorted(kept.items()):
    simple = geometry.simplify(0.01)
    parts = getattr(simple, "geoms", [simple])
    rings = [
        [round(v * 100) for xy in part.exterior.coords[:-1] for v in xy]
        for part in parts
        if len(part.exterior.coords) > 3
    ]
    basins.append([hybas, down if down in kept else 0, rings])

data = {"source": "HydroBASINS v1c level 8, CC BY 4.0", "scale": 100, "basins": basins}
(ROOT / "basins.json").write_text(json.dumps(data, separators=(",", ":")) + "\n")
print(len(basins), "basins")
