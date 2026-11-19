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

from .basins import SOURCE
from .geometry import Polygon, contains
from .google import Flash, Status

RANGE_KM = 150.0
"""No gauge further away is read. Inundation maps reach tens of km from their gauge."""
STALE = timedelta(hours=48)
_LEVELS = ("HIGH", "MEDIUM", "LOW")
_SEVERITIES = ("EXTREME", "SEVERE", "ABOVE_NORMAL")
_KNOWN = (*_SEVERITIES, "NO_FLOODING")
HAZARDS = ("riverine", "flash")
"""What a verdict covers. Rain in towns, the sea and dam failures are not forecast."""
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
    """Stale gauges that cover the point, and others."""
    now: datetime
    cover: frozenset[Status] = frozenset()
    """Gauges in the point's basin, upstream of it, or in the basin it drains to."""

    @property
    def polygon_ids(self) -> tuple[str, ...]:
        return area_ids(self.statuses, self.flashes)


def area_ids(statuses: tuple[Status, ...], flashes: tuple[Flash, ...]) -> tuple[str, ...]:
    """Every area the forecasts name that could hold a point."""
    ids = [pid for s in statuses for pid in _ids(s)]
    ids += [pid for f in flashes for pid in (f.highly_likely_id, f.likely_id) if pid]
    return tuple(dict.fromkeys(ids))


@dataclass(frozen=True, slots=True)
class Riverine:
    gauge_id: str
    level: str
    """HIGH, MEDIUM or LOW inundation area."""
    severity: str
    trend: str | None
    map_type: str | None
    issued: datetime | None
    polygon_id: str
    """The forecast area that holds the point, for the audit trail."""
    starts: datetime | None
    ends: datetime | None
    change_m: tuple[float, float] | None
    verified: bool = False
    source: str | None = None


@dataclass(frozen=True, slots=True)
class Watch:
    """An elevated gauge with no inundation map holding the point: it has no map, or
    the point is only inside the area Google alerts people in."""

    gauge_id: str
    severity: str
    km: float
    alert_area: str | None = None
    verified: bool = False


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
    """Fresh, quality-verified gauges of known severity that cover the point."""
    gaps: tuple[str, ...]
    """What stops a NONE: anything stale, missing or unreadable that could reach the point."""
    postcode: str | None = None
    area: str | None = None
    """The NIPOST postcode area."""
    state: str | None = None
    lga: str | None = None
    lga_pcode: str | None = None
    """OCHA's P-code for the LGA, to join with humanitarian data."""
    watch: Watch | None = None
    nearest_gauge_km: float | None = None
    notes: tuple[str, ...] = ()
    """What did not change the verdict: gaps too far away, and unverified gauges."""
    areas: Mapping[str, str | None] = field(default_factory=dict)
    """Every forecast area that holds the point, by id, with the SHA-256 of its KML."""
    snapshot: str | None = None
    """SHA-256 of the saved forecasts judged, which replay the verdict exactly."""

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
        return data | {"verdict": self.verdict, "lead_hours": self.lead_hours} | judged()


def judged() -> dict[str, Any]:
    """The version and thresholds behind a verdict, and the notice to pass on."""
    return {
        "version": version("ng-flood"),
        "basins": SOURCE,
        "stale_hours": STALE.total_seconds() / 3600,
        "hazards": list(HAZARDS),
        "notice": NOTICE,
    }


FIELDS = frozenset(Assessment.__dataclass_fields__) | {"verdict", "lead_hours", *judged()}
"""Every key of a record."""


def select(
    lat: float,
    lng: float,
    statuses: tuple[Status, ...],
    flashes: tuple[Flash, ...],
    now: datetime,
    cover: frozenset[Status] = frozenset(),
) -> Selection:
    near = [s for s in statuses if _km(lat, lng, s.lat, s.lng) <= RANGE_KM]
    fresh = tuple(s for s in near if _fresh(s, now))
    old = [s in cover for s in near if not _fresh(s, now)]
    live = tuple(f for f in flashes if now <= f.issued + timedelta(hours=f.period_hours))
    return Selection(fresh, live, (sum(old), len(old) - sum(old)), now, cover)


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
            s.verified,
            s.source,
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
    close = [s for s in selection.statuses if s in selection.cover]
    near_ids = {pid for s in close for pid in _ids(s)}
    far_ids = {pid for s in selection.statuses for pid in _ids(s)} - near_ids
    missing = [pid for pid in selection.polygon_ids if polygons.get(pid) is None]
    far = sum(pid in far_ids for pid in missing)
    stale, far_stale = selection.stale
    beyond = "on other rivers"
    worst = min(hits, key=_rank, default=None)
    known = sorted(km(s) for s in close if s.severity in _KNOWN and s.verified)
    unverified = sum(s.severity in _KNOWN and not s.verified for s in close)
    alerts = {s.gauge_id: pid for s in selection.statuses if (pid := _alert(s)) and inside(pid)}
    blind = [s for s in close if s.severity in _SEVERITIES and not _areas(s)]
    risen = min(
        [s for s in selection.statuses if s.gauge_id in alerts] + blind,
        key=lambda s: (_SEVERITIES.index(s.severity), s.gauge_id not in alerts, km(s)),
        default=None,
    )
    return Assessment(
        lat,
        lng,
        selection.now,
        worst,
        flash,
        len(known),
        (
            *gaps,
            *some(stale, "stale forecast", "ignored"),
            *some(len(missing) - far, "forecast area", "unavailable"),
        ),
        watch=(
            Watch(
                risen.gauge_id,
                risen.severity,
                round(km(risen), 1),
                alerts.get(risen.gauge_id),
                risen.verified,
            )
            if risen
            else None
        ),
        nearest_gauge_km=round(known[0], 1) if known else None,
        notes=(
            *some(far_stale, "stale forecast", beyond),
            *some(far, "forecast area", f"unavailable {beyond}"),
            *some(unverified, "unverified gauge", "not counted"),
        ),
        areas={pid: (digests or {}).get(pid) for pid in selection.polygon_ids if inside(pid)},
    )


def _fresh(status: Status, now: datetime) -> bool:
    return status.issued is not None and now - status.issued <= STALE


def some(n: int, noun: str, state: str) -> list[str]:
    return [f"{n} {noun}{'s' * (n != 1)} {state}"] if n else []


def _areas(status: Status) -> list[tuple[str, str]]:
    """Areas of a gauge that forecasts flooding; any other gauge's areas are not a forecast."""
    if status.severity not in _SEVERITIES:
        return []
    return [(level, pid) for level, pid in status.maps if level in _LEVELS]


def _alert(status: Status) -> str | None:
    """The area Google alerts people in: wider than any map, so never a forecast of water."""
    return status.alert_polygon_id if status.severity in _SEVERITIES else None


def _ids(status: Status) -> list[str]:
    return [pid for _, pid in _areas(status)] + [pid for pid in [_alert(status)] if pid]


def _rank(hit: Riverine) -> tuple[int, int]:
    return _SEVERITIES.index(hit.severity), _LEVELS.index(hit.level)


def _km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    a = (
        sin(radians(lat2 - lat1) / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(radians(lng2 - lng1) / 2) ** 2
    )
    return 12742.0 * asin(sqrt(a))
