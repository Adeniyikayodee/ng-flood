"""Decoders for Google's Flood Forecasting API. Pure: no I/O.

A record that cannot be read is counted, never dropped silently.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from .geometry import Polygon, parse_kml

T = TypeVar("T")

BASE_URL = "https://floodforecasting.googleapis.com"
STATUS_PATH = "/v1/floodStatus:searchLatestFloodStatusByArea"
FLASH_PATH = "/v1/flashFloods:search"
POLYGON_PATH = "/v1/serializedPolygons/"


@dataclass(frozen=True, slots=True)
class Status:
    """A gauge's latest riverine forecast."""

    gauge_id: str
    lat: float
    lng: float
    severity: str
    trend: str | None
    issued: datetime | None
    maps: tuple[tuple[str, str], ...]
    """(level, polygon id) pairs; level is HIGH, MEDIUM or LOW."""
    map_type: str | None
    alert_polygon_id: str | None


@dataclass(frozen=True, slots=True)
class Flash:
    """A flash flood forecast. Its cells are about 20 km wide."""

    issued: datetime | None
    period_hours: float | None
    likely_id: str | None
    highly_likely_id: str | None


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    skipped: int
    next_token: str | None


def statuses(body: Any) -> Page[Status] | None:
    return _page(body, "floodStatuses", _status)


def flashes(body: Any) -> Page[Flash] | None:
    return _page(body, "flashFloodEvents", _flash)


def polygon(body: Any) -> tuple[Polygon, ...] | None:
    kml = _text(body, "kml") if isinstance(body, Mapping) else None
    return parse_kml(kml) if kml else None


def _page(body: Any, key: str, read: Callable[[Any], T | None]) -> Page[T] | None:
    if not isinstance(body, Mapping):
        return None
    raw = body.get(key, [])
    if not isinstance(raw, list):
        return None
    items = tuple(item for item in map(read, raw) if item is not None)
    return Page(items, len(raw) - len(items), _text(body, "nextPageToken"))


def _status(raw: Any) -> Status | None:
    if not isinstance(raw, Mapping):
        return None
    gauge, at = _text(raw, "gaugeId"), raw.get("gaugeLocation")
    if gauge is None or not isinstance(at, Mapping):
        return None
    lat, lng = _number(at, "latitude"), _number(at, "longitude")
    if lat is None or lng is None:
        return None
    map_set = raw.get("inundationMapSet")
    map_set = map_set if isinstance(map_set, Mapping) else {}
    maps = map_set.get("inundationMaps")
    return Status(
        gauge_id=gauge,
        lat=lat,
        lng=lng,
        severity=_text(raw, "severity") or "UNKNOWN",
        trend=_text(raw, "forecastTrend"),
        issued=_time(raw.get("issuedTime")),
        maps=tuple(
            (level, pid)
            for m in (maps if isinstance(maps, list) else [])
            if isinstance(m, Mapping)
            and (level := _text(m, "level"))
            and (pid := _text(m, "serializedPolygonId"))
        ),
        map_type=_text(map_set, "inundationMapType"),
        alert_polygon_id=_text(raw, "serializedNotificationPolygonId"),
    )


def _flash(raw: Any) -> Flash | None:
    if not isinstance(raw, Mapping):
        return None
    likely = _text(raw, "likelyAffectedPolygonId") or _text(raw, "eventPolygonId")
    highly = _text(raw, "highlyLikelyAffectedPolygonId")
    if likely is None and highly is None:
        return None
    return Flash(
        _time(raw.get("forecastIssueTime")), _number(raw, "forecastPeriodHours"), likely, highly
    )


def _text(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def _number(data: Mapping[str, Any], key: str) -> float | None:
    value = data.get(key)
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        time = datetime.fromisoformat(value)
    except ValueError:
        return None
    return time if time.tzinfo else time.replace(tzinfo=UTC)
