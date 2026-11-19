"""Print Google's flood forecast for one Nigerian coordinate, or for a CSV of sites."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .assess import COVER_KM, FIELDS, NOTICE, Assessment, judged
from .client import Client, Failure
from .snapshot import Snapshot

Site = tuple[str, float, float, dict[str, str]]
VERDICTS = ("FORECAST", "WATCH", "UNKNOWN", "NONE")
"""Worst first. UNKNOWN outranks NONE, so one blind site stops an area reading as clear."""


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
        "--by",
        choices=["postcode"],
        help="with --sites, print one record for each postcode area, not each site; "
        "implies --postcode",
    )
    parser.add_argument(
        "--records",
        metavar="FILE",
        type=Path,
        help="with --by, append each site's record to this file",
    )
    parser.add_argument(
        "--evidence",
        metavar="DIR",
        type=Path,
        help="save the KML of every forecast area that holds a site, named by its SHA-256",
    )
    parser.add_argument(
        "--fetch",
        metavar="FILE",
        type=Path,
        help="save Nigeria's forecasts and every area they name to FILE (.gz to compress)",
    )
    parser.add_argument(
        "--snapshot",
        metavar="FILE",
        type=Path,
        help="judge from forecasts saved by --fetch, offline and without a Google key",
    )
    parser.add_argument(
        "--at",
        metavar="TIME",
        type=moment,
        help="judge as of this ISO time with offset; with --snapshot, replays a record",
    )
    args = parser.parse_args(argv)
    if args.fetch:
        if args.lat is not None or args.sites or args.postcode or args.json or args.evidence:
            parser.error("--fetch takes no sites or other options")
        return fetch(args.fetch, now)
    if (args.sites is None) == (args.lat is None or args.lng is None):
        parser.error("give LAT LNG or --sites CSV")
    if (args.keep or args.by) and not args.sites:
        parser.error("--keep and --by need --sites")
    if args.records and not args.by:
        parser.error("--records needs --by")
    if clash := sorted(FIELDS.union(("id", "error")).intersection(args.keep)):
        parser.error(f"--keep cannot take {', '.join(clash)}: every record has its own")
    if args.json and args.sites:
        parser.error("--sites always prints JSON; drop --json")
    sites: list[Site] | str = [("", args.lat, args.lng, {})]
    snapshot = load(args.snapshot) if args.snapshot else None
    if args.snapshot and snapshot is None:
        sites = f"{args.snapshot} is not a snapshot saved by --fetch"
    try:
        if args.evidence:
            args.evidence.mkdir(parents=True, exist_ok=True)
        if args.records:
            args.records.open("a").close()
        if args.sites:
            with open(args.sites, newline="", encoding="utf-8-sig") as file:
                sites = read_sites(file, args.keep)
    except OSError as error:
        sites = str(error)
    except (UnicodeError, csv.Error) as error:
        sites = f"{args.sites} is not a UTF-8 CSV: {error}"
    key = os.environ.get("GOOGLE_FLOOD_API_KEY", "")
    label = args.postcode or args.by
    nipost = os.environ.get("NG_POSTCODE_API_KEY") if label else None
    need = [
        name
        for name, ok in (
            ("GOOGLE_FLOOD_API_KEY", key or snapshot),
            ("NG_POSTCODE_API_KEY", nipost or not label),
        )
        if not ok
    ]
    if need and not isinstance(sites, str):
        sites = f"set {need[0]}"
    if isinstance(sites, str):
        print(sites, file=sys.stderr)
        return 2
    now = args.at or now or datetime.now(UTC)
    with Client(key, nipost, evidence=args.evidence) as client:
        results = client.assess_many([(lat, lng) for _, lat, lng, _ in sites], now, snapshot)
    rows = results if isinstance(results, list) else [results] * len(sites)
    if args.records:
        with args.records.open("a", encoding="utf-8") as log:
            log.writelines(json.dumps(record) + "\n" for record in records(sites, rows, now))
    if args.sites:
        for record in roll_up(rows, now) if args.by else records(sites, rows, now):
            print(json.dumps(record))
        if isinstance(results, Failure):
            print(results, file=sys.stderr)
    elif isinstance(rows[0], Failure):
        print(rows[0], file=sys.stderr)
    else:
        print(json.dumps(rows[0].as_dict()) if args.json else render(rows[0]))
    return 0 if any(isinstance(row, Assessment) for row in rows) else 1


def fetch(path: Path, now: datetime | None = None) -> int:
    if not (key := os.environ.get("GOOGLE_FLOOD_API_KEY")):
        print("set GOOGLE_FLOOD_API_KEY", file=sys.stderr)
        return 2
    with Client(key) as client:
        snapshot = client.fetch(now, whole=True)
    try:
        if isinstance(snapshot, Failure):
            raise OSError(snapshot)
        data = snapshot.dumps().encode()
        path.write_bytes(gzip.compress(data, mtime=0) if path.suffix == ".gz" else data)
    except OSError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


def moment(text: str) -> datetime:
    time = datetime.fromisoformat(text)
    if time.tzinfo is None:
        raise ValueError(text)
    return time


def load(path: Path) -> Snapshot | None:
    try:
        data = path.read_bytes()
        return Snapshot.loads((gzip.decompress(data) if path.suffix == ".gz" else data).decode())
    except (OSError, EOFError, UnicodeError):
        return None


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


def roll_up(results: Sequence[Assessment | Failure], now: datetime) -> list[dict[str, Any]]:
    """One record a postcode area, worst first. Sites with no area share a null row and
    failures are counted, so the rows add up to the input."""
    groups: dict[str | None, list[Assessment | Failure]] = {}
    for result in results:
        groups.setdefault(getattr(result, "area", None), []).append(result)
    rows = []
    for area, group in groups.items():
        found = [result for result in group if isinstance(result, Assessment)]
        counts = Counter(result.verdict for result in found)
        leads = [result.lead_hours for result in found if result.lead_hours is not None]
        rows.append(
            {
                "area": area,
                "sites": len(group),
                "verdict": next((verdict for verdict in VERDICTS if counts[verdict]), None),
                **{verdict: counts[verdict] for verdict in VERDICTS},
                "errors": len(group) - len(found),
                "lead_hours": min(leads, default=None),
                "checked": now.isoformat(),
            }
            | judged()
        )
    return sorted(rows, key=lambda row: VERDICTS.index(row["verdict"] or "NONE"))


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
            [
                f"Watch:    gauge {w.gauge_id} is {w.severity} {w.km:g} km away and "
                + ("its alert area holds the point" if w.alert_area else "has no map")
            ]
            if (w := a.watch)
            else []
        ),
        *(f"Gap:      {gap}" for gap in a.gaps),
        *(f"Note:     {note}" for note in a.notes),
        "Covers:   river and flash floods, not town rain, the sea or dam failures",
        NOTICE,
    ]
    return "\n".join(lines)
