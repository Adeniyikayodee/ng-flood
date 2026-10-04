"""HTTP shell: the only module that performs I/O. Nothing is cached or stored."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import isfinite
from typing import Any, TypeVar
from urllib.parse import quote

import httpx
from ng_postcode import api as nipost
from ng_postcode.client import Client as Nipost

from . import google
from .assess import Assessment, assess, select
from .geometry import Polygon

T = TypeVar("T")

TIMEOUT = 10.0
MAX_PAGES = 10
REVERSE_M = 250.0
_BOUNDS = (4.0, 14.0, 2.5, 15.0)
"""A box around Nigeria: south, north, west, east."""


@dataclass(frozen=True, slots=True)
class Failure:
    """No assessment could be made."""

    reason: str

    def __str__(self) -> str:
        return self.reason


def _in_nigeria(lat: float, lng: float) -> bool:
    south, north, west, east = _BOUNDS
    return isfinite(lat) and isfinite(lng) and south <= lat <= north and west <= lng <= east


class Client:
    """Blocking client. Pass `http` to reuse your own `httpx.Client`; it stays yours to close.

    Without `nipost_key` the assessment carries no postcode.
    """

    def __init__(
        self, google_key: str, nipost_key: str | None = None, *, http: httpx.Client | None = None
    ) -> None:
        self._http = http if http is not None else httpx.Client(timeout=TIMEOUT)
        self._owns_http = http is None
        self._headers = {"X-Goog-Api-Key": google_key}
        self._nipost = Nipost(nipost_key, http=self._http) if nipost_key else None

    def assess(self, lat: float, lng: float, now: datetime | None = None) -> Assessment | Failure:
        results = self.assess_many([(lat, lng)], now)
        return results if isinstance(results, Failure) else results[0]

    def assess_many(
        self, points: Sequence[tuple[float, float]], now: datetime | None = None
    ) -> list[Assessment | Failure] | Failure:
        """Assess every (lat, lng) from one download of the forecasts.

        A point outside Nigeria fails alone. A riverine outage fails the batch.
        """
        results: list[Assessment | Failure] = [
            Failure("not a coordinate in Nigeria") for _ in points
        ]
        inside = [i for i, point in enumerate(points) if _in_nigeria(*point)]
        if not inside:
            return results
        statuses = self._all(google.STATUS_PATH, {"regionCode": "NG"}, google.statuses)
        if isinstance(statuses, Failure):
            return statuses
        flashes = self._all(google.FLASH_PATH, {"countryCodes": ["NG"]}, google.flashes)
        gaps = [f"{statuses[1]} riverine forecasts unreadable"] if statuses[1] else []
        if isinstance(flashes, Failure):
            gaps.append(f"flash floods unavailable: {flashes}")
            flashes = ((), 0)
        elif flashes[1]:
            gaps.append(f"{flashes[1]} flash forecasts unreadable")
        now = now or datetime.now(UTC)
        polygons: dict[str, tuple[Polygon, ...] | None] = {}
        for i in inside:
            lat, lng = points[i]
            selection = select(lat, lng, statuses[0], flashes[0], now)
            for pid in selection.polygon_ids:
                if pid not in polygons:
                    polygons[pid] = self._polygon(pid)
            postcode, area = self._label(lat, lng)
            result = assess(lat, lng, selection, polygons, tuple(gaps))
            results[i] = replace(result, postcode=postcode, area=area)
        return results

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _all(
        self, path: str, body: dict[str, Any], read: Callable[[Any], google.Page[T] | None]
    ) -> tuple[tuple[T, ...], int] | Failure:
        items: tuple[T, ...] = ()
        skipped = 0
        token: str | None = None
        for _ in range(MAX_PAGES):
            raw = self._call("POST", path, body | ({"pageToken": token} if token else {}))
            if isinstance(raw, Failure):
                return raw
            page = read(raw)
            if page is None:
                return Failure(f"unexpected body from {path}")
            items, skipped, token = items + page.items, skipped + page.skipped, page.next_token
            if token is None:
                return items, skipped
        return Failure(f"more than {MAX_PAGES} pages from {path}")

    def _polygon(self, polygon_id: str) -> tuple[Polygon, ...] | None:
        raw = self._call("GET", google.POLYGON_PATH + quote(polygon_id, safe=""))
        return None if isinstance(raw, Failure) else google.polygon(raw)

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any | Failure:
        try:
            response = self._http.request(
                method, google.BASE_URL + path, json=body, headers=self._headers
            )
        except httpx.HTTPError as error:
            return Failure(type(error).__name__)
        if response.status_code != 200:
            return Failure(f"HTTP {response.status_code} from {path}")
        try:
            return response.json()
        except ValueError:
            return Failure(f"unreadable body from {path}")

    def _label(self, lat: float, lng: float) -> tuple[str | None, str | None]:
        if self._nipost is None:
            return None, None
        found = self._nipost.send(nipost.reverse(nipost.Coordinate(lat, lng), REVERSE_M))
        if not isinstance(found, nipost.Reverse):
            return None, None
        return (found.unit.postcode or None) if found.unit else None, found.area
