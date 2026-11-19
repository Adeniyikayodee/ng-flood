# /// script
# requires-python = ">=3.11"
# dependencies = ["shapely>=2.0"]
# ///
"""Build src/ng_flood/areas.json from OCHA's Nigeria admin boundaries (COD-AB, CC BY-IGO):
every LGA with its state and P-code, simplified to 0.005 degrees.

    # nga_admin_boundaries.geojson.zip from https://data.humdata.org/dataset/cod-ab-nga
    unzip adm.zip && uv run scripts/areas.py nga_admin2.geojson
"""

import json
import sys
from pathlib import Path

from shapely.geometry import shape

OUT = Path(__file__).parent.parent / "src" / "ng_flood" / "areas.json"

lgas = []
for feature in json.loads(Path(sys.argv[1]).read_text())["features"]:
    p = feature["properties"]
    simple = shape(feature["geometry"]).simplify(0.005)
    rings = [
        [round(v * 1000) for xy in part.exterior.coords[:-1] for v in xy]
        for part in getattr(simple, "geoms", [simple])
        if len(part.exterior.coords) > 3
    ]
    lgas.append([p["adm1_name"], p["adm2_name"], p["adm2_pcode"], rings])

data = {"source": "OCHA COD-AB Nigeria, CC BY-IGO", "scale": 1000, "lgas": sorted(lgas)}
OUT.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n")
print(len(lgas), "LGAs")
