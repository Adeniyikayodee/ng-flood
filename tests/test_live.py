"""The decoders against the real API. Skipped without GOOGLE_FLOOD_API_KEY.

Every response is saved under tests/live/ to become a fixture.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import httpx
import pytest

from ng_flood import Assessment, Client

KEY = os.environ.get("GOOGLE_FLOOD_API_KEY", "")
LIVE = Path(__file__).parent / "live"


def save(response: httpx.Response) -> None:
    response.read()
    name = re.sub(r"\W+", "-", response.request.url.path.removeprefix("/v1/"))
    LIVE.mkdir(exist_ok=True)
    (LIVE / f"{name}.json").write_bytes(response.content)


@pytest.mark.skipif(not KEY, reason="needs GOOGLE_FLOOD_API_KEY")
def test_the_decoders_read_live_responses() -> None:
    http = httpx.Client(timeout=30, event_hooks={"response": [save]})
    with http, Client(KEY, http=http) as client:
        found = client.assess(7.8023, 6.7333)  # Lokoja, where the Niger meets the Benue
    assert isinstance(found, Assessment), found
    assert not [gap for gap in found.gaps if "unreadable" in gap or "unavailable" in gap]
    assert found.nearest_gauge_km is not None
