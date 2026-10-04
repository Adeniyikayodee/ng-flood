"""Join a coordinate to the forecasts around it. Pure: no I/O.

The verdict is never more precise than the evidence: anything missing, stale
or unreadable makes it UNKNOWN rather than NONE.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import asin, cos, radians, sin, sqrt

from .geometry import Polygon, contains
from .google import Flash, Status

RANGE_KM = 150.0
"""Inundation maps reach tens of km from their gauge; this leaves a wide margin."""
STALE = timedelta(hours=48)
ALERT_AREA = "ALERT_AREA"
_LEVELS = ("HIGH", "MEDIUM", "LOW", ALERT_AREA)
_SEVERITIES = ("EXTREME", "SEVERE", "ABOVE_NORMAL")

Polygons = Mapping[str, tuple[Polygon, ...] | None]


@dataclass(frozen=True, slots=True)
class Selection:
    """The fresh forecasts that could reach a point."""

    statuses: tuple[Status, ...]
    flashes: tuple[Flash, ...]
    stale: int

    @property
    def polygon_ids(self) -> tuple[str, ...]:
        ids = [pid for s in self.statuses for _, pid in _areas(s)]
        ids += [pid for f in self.flashes for pid in (f.highly_likely_id, f.likely_id) if pid]
        return tuple(dict.fromkeys(ids))


@dataclass(frozen=True, slots=True)
class Riverine:
    gauge_id: str
    level: str
    """HIGH, MEDIUM or LOW inundation area, or ALERT_AREA."""
    severity: str
    trend: str | None
    map_type: str | None
    issued: datetime | None


@dataclass(frozen=True, slots=True)
class Assessment:
    lat: float
    lng: float
    riverine: Riverine | None
    flash: str | None
    """HIGHLY_LIKELY or LIKELY, at 20 km resolution."""
    gauges: int
    gaps: tuple[str, ...]
    postcode: str | None = None
    area: str | None = None

    @property
    def verdict(self) -> str:
        if self.riverine or self.flash:
            return "FORECAST"
        return "UNKNOWN" if self.gaps or not self.gauges else "NONE"


def select(
    lat: float, lng: float, statuses: tuple[Status, ...], flashes: tuple[Flash, ...], now: datetime
) -> Selection:
    near = [s for s in statuses if _km(lat, lng, s.lat, s.lng) <= RANGE_KM]
    fresh = tuple(s for s in near if s.issued and now - s.issued <= STALE)
    live = tuple(f for f in flashes if _live(f, now))
    return Selection(fresh, live, len(near) - len(fresh) + len(flashes) - len(live))


def assess(
    lat: float, lng: float, selection: Selection, polygons: Polygons, gaps: tuple[str, ...] = ()
) -> Assessment:
    def inside(pid: str | None) -> bool:
        area = polygons.get(pid) if pid else None
        return area is not None and contains(area, lat, lng)

    hits = [
        Riverine(s.gauge_id, level, s.severity, s.trend, s.map_type, s.issued)
        for s in selection.statuses
        for level, pid in _areas(s)
        if inside(pid)
    ]
    flash = next(
        (
            name
            for name, pids in (
                ("HIGHLY_LIKELY", [f.highly_likely_id for f in selection.flashes]),
                ("LIKELY", [f.likely_id for f in selection.flashes]),
            )
            if any(map(inside, pids))
        ),
        None,
    )
    missing = sum(polygons.get(pid) is None for pid in selection.polygon_ids)
    notes = [
        *gaps,
        *([f"{selection.stale} stale forecasts ignored"] if selection.stale else []),
        *([f"{missing} forecast areas unavailable"] if missing else []),
    ]
    return Assessment(
        lat, lng, min(hits, key=_rank, default=None), flash, len(selection.statuses), tuple(notes)
    )


def _areas(status: Status) -> list[tuple[str, str]]:
    alert = [(ALERT_AREA, status.alert_polygon_id)] if status.alert_polygon_id else []
    return [(level, pid) for level, pid in status.maps if level in _LEVELS] + alert


def _rank(hit: Riverine) -> tuple[int, int]:
    severity = hit.severity if hit.severity in _SEVERITIES else None
    return _LEVELS.index(hit.level), _SEVERITIES.index(severity) if severity else len(_SEVERITIES)


def _live(flash: Flash, now: datetime) -> bool:
    if flash.issued is None or flash.period_hours is None:
        return False
    return now <= flash.issued + timedelta(hours=flash.period_hours)


def _km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    a = (
        sin(radians(lat2 - lat1) / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(radians(lng2 - lng1) / 2) ** 2
    )
    return 12742.0 * asin(sqrt(a))
