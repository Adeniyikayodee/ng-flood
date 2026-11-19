from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import box
from test_geometry import SQUARE, kml, polygon

from ng_flood import Assessment, Client, Failure, Watch, google
from ng_flood.basins import Basins
from ng_flood.cli import render

NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)
ISSUED = "2026-10-04T06:00:00Z"
LAT, LNG = 8.0, 7.0
Handler = Callable[[httpx.Request], httpx.Response]

STATUS: dict[str, Any] = {
    "gaugeId": "g1",
    "gaugeLocation": {"latitude": 8.1, "longitude": 7.1},
    "issuedTime": ISSUED,
    "severity": "SEVERE",
    "forecastTrend": "RISE",
    "qualityVerified": True,
    "source": "GRDC",
    "inundationMapSet": {
        "inundationMapType": "PROBABILITY",
        "inundationMaps": [
            {"level": "HIGH", "serializedPolygonId": "far"},
            {"level": "LOW", "serializedPolygonId": "here"},
        ],
    },
}
DRY = STATUS | {"inundationMapSet": {}, "severity": "NO_FLOODING"}
KML = {"here": kml(polygon(SQUARE)), "far": kml(polygon("0,0 1,0 1,1 0,1 0,0"))}
REVERSE = {
    "found": True,
    "unit": {"postcode": "KG-05-A12-FK-01", "display": "KG 05 A12 FK 01"},
    "area": "KG-05-A12-FK",
}


def server(
    statuses: list[Any] | None = None,
    flashes: list[Any] | None = None,
    broken: tuple[str, ...] = (),
) -> Handler:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if any(part in path for part in broken):
            return httpx.Response(503)
        if request.url.host == "api.postcode.gov.ng":
            assert request.headers["X-API-Key"] == "nipost"
            return httpx.Response(200, json={"data": REVERSE})
        assert request.headers["X-Goog-Api-Key"] == "google"
        if path.endswith("searchLatestFloodStatusByArea"):
            home = b'"regionCode":"NG"' in request.content
            return httpx.Response(200, json={"floodStatuses": (statuses or []) if home else []})
        if path.endswith("flashFloods:search"):
            return httpx.Response(200, json={"flashFloodEvents": flashes or []})
        return httpx.Response(200, json={"kml": KML[path.rsplit("/", 1)[-1]]})

    return handle


def run(handler: Handler, lat: float = LAT, nipost: str | None = "nipost") -> Assessment | Failure:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    with Client("google", nipost, http=http) as client:
        return client.assess(lat, LNG, NOW)


def ok(result: Assessment | Failure) -> Assessment:
    assert isinstance(result, Assessment), result
    return result


def test_reports_the_highest_area_that_holds_the_point() -> None:
    found = ok(run(server([STATUS])))
    assert found.verdict == "FORECAST"
    assert found.riverine is not None
    assert (found.riverine.level, found.riverine.severity) == ("LOW", "SEVERE")
    assert (found.area, found.postcode) == ("KG-05-A12-FK", "KG-05-A12-FK-01")
    assert (found.state, found.lga, found.lga_pcode) == ("Kogi", "Lokoja", "NG023012")
    assert "LGA:      Lokoja, Kogi" in render(found)
    assert "Point:    lat 8, lng 7" in render(found)
    assert ok(run(server([STATUS]), lat=9.5)).lga is None
    assert found.gaps == ()
    assert "KG-05-A12-FK" in render(found)
    assert "not town rain" in render(found)
    assert found.as_dict()["hazards"] == ["riverine", "flash"]


def test_severity_outranks_level_across_gauges() -> None:
    mild = STATUS | {"gaugeId": "g2", "severity": "ABOVE_NORMAL"}
    mild["inundationMapSet"] = {
        "inundationMaps": [{"level": "HIGH", "serializedPolygonId": "here"}]
    }
    found = ok(run(server([mild, STATUS]))).riverine
    assert found is not None
    assert (found.gauge_id, found.level) == ("g1", "LOW")


