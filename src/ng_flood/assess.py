"""Join a coordinate to the forecasts around it. Pure: no I/O.

The verdict is never more precise than the evidence: anything missing, stale
or unreadable makes it UNKNOWN rather than NONE.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from functools import cache
from importlib.metadata import version
from math import asin, cos, radians, sin, sqrt
from typing import Any

from .geometry import Polygon, contains
from .google import Flash, Status

RANGE_KM = 150.0
"""Inundation maps reach tens of km from their gauge; this leaves a wide margin."""
COVER_KM = 50.0
"""NONE needs a fresh gauge this close. Provisional until checked against live data."""
STALE = timedelta(hours=48)
ALERT_AREA = "ALERT_AREA"
_LEVELS = ("HIGH", "MEDIUM", "LOW", ALERT_AREA)
_SEVERITIES = ("EXTREME", "SEVERE", "ABOVE_NORMAL")
_KNOWN = (*_SEVERITIES, "NO_FLOODING")
NOTICE = (
    "Forecast: Google Flood Forecasting, CC BY 4.0. Not an official warning; "
    "follow NEMA and NIHSA. Postcode: NIPOST."
)

Polygons = Mapping[str, tuple[Polygon, ...] | None]


@dataclass(frozen=True, slots=True)
class Selection:
    """The fresh forecasts that could reach a point."""

    statuses: tuple[Status, ...]
    flashes: tuple[Flash, ...]
    stale: tuple[int, int]
    """Stale gauges within COVER_KM, and beyond it."""
    now: datetime

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
    polygon_id: str
    """The forecast area that holds the point, for the audit trail."""
    starts: datetime | None
    ends: datetime | None
    change_m: tuple[float, float] | None


@dataclass(frozen=True, slots=True)
class Watch:
    """An elevated gauge that publishes no area to test the point against."""

    gauge_id: str
    severity: str
    km: float


@dataclass(frozen=True, slots=True)
class Assessment:
    lat: float
    lng: float
    checked: datetime
    """The time the forecasts were judged fresh against."""
    riverine: Riverine | None
    flash: str | None
    """HIGHLY_LIKELY or LIKELY, at 20 km resolution."""
    gauges: int
    """Fresh gauges of known severity within COVER_KM."""
    gaps: tuple[str, ...]
    """What stops a NONE: anything stale, missing or unreadable that could reach the point."""
    postcode: str | None = None
    area: str | None = None
    watch: Watch | None = None
    nearest_gauge_km: float | None = None
    notes: tuple[str, ...] = ()
    """Gaps too far away to change the verdict."""
    areas: Mapping[str, str | None] = field(default_factory=dict)
    """Every forecast area that holds the point, by id, with the SHA-256 of its KML."""

    @property
    def verdict(self) -> str:
        if self.riverine or self.flash:
            return "FORECAST"
        if self.watch:
            return "WATCH"
        return "UNKNOWN" if self.gaps or not self.gauges else "NONE"

    @property
    def lead_hours(self) -> float | None:
        """Hours until the riverine forecast window opens; 0 once it has."""
        if self.riverine is None or self.riverine.starts is None:
            return None
        return max(0.0, round((self.riverine.starts - self.checked).total_seconds() / 3600, 1))

    def as_dict(self) -> dict[str, Any]:
        """A JSON-ready record of the verdict and the evidence behind it."""
        data: dict[str, Any] = json.loads(json.dumps(asdict(self), default=datetime.isoformat))
        return data | {
            "verdict": self.verdict,
            "lead_hours": self.lead_hours,
            "version": version("ng-flood"),
            "cover_km": COVER_KM,
            "stale_hours": STALE.total_seconds() / 3600,
            "notice": NOTICE,
        }


FIELDS = frozenset(Assessment.__dataclass_fields__) | {
    "verdict",
    "lead_hours",
    "version",
    "cover_km",
    "stale_hours",
    "notice",
}
"""Every key of a record."""


def select(
    lat: float, lng: float, statuses: tuple[Status, ...], flashes: tuple[Flash, ...], now: datetime
) -> Selection:
    near = [(km, s) for s in statuses if (km := _km(lat, lng, s.lat, s.lng)) <= RANGE_KM]
    fresh = tuple(s for _, s in near if _fresh(s, now))
    old = [km <= COVER_KM for km, s in near if not _fresh(s, now)]
    live = tuple(f for f in flashes if now <= f.issued + timedelta(hours=f.period_hours))
    return Selection(fresh, live, (sum(old), len(old) - sum(old)), now)


def assess(
    lat: float,
    lng: float,
    selection: Selection,
    polygons: Polygons,
    gaps: tuple[str, ...] = (),
    digests: Mapping[str, str] | None = None,
) -> Assessment:
    @cache
    def inside(pid: str | None) -> bool:
        area = polygons.get(pid) if pid else None
        return area is not None and contains(area, lat, lng)

    def km(s: Status) -> float:
        return _km(lat, lng, s.lat, s.lng)

    hits = [
        Riverine(
            s.gauge_id,
            level,
            s.severity,
            s.trend,
            s.map_type,
            s.issued,
            pid,
            s.starts,
            s.ends,
            s.change_m,
        )
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
    close = [s for s in selection.statuses if km(s) <= COVER_KM]
    near_ids = {pid for s in close for _, pid in _areas(s)}
    far_ids = {pid for s in selection.statuses for _, pid in _areas(s)} - near_ids
    missing = [pid for pid in selection.polygon_ids if polygons.get(pid) is None]
    far = sum(pid in far_ids for pid in missing)
    stale, far_stale = selection.stale
    beyond = f"beyond {COVER_KM:g} km"
    worst = min(hits, key=_rank, default=None)
    known = sorted(km(s) for s in selection.statuses if s.severity in _KNOWN)
    blind = [s for s in close if s.severity in _SEVERITIES and not _areas(s)]
    risen = min(blind, key=lambda s: (_SEVERITIES.index(s.severity), km(s)), default=None)
    return Assessment(
        lat,
        lng,
        selection.now,
        worst,
        flash,
        sum(d <= COVER_KM for d in known),
        (
            *gaps,
            *some(stale, "stale forecast", "ignored"),
            *some(len(missing) - far, "forecast area", "unavailable"),
        ),
        watch=Watch(risen.gauge_id, risen.severity, round(km(risen), 1)) if risen else None,
        nearest_gauge_km=round(known[0], 1) if known else None,
        notes=(
            *some(far_stale, "stale forecast", beyond),
            *some(far, "forecast area", f"unavailable {beyond}"),
        ),
        areas={pid: (digests or {}).get(pid) for pid in selection.polygon_ids if inside(pid)},
    )


def _fresh(status: Status, now: datetime) -> bool:
    return status.issued is not None and now - status.issued <= STALE


def some(n: int, noun: str, state: str) -> list[str]:
    return [f"{n} {noun}{'s' * (n != 1)} {state}"] if n else []


def _areas(status: Status) -> list[tuple[str, str]]:
    alert = [(ALERT_AREA, status.alert_polygon_id)] if status.alert_polygon_id else []
    return [(level, pid) for level, pid in status.maps if level in _LEVELS] + alert


def _rank(hit: Riverine) -> tuple[int, int]:
    known = hit.severity in _SEVERITIES
    return _SEVERITIES.index(hit.severity) if known else len(_SEVERITIES), _LEVELS.index(hit.level)


def _km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    a = (
        sin(radians(lat2 - lat1) / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(radians(lng2 - lng1) / 2) ** 2
    )
    return 12742.0 * asin(sqrt(a))
