"""`ng-flood LAT LNG`: print Google's flood forecast for a Nigerian coordinate."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .assess import NOTICE, Assessment
from .client import Client, Failure


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ng-flood", description=__doc__)
    parser.add_argument("lat", type=float)
    parser.add_argument("lng", type=float)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    key = os.environ.get("GOOGLE_FLOOD_API_KEY")
    if not key:
        print("set GOOGLE_FLOOD_API_KEY", file=sys.stderr)
        return 2
    with Client(key, os.environ.get("NG_POSTCODE_API_KEY")) as client:
        result = client.assess(args.lat, args.lng)
    if isinstance(result, Failure):
        print(result, file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result.as_dict()))
    else:
        print(render(result))
    return 0


def render(a: Assessment) -> str:
    r = a.riverine
    river = (
        f"inside {r.level} area of gauge {r.gauge_id}, {r.severity}, trend {r.trend or 'unknown'}"
        if r
        else f"not inside a forecast area ({a.gauges} gauges within range)"
    )
    lines = [
        f"Verdict:  {a.verdict}",
        f"Area:     {a.area or 'postcode not resolved'}",
        f"Riverine: {river}",
        f"Flash:    {a.flash or 'none forecast'} (20 km cells)",
        *(f"Gap:      {gap}" for gap in a.gaps),
        NOTICE,
    ]
    return "\n".join(lines)