def test_an_alert_area_is_a_watch_not_a_forecast() -> None:
    maps = {"inundationMaps": [{"level": "LOW", "serializedPolygonId": "far"}]}
    status = STATUS | {"inundationMapSet": maps, "serializedNotificationPolygonId": "here"}
    found = ok(run(server([status])))
    assert (found.verdict, found.riverine) == ("WATCH", None)
    assert found.watch == Watch("g1", "SEVERE", 15.6, "here", verified=True)
    assert found.areas == {"here": sha256(KML["here"].encode()).hexdigest()}
    assert "its alert area holds the point" in render(found)
    gone = ok(run(server([status], broken=("serializedPolygons/here",))))
    assert (gone.verdict, gone.gaps) == ("UNKNOWN", ("1 forecast area unavailable",))


def test_a_gauge_that_forecasts_no_flooding_has_no_area() -> None:
    for severity in ("NO_FLOODING", "UNKNOWN"):
        quiet = STATUS | {"severity": severity, "serializedNotificationPolygonId": "here"}
        found = ok(run(server([quiet, DRY], broken=("serializedPolygons/",))))
        assert (found.riverine, found.gaps, found.areas) == (None, (), {})


def test_none_needs_a_fresh_gauge_in_range_and_no_gaps() -> None:
    assert ok(run(server([DRY]))).verdict == "NONE"
    assert ok(run(server([]))).verdict == "UNKNOWN"
    far = DRY | {"gaugeLocation": {"latitude": 12.5, "longitude": 13.0}}
    assert ok(run(server([far]))).verdict == "UNKNOWN"
    assert ok(run(server([DRY | {"severity": "UNKNOWN"}]))).verdict == "UNKNOWN"


def test_an_unverified_gauge_cannot_make_none() -> None:
    doubtful = DRY | {"qualityVerified": False, "gaugeId": "g2"}
    found = ok(run(server([doubtful])))
    assert (found.verdict, found.gauges, found.nearest_gauge_km) == ("UNKNOWN", 0, None)
    assert found.notes == ("1 unverified gauge not counted",)
    assert ok(run(server([doubtful, DRY]))).verdict == "NONE"
    assert ok(run(server([STATUS | {"qualityVerified": False}]))).verdict == "FORECAST"


def test_a_gauge_beyond_cover_does_not_make_none() -> None:
    there = DRY | {"gaugeLocation": {"latitude": 8.6, "longitude": 7.0}}
    found = ok(run(server([there])))
    assert (found.verdict, found.gauges, found.nearest_gauge_km) == ("UNKNOWN", 0, None)
    assert ok(run(server([there, DRY]))).nearest_gauge_km == 15.6
    severe = there | {"severity": "SEVERE"}
    assert ok(run(server([severe]))).watch is None


def test_a_gauge_upstream_or_just_downstream_covers_the_site() -> None:
    # 5 drains to 4, 4 and 3 drain to 2, 2 to 1: the site is in 4.
    basins = Basins(
        [
            (1, 0, box(6.5, 6.5, 7.5, 7.0)),
            (2, 1, box(6.5, 7.0, 7.5, 7.5)),
            (3, 2, box(7.5, 7.0, 8.5, 8.5)),
            (4, 2, box(6.5, 7.5, 7.5, 8.5)),
            (5, 4, box(6.5, 8.5, 7.5, 9.5)),
        ]
    )

    def gauge(lat: float, lng: float) -> dict[str, Any]:
        return DRY | {"gaugeLocation": {"latitude": lat, "longitude": lng}}

    for lat, lng, verdict in (
        (9.2, 7.0, "NONE"),
        (7.2, 7.0, "NONE"),
        (6.8, 7.0, "UNKNOWN"),
        (8.0, 8.0, "UNKNOWN"),
    ):
        http = httpx.Client(transport=httpx.MockTransport(server([gauge(lat, lng)])))
        with Client("google", http=http, basins=basins) as client:
            assert ok(client.assess(LAT, LNG, NOW)).verdict == verdict, (lat, lng)


