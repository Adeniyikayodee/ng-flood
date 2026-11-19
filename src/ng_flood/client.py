"""HTTP shell: the only module that performs I/O. Nothing is cached or stored."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from importlib.resources import files
from math import isfinite
from pathlib import Path
from time import sleep
from typing import Any
from urllib.parse import quote

import httpx
from ng_postcode import api as nipost
from ng_postcode.client import Client as Nipost

from . import google
from .areas import Areas, Lga, lgas
from .assess import Assessment, area_ids, assess, select
from .basins import Basins, bundled
from .geometry import Polygon, contains, parse_kml
from .snapshot import Snapshot, read

TIMEOUT = 10.0
MAX_PAGES = 100
"""A guard against a token that never ends, not a limit on the data."""
WAITS = (0.5, 2.0)
"""Seconds before each retry of a 429, a 5xx or a network error."""
MAX_WAIT = 30.0
STRIKES = 3
"""Failures in a row before an endpoint is left alone for the rest of the run."""
REVERSE_M = 250.0
_NIGERIA = parse_kml(files("ng_flood").joinpath("nigeria.kml").read_text()) or ()
"""The border, about 10 km generous so border and coastal sites pass."""


@dataclass(frozen=True, slots=True)
class Failure:
    """No assessment could be made."""

    reason: str

    def __str__(self) -> str:
        return self.reason


def _in_nigeria(lat: float, lng: float) -> bool:
    return isfinite(lat + lng) and contains(_NIGERIA, lat, lng)


def _retry_after(response: httpx.Response) -> float:
    try:
        return min(float(response.headers.get("Retry-After", 0)), MAX_WAIT)
    except ValueError:
        return 0.0


class Client:
    """Blocking client. Pass `http` to reuse your own `httpx.Client`; it stays yours to close.

    Without `nipost_key` the assessment carries no postcode. With `evidence`, the KML of
    every area that holds a point is saved in that directory, named by its SHA-256.
    """

    def __init__(
        self,
        google_key: str,
        nipost_key: str | None = None,
        *,
        http: httpx.Client | None = None,
        evidence: Path | None = None,
        basins: Basins | None = None,
        areas: Areas[Lga] | None = None,
    ) -> None:
        self._evidence = evidence
        self._basins = basins
        self._lgas = areas
        self._http = http if http is not None else httpx.Client(timeout=TIMEOUT)
        self._owns_http = http is None
        self._headers = {"X-Goog-Api-Key": google_key}
        self._nipost = Nipost(nipost_key, http=self._http) if nipost_key else None
        self._strikes: Counter[str] = Counter()

    def assess(self, lat: float, lng: float, now: datetime | None = None) -> Assessment | Failure:
        results = self.assess_many([(lat, lng)], now)
        return results if isinstance(results, Failure) else results[0]

    def fetch(self, now: datetime | None = None, *, whole: bool = False) -> Snapshot | Failure:
        """Download Nigeria's forecasts; with `whole`, every area they name as well."""
        self._strikes.clear()
        fetched = now or datetime.now(UTC)
        statuses: dict[str, list[Any] | str] = {}
        for region in google.REGIONS:
            pages = self._pages(google.STATUS_PATH, {"regionCode": region}, google.statuses)
            if isinstance(pages, Failure) and region == google.REGIONS[0]:
                return pages
            statuses[region] = str(pages) if isinstance(pages, Failure) else pages
        flashes = self._pages(google.FLASH_PATH, {"countryCodes": ["NG"]}, google.flashes)
        snapshot = Snapshot(
            fetched, statuses, str(flashes) if isinstance(flashes, Failure) else flashes
        )
        forecasts = read(snapshot)
        if whole and forecasts:
            ids = area_ids(forecasts.statuses, forecasts.flashes)
            snapshot.kml, snapshot.complete = {pid: self._kml(pid) for pid in ids}, True
        return snapshot

    def assess_many(
        self,
        points: Sequence[tuple[float, float]],
        now: datetime | None = None,
        snapshot: Snapshot | None = None,
    ) -> list[Assessment | Failure] | Failure:
        """Assess every (lat, lng) from one download of the forecasts, or from `snapshot`.

        A point outside Nigeria fails alone. A riverine outage fails the batch, and
        so does evidence that cannot be saved.
        """
        results: list[Assessment | Failure] = [
            Failure(
                "not a coordinate in Nigeria"
                if isfinite(lat + lng)
                else "lat or lng is not a number"
            )
            for lat, lng in points
        ]
        inside = [i for i, point in enumerate(points) if _in_nigeria(*point)]
        self._strikes.clear()
        if not inside:
            return results
        source = snapshot or self.fetch()
        if isinstance(source, Failure):
            return source
        forecasts = read(source)
        if forecasts is None:
            return Failure("the snapshot holds a page Google would not send")
        now = now or datetime.now(UTC)
        digest = source.digest() if snapshot and snapshot.complete else None
        basins = self._basins or bundled()
        places = self._lgas or lgas()
        homes = {s: basins.at(s.lat, s.lng) for s in forecasts.statuses}
        polygons: dict[str, tuple[Polygon, ...] | None] = {}
        digests: dict[str, str] = {}
        saved: set[str] = set()
        for i in inside:
            lat, lng = points[i]
            reach = basins.reach(basins.at(lat, lng))
            cover = frozenset(s for s, home in homes.items() if home in reach)
            selection = select(lat, lng, forecasts.statuses, forecasts.flashes, now, cover)
            for pid in selection.polygon_ids:
                if pid not in polygons:
                    if pid not in source.kml and not source.complete:
                        source.kml[pid] = self._kml(pid)
                    text = source.kml.get(pid) or ""
                    polygons[pid] = parse_kml(text)
                    digests[pid] = sha256(text.encode()).hexdigest()
            postcode, area, why = self._label(lat, lng)
            result = assess(lat, lng, selection, polygons, forecasts.gaps, digests)
            lga = places.at(lat, lng)
            results[i] = replace(
                result,
                postcode=postcode,
                area=area,
                state=lga.state if lga else None,
                lga=lga.name if lga else None,
                lga_pcode=lga.pcode if lga else None,
                notes=result.notes + why,
                snapshot=digest,
            )
            if self._evidence:
                try:
                    for pid in result.areas.keys() - saved:
                        path = self._evidence / f"{digests[pid]}.kml"
                        path.write_text(source.kml.get(pid) or "", encoding="utf-8")
                except OSError as error:
                    return Failure(f"evidence not saved: {error}")
                saved |= result.areas.keys()
        return results

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _pages(
        self, path: str, body: dict[str, Any], read: Callable[[Any], google.Page[Any] | None]
    ) -> list[Any] | Failure:
        pages: list[Any] = []
        token: str | None = None
        for _ in range(MAX_PAGES):
            raw = self._call("POST", path, body | ({"pageToken": token} if token else {}))
            if isinstance(raw, Failure):
                return raw
            page = read(raw)
            if page is None:
                return Failure(f"unexpected body from {path}")
            pages.append(raw)
            token = page.next_token
            if token is None:
                return pages
        return Failure(f"more than {MAX_PAGES} pages from {path}")

    def _kml(self, polygon_id: str) -> str | None:
        if self._strikes["areas"] >= STRIKES:
            return None
        raw = self._call("GET", google.POLYGON_PATH + quote(polygon_id, safe=""))
        self._strike("areas", isinstance(raw, Failure))
        return None if isinstance(raw, Failure) else google.kml(raw)

    def _strike(self, endpoint: str, failed: bool) -> None:
        self._strikes[endpoint] = self._strikes[endpoint] + 1 if failed else 0

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any | Failure:
        for wait in (*WAITS, None):
            try:
                response = self._http.request(
                    method, google.BASE_URL + path, json=body, headers=self._headers
                )
            except httpx.HTTPError as error:
                failure = Failure(type(error).__name__)
            else:
                if response.status_code == 200:
                    try:
                        return response.json()
                    except ValueError:
                        return Failure(f"unreadable body from {path}")
                failure = Failure(f"HTTP {response.status_code} from {path}")
                if response.status_code != 429 and response.status_code < 500:
                    return failure
                wait = wait and max(wait, _retry_after(response))
            if wait is None:
                break
            sleep(wait)
        return failure

    def _label(self, lat: float, lng: float) -> tuple[str | None, str | None, tuple[str, ...]]:
        """The postcode and its area, or a note saying why there is none."""
        if self._nipost is None:
            return None, None, ()
        if self._strikes["postcodes"] >= STRIKES:
            return None, None, (f"postcode lookup stopped after {STRIKES} failures",)
        found = self._nipost.send(nipost.reverse(nipost.Coordinate(lat, lng), REVERSE_M))
        self._strike("postcodes", not isinstance(found, nipost.Reverse))
        if not isinstance(found, nipost.Reverse):
            return None, None, (f"postcode lookup failed: {getattr(found, 'code', found)}",)
        if found.area is None:
            return None, None, (f"no postcode within {REVERSE_M:g} m",)
        return (found.unit.postcode or None) if found.unit else None, found.area, ()
