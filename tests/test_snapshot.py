from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
from test_client import LAT, LNG, NOW, STATUS, Handler, server

from ng_flood import Assessment, Client
from ng_flood.snapshot import Snapshot


def client(handler: Handler) -> Client:
    return Client("google", http=httpx.Client(transport=httpx.MockTransport(handler)))


def never(request: httpx.Request) -> httpx.Response:
    raise AssertionError(request.url)


def test_a_whole_snapshot_is_judged_without_the_network() -> None:
    with client(server([STATUS])) as online:
        snapshot = online.fetch(NOW, whole=True)
        live = online.assess(LAT, LNG, NOW)
    assert isinstance(snapshot, Snapshot)
    assert sorted(snapshot.kml) == ["far", "here"]
    copy = Snapshot.loads(snapshot.dumps())
    assert copy == snapshot
    assert copy.digest() == snapshot.digest()
    with client(never) as offline:
        found = offline.assess_many([(LAT, LNG)], NOW, copy)
    assert isinstance(found, list)
    assert isinstance(found[0], Assessment)
    assert isinstance(live, Assessment)
    assert found[0] == replace(live, snapshot=snapshot.digest())
    assert live.snapshot is None


def test_a_snapshot_without_an_area_is_a_gap_not_a_call() -> None:
    with client(server([STATUS])) as online:
        snapshot = online.fetch(NOW, whole=True)
    assert isinstance(snapshot, Snapshot)
    del snapshot.kml["here"]
    with client(never) as offline:
        found = offline.assess_many([(LAT, LNG)], NOW, snapshot)
    assert isinstance(found, list)
    assert isinstance(found[0], Assessment)
    assert (found[0].verdict, found[0].gaps) == ("UNKNOWN", ("1 forecast area unavailable",))


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[]",
        '{"format": 1}',
        '{"format": 2, "fetched": "2026-10-04T12:00:00+00:00", "statuses": [], "flashes": [], '
        '"kml": {}}',
        '{"format": 1, "fetched": "2026-10-04T12:00:00", "statuses": [], "flashes": [], "kml": {}}',
        '{"format": 1, "fetched": "2026-10-04T12:00:00Z", "statuses": {}, "flashes": [], '
        '"kml": {}}',
        '{"format": 1, "fetched": "2026-10-04T12:00:00Z", "statuses": [], "flashes": [], '
        '"kml": {"a": 1}}',
    ],
)
def test_a_file_that_is_not_a_snapshot_is_refused(text: str) -> None:
    assert Snapshot.loads(text) is None


def test_a_snapshot_with_a_bad_page_fails_the_batch() -> None:
    snapshot = Snapshot(NOW, [{"floodStatuses": "x"}], [])
    with client(never) as offline:
        assert str(offline.assess_many([(LAT, LNG)], NOW, snapshot)).startswith("the snapshot")
