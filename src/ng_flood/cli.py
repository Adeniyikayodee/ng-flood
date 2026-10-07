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

from .assess import COVER_KM, FIELDS, NOTICE, Assessment
from .client import Client, Failure

Site = tuple[str, float, float, dict[str, str]]


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
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
        "--keep",
        metavar="COLUMNS",
        type=lambda text: [name.strip() for name in text.split(",") if name.strip()],
        default=[],
        help="with --sites, copy these comma-separated CSV columns into each record, e.g. lga,ward",
    )
    parser.add_argument(
        "--postcode",
        action="store_true",
        help="send each site to NIPOST for its postcode area; needs NG_POSTCODE_API_KEY",
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
    if args.keep and not args.sites:
        parser.error("--keep needs --sites")
    if clash := sorted(FIELDS.union(("id", "error")).intersection(args.keep)):
        parser.error(f"--keep cannot take {', '.join(clash)}: every record has its own")
    if args.json and args.sites:
        parser.error("--sites always prints JSON; drop --json")
    sites: list[Site] | str = [("", args.lat, args.lng, {})]
    try:
        if args.evidence:
            args.evidence.mkdir(parents=True, exist_ok=True)
        if args.sites:
            with open(args.sites, newline="", encoding="utf-8-sig") as file:
                sites = read_sites(file, args.keep)
    except OSError as error:
        sites = str(error)
    except (UnicodeError, csv.Error) as error:
        sites = f"{args.sites} is not a UTF-8 CSV: {error}"
    key = os.environ.get("GOOGLE_FLOOD_API_KEY")
    nipost = os.environ.get("NG_POSTCODE_API_KEY") if args.postcode else None
    if not isinstance(sites, str) and not (key and (nipost or not args.postcode)):
        sites = f"set {'NG_POSTCODE_API_KEY' if key else 'GOOGLE_FLOOD_API_KEY'}"
    if isinstance(sites, str) or not key:
        print(sites, file=sys.stderr)
        return 2
    now = now or datetime.now(UTC)
    with Client(key, nipost, evidence=args.evidence) as client:
        results = client.assess_many([(lat, lng) for _, lat, lng, _ in sites], now)
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


def read_sites(lines: Iterator[str], keep: Sequence[str] = ()) -> list[Site] | str:
    """Rows of a CSV with id, lat and lng columns, and any columns to keep. A number
    that cannot be read becomes NaN, so its row fails alone instead of stopping the batch."""
    reader = csv.DictReader(lines)
    if lacks := {"id", "lat", "lng", *keep} - set(reader.fieldnames or ()):
        return f"the CSV has no column named {' or '.join(sorted(lacks))}"
    return [
        (row["id"], _number(row["lat"]), _number(row["lng"]), {k: row[k] for k in keep})
        for row in reader
    ]


def records(
    sites: list[Site], results: Sequence[Assessment | Failure], now: datetime
) -> Iterator[dict[str, Any]]:
    """One record a site. A failure is logged too, so a run that found nothing leaves a trace."""
    for (site, _, _, kept), result in zip(sites, results, strict=True):
        error = {"error": str(result), "checked": now.isoformat()}
        yield {"id": site} | kept | (error if isinstance(result, Failure) else result.as_dict())


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