def test_a_stale_forecast_is_a_gap() -> None:
    found = ok(run(server([STATUS | {"issuedTime": "2026-09-30T06:00:00Z"}])))
    assert (found.verdict, found.riverine) == ("UNKNOWN", None)
    assert found.gaps == ("1 stale forecast ignored",)


def test_a_gap_too_far_to_matter_is_only_a_note() -> None:
    there = {"gaugeLocation": {"latitude": 8.6, "longitude": 7.0}}
    old = DRY | there | {"issuedTime": "2026-09-30T06:00:00Z"}
    found = ok(run(server([DRY] * 30 + [old, STATUS | there], broken=("serializedPolygons/",))))
    assert (found.verdict, found.gaps) == ("NONE", ())
    assert found.notes == (
        "1 stale forecast on other rivers",
        "2 forecast areas unavailable on other rivers",
    )
    assert "Note:     1 stale" in render(found)


def test_a_missing_polygon_is_a_gap_not_dry_land() -> None:
    found = ok(run(server([STATUS], broken=("serializedPolygons/here",))))
    assert found.verdict == "UNKNOWN"
    assert found.gaps == ("1 forecast area unavailable",)


def test_an_elevated_gauge_without_a_map_is_a_watch() -> None:
    found = ok(run(server([STATUS | {"inundationMapSet": {}}, DRY])))
    assert found.verdict == "WATCH"
    assert found.watch == Watch("g1", "SEVERE", 15.6, verified=True)
    assert "g1 is SEVERE 15.6 km away and has no map" in render(found)
    assert found.as_dict()["watch"]["severity"] == "SEVERE"
    assert ok(run(server([STATUS]), lat=9.5)).watch is None


def test_an_unreadable_status_is_a_gap() -> None:
    found = ok(run(server([DRY, {"gaugeId": "no-location"}])))
    assert found.verdict == "UNKNOWN"
    assert found.gaps == ("1 riverine forecast unreadable",)


def test_a_gauge_is_unverified_unless_google_says_so() -> None:
    found = ok(run(server([STATUS]))).riverine
    assert found is not None
    assert (found.verified, found.source) == (True, "GRDC")
    page = google.statuses({"floodStatuses": [STATUS | {"qualityVerified": "true"}]})
    assert page is not None
    assert page.items[0].verified is False


def test_a_number_that_is_not_finite_is_unreadable() -> None:
    at = {"latitude": float("nan"), "longitude": 7.1}
    page = google.statuses({"floodStatuses": [STATUS | {"gaugeLocation": at}]})
    assert page is not None
    assert (page.items, page.skipped) == ((), 1)


def test_flash_floods_report_the_stronger_area() -> None:
    event = {
        "forecastIssueTime": ISSUED,
        "forecastPeriodHours": 24,
        "likelyAffectedPolygonId": "here",
        "highlyLikelyAffectedPolygonId": "far",
    }
    assert ok(run(server(flashes=[event]))).flash == "LIKELY"
    both = event | {"highlyLikelyAffectedPolygonId": "here"}
    assert ok(run(server(flashes=[both]))).flash == "HIGHLY_LIKELY"


def test_an_expired_flash_forecast_is_not_a_gap() -> None:
    expired = {
        "forecastIssueTime": ISSUED,
        "forecastPeriodHours": 1,
        "likelyAffectedPolygonId": "x",
    }
    found = ok(run(server([DRY], flashes=[expired])))
    assert (found.verdict, found.flash, found.gaps) == ("NONE", None, ())
    del expired["forecastPeriodHours"]
    assert ok(run(server([DRY], flashes=[expired]))).gaps == ("1 flash forecast unreadable",)


