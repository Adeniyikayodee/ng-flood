"""Google's raw answers for Nigeria, kept whole so any point can be judged offline and any
verdict replayed. Pure: no I/O."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Any, TypeVar

from . import google
from .assess import some

T = TypeVar("T")

FORMAT = 1


@dataclass(slots=True)
class Snapshot:
    fetched: datetime
    statuses: list[Any]
    """Raw pages of riverine forecasts."""
    flashes: list[Any] | str
    """Raw pages of flash flood forecasts, or why there are none."""
    kml: dict[str, str | None] = field(default_factory=dict)
    """Areas by polygon id; None when one could not be fetched."""
    complete: bool = False
    """Holds every area its forecasts name, so nothing more is fetched."""

    def dumps(self) -> str:
        data = {
            "format": FORMAT,
            "fetched": self.fetched.isoformat(),
            "statuses": self.statuses,
            "flashes": self.flashes,
            "kml": self.kml,
            "complete": self.complete,
        }
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        return sha256(self.dumps().encode()).hexdigest()

    @classmethod
    def loads(cls, text: str) -> Snapshot | None:
        try:
            data = json.loads(text)
            fetched = datetime.fromisoformat(data["fetched"])
        except (ValueError, TypeError, KeyError):
            return None
        statuses, flashes, kml = data.get("statuses"), data.get("flashes"), data.get("kml")
        if (
            data.get("format") != FORMAT
            or fetched.tzinfo is None
            or not isinstance(statuses, list)
            or not isinstance(flashes, list | str)
            or not isinstance(kml, dict)
            or not all(isinstance(v, str | None) for v in kml.values())
        ):
            return None
        return cls(fetched, statuses, flashes, kml, data.get("complete") is True)


@dataclass(frozen=True, slots=True)
class Forecasts:
    statuses: tuple[google.Status, ...]
    flashes: tuple[google.Flash, ...]
    gaps: tuple[str, ...]
    """Records that could not be read, and flash floods that could not be fetched."""


def read(snapshot: Snapshot) -> Forecasts | None:
    """Decode a snapshot. None when a page is not what Google sends."""
    statuses = _items(snapshot.statuses, google.statuses)
    flashes = (
        ((), 0) if isinstance(snapshot.flashes, str) else _items(snapshot.flashes, google.flashes)
    )
    if statuses is None or flashes is None:
        return None
    gaps = some(statuses[1], "riverine forecast", "unreadable")
    if isinstance(snapshot.flashes, str):
        gaps.append(f"flash floods unavailable: {snapshot.flashes}")
    gaps += some(flashes[1], "flash forecast", "unreadable")
    return Forecasts(statuses[0], flashes[0], tuple(gaps))


def _items(
    pages: list[Any], read: Callable[[Any], google.Page[T] | None]
) -> tuple[tuple[T, ...], int] | None:
    items: tuple[T, ...] = ()
    skipped = 0
    for raw in pages:
        page = read(raw)
        if page is None:
            return None
        items, skipped = items + page.items, skipped + page.skipped
    return items, skipped
