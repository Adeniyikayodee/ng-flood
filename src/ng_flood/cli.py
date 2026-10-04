"""Print Google's flood forecast for one Nigerian coordinate, or for a CSV of sites."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .assess import COVER_KM, NOTICE, Assessment
from .client import Client, Failure

Site = tuple[str, float, float]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ng-flood", description=__doc__)
    parser.add_argument("lat", type=float, nargs="?")
    parser.add_argument("lng", type=float, nargs="?")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--sites",
        metavar="CSV",
        help="check every row of a CSV with id, lat and lng columns; prints one JSON record a line",
    )
    parser.add_argument(
        "--evidence",
        metavar="DIR",
        type=Path,
        help="save the KML of every forecast area that holds a site, named by its SHA-256",
    )
    args = parser.parse_args(argv)
    if (args.sites is None) == (args.lat is None or args.lng is None):
        parser.error("give LAT LNG or --sites CSV")
    sites: list[Site] | str = [("", args.lat, args.lng)]
    try:
        if args.evidence:
            args.evidence.mkdir(parents=True, exist_ok=True)
        if args.sites:
            with open(args.sites, newline="", encoding="utf-8-sig") as file:
                sites = read_sites(file)
    except OSError as error:
        sites = str(error)
    key = os.environ.get("GOOGLE_FLOOD_API_KEY")
    if isinstance(sites, str) or not key:
        print(sites if isinstance(sites, str) else "set GOOGLE_FLOOD_API_KEY", file=sys.stderr)
        return 2
    now = datetime.now(UTC)
    with Client(key, os.environ.get("NG_POSTCODE_API_KEY"), evidence=args.evidence) as client:
        results = client.assess_many([(lat, lng) for _, lat, lng in sites], now)
    rows = results if isinstance(results, list) else [results] * len(sites)
    if args.sites:
        for record in records(sites, rows, now):
            print(json.dumps(record))
        if isinstance(results, Failure):
            print(results, file=sys.stderr)
    elif isinstance(rows[0], Failure):
        print(rows[0], file=sys.stderr)
    else:
        print(json.dumps(rows[0].as_dict()) if args.json else render(rows[0]))
    return 0 if any(isinstance(row, Assessment) for row in rows) else 1


def read_sites(lines: Iterator[str]) -> list[Site] | str:
    """Rows of a CSV with id, lat and lng columns. A number that cannot be read
    becomes NaN, so its row fails alone instead of stopping the batch."""
    reader = csv.DictReader(lines)
    if not {"id", "lat", "lng"} <= set(reader.fieldnames or ()):
        return "the CSV needs id, lat and lng columns"
    return [(row["id"], _number(row["lat"]), _number(row["lng"])) for row in reader]


def records(
    sites: list[Site], results: Sequence[Assessment | Failure], now: datetime
) -> Iterator[dict[str, Any]]:
    """One record a site. A failure is logged too, so a run that found nothing leaves a trace."""
    for (site, _, _), result in zip(sites, results, strict=True):
        error = {"error": str(result), "checked": now.isoformat()}
        yield {"id": site} | (error if isinstance(result, Failure) else result.as_dict())


def _number(text: str | None) -> float:
    try:
        return float(text or "")
    except ValueError:
        return float("nan")


def render(a: Assessment) -> str:
    r = a.riverine
    river = (
        f"inside {r.level} area of gauge {r.gauge_id}, {r.severity}, trend {r.trend or 'unknown'}"
        if r
        else f"not inside an area ({a.gauges} gauge{'s' * (a.gauges != 1)} within {COVER_KM:g} km)"
    )
    lines = [
        f"Verdict:  {a.verdict}",
        f"Area:     {a.area or 'postcode not resolved'}",
        f"Riverine: {river}",
        *(
            [f"Lead:     {a.lead_hours:g} h until the forecast window opens"]
            if r and r.starts
            else []
        ),
        f"Flash:    {a.flash or 'none forecast'} (20 km cells)",
        *(
            [f"Watch:    gauge {w.gauge_id} is {w.severity} {w.km:g} km away and has no map"]
            if (w := a.watch)
            else []
        ),
        *(f"Gap:      {gap}" for gap in a.gaps),
        *(f"Note:     {note}" for note in a.notes),
        NOTICE,
    ]
    return "\n".join(lines)