def test_flash_outage_keeps_the_riverine_answer() -> None:
    found = ok(run(server([STATUS], broken=("flashFloods",))))
    assert found.verdict == "FORECAST"
    assert found.gaps == ("flash floods unavailable: HTTP 503 from /v1/flashFloods:search",)


def test_a_neighbour_outage_is_a_gap() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if b'"CM"' in request.content:
            return httpx.Response(503)
        return server([STATUS])(request)

    found = ok(run(handle))
    assert found.verdict == "FORECAST"
    assert found.gaps == (
        "riverine forecasts for CM unavailable: "
        "HTTP 503 from /v1/floodStatus:searchLatestFloodStatusByArea",
    )


def test_riverine_outage_is_a_failure() -> None:
    assert run(server(broken=("floodStatus",))) == Failure(
        "HTTP 503 from /v1/floodStatus:searchLatestFloodStatusByArea"
    )


def test_postcode_outage_keeps_the_forecast() -> None:
    found = ok(run(server([STATUS], broken=("/v1/search/reverse",))))
    assert (found.verdict, found.area, found.postcode) == ("FORECAST", None, None)
    assert found.notes == ("postcode lookup failed: malformed_response",)
    assert ok(run(server([STATUS]), nipost=None)).area is None


def test_a_site_far_from_any_building_says_so() -> None:
    def empty(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.postcode.gov.ng":
            return httpx.Response(200, json={"data": {"found": False}})
        return server([STATUS])(request)

    found = ok(run(empty))
    assert (found.verdict, found.area) == ("FORECAST", None)
    assert found.notes == ("no postcode within 250 m",)


def test_outside_nigeria_makes_no_call() -> None:
    def never(request: httpx.Request) -> httpx.Response:
        raise AssertionError(request.url)

    assert run(never, lat=51.5) == Failure("not a coordinate in Nigeria")
    assert run(never, lat=float("nan")) == Failure("lat or lng is not a number")


def test_the_border_decides_what_is_nigeria() -> None:
    with Client("google", http=httpx.Client(transport=httpx.MockTransport(server()))) as client:
        assert client.assess(6.0, 12.0, NOW) == Failure("not a coordinate in Nigeria")  # Cameroon
        for lat, lng in ((6.45, 3.39), (4.32, 6.2), (13.7, 13.3), (6.38, 2.72)):  # the edges
            assert isinstance(client.assess(lat, lng, NOW), Assessment)


def test_follows_pages_and_names_no_key_on_network_failure() -> None:
    calls: list[bytes] = []

    def paged(request: httpx.Request) -> httpx.Response:
        if b'"NG"' in request.content:
            calls.append(request.content)
            more = {"nextPageToken": "t"} if len(calls) == 1 else {}
            return httpx.Response(200, json={"floodStatuses": [STATUS], **more})
        return server()(request)

    assert ok(run(paged)).gauges == 2
    assert b'"pageToken":"t"' in calls[1]

    def endless(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"floodStatuses": [], "nextPageToken": "t"})

    assert str(run(endless)).startswith("more than 100 pages")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused google", request=request)

    assert run(down) == Failure("ConnectError")


def test_retries_a_busy_server_then_gives_up(waits: list[float]) -> None:
    replies = [httpx.Response(503), httpx.Response(429, headers={"Retry-After": "5"})]

    def busy(request: httpx.Request) -> httpx.Response:
        if "floodStatus" in request.url.path and replies:
            return replies.pop(0)
        return server([STATUS])(request)

    assert ok(run(busy)).verdict == "FORECAST"
    assert waits == [0.5, 5.0]
    waits.clear()
    assert isinstance(run(server(broken=("floodStatus",))), Failure)
    assert waits == [0.5, 2.0]
    waits.clear()
    assert isinstance(run(lambda request: httpx.Response(403)), Failure)
    assert waits == []


def test_reports_lead_time_and_the_evidence_for_an_audit() -> None:
    window = {"start": "2026-10-06T12:00:00Z", "end": "2026-10-07T12:00:00Z"}
    change = {"valueChange": {"lowerBound": 0.4, "upperBound": 0.9}}
    found = ok(run(server([STATUS | {"forecastTimeRange": window, "forecastChange": change}])))
    assert found.riverine is not None
    assert (found.lead_hours, found.riverine.change_m) == (48.0, (0.4, 0.9))
    assert (found.riverine.polygon_id, found.checked) == ("here", NOW)
    record = found.as_dict()
    assert record["checked"] == "2026-10-04T12:00:00+00:00"
    assert record["riverine"]["starts"] == "2026-10-06T12:00:00+00:00"
    assert (record["lead_hours"], record["riverine"]["gauge_id"]) == (48.0, "g1")
    assert "Lead:     48 h" in render(found)


def test_lead_time_is_zero_once_the_window_opens_and_absent_without_one() -> None:
    opened = {"forecastTimeRange": {"start": "2026-10-04T00:00:00Z"}}
    assert ok(run(server([STATUS | opened]))).lead_hours == 0.0
    assert ok(run(server([STATUS]))).lead_hours is None
    assert "Lead:" not in render(ok(run(server([STATUS]))))


def test_a_batch_downloads_the_forecasts_once() -> None:
    paths: list[str] = []

    def counted(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return server([STATUS])(request)

    with Client("google", http=httpx.Client(transport=httpx.MockTransport(counted))) as client:
        results = client.assess_many([(LAT, LNG), (51.5, 0.0), (8.5, 7.5), (9.2, 7.0)], NOW)
    assert not isinstance(results, Failure)
    verdicts = [r.verdict if isinstance(r, Assessment) else str(r) for r in results]
    assert verdicts == ["FORECAST", "not a coordinate in Nigeria", "FORECAST", "UNKNOWN"]
    assert sorted(paths) == sorted(
        [
            *["/v1/floodStatus:searchLatestFloodStatusByArea"] * 4,
            "/v1/flashFloods:search",
            "/v1/serializedPolygons/far",
            "/v1/serializedPolygons/here",
        ]
    )


def test_an_endpoint_that_keeps_failing_is_left_alone() -> None:
    paths: list[str] = []
    maps = [{"level": "LOW", "serializedPolygonId": f"p{i}"} for i in range(5)]
    status = STATUS | {"inundationMapSet": {"inundationMaps": maps}}

    def counted(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return server([status], broken=("serializedPolygons", "reverse"))(request)

    http = httpx.Client(transport=httpx.MockTransport(counted))
    with Client("google", "nipost", http=http) as client:
        results = client.assess_many([(LAT, LNG)] * 5, NOW)
    assert isinstance(results, list)
    assert sorted({path.rsplit("/", 1)[-1] for path in paths if "Polygons" in path}) == [
        "p0",
        "p1",
        "p2",
    ]
    assert sum("reverse" in path for path in paths) == 3
    last = ok(results[-1])
    assert (last.verdict, last.gaps) == ("UNKNOWN", ("5 forecast areas unavailable",))
    assert last.notes == ("postcode lookup stopped after 3 failures",)


def test_evidence_is_saved_once_or_the_run_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def go(evidence: Path) -> list[Assessment | Failure] | Failure:
        http = httpx.Client(transport=httpx.MockTransport(server([STATUS])))
        with Client("google", http=http, evidence=evidence) as client:
            return client.assess_many([(LAT, LNG)] * 3, NOW)

    (tmp_path / "file").touch()
    assert str(go(tmp_path / "file")).startswith("evidence not saved: ")
    writes: list[str] = []
    monkeypatch.setattr(Path, "write_text", lambda self, *_, **__: writes.append(self.name))
    assert isinstance(go(tmp_path), list)
    assert len(writes) == 1
