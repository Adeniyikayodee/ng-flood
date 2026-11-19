from __future__ import annotations

import gzip
import json
import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_client import KML, REVERSE, STATUS, Handler, server

from ng_flood import Assessment, Client, Failure, Riverine
from ng_flood.assess import FIELDS
from ng_flood.cli import Site, main, read_sites, records, roll_up
from ng_flood.snapshot import Snapshot

NOW = datetime(2026, 10, 4, tzinfo=UTC)
STAMP = "2026-10-04T00:00:00+00:00"
CSV = "id,lat,lng,village\nhh-1,8.0,7.0,Lokoja\nhh-2,north,7.0,Lokoja\n"
FAILED = Failure("not a coordinate in Nigeria")
HIT = Riverine("g1", "LOW", "SEVERE", None, None, None, "here", None, None, None)


def test_reads_sites_and_keeps_a_bad_row_in_place() -> None:
    sites = read_sites(iter(CSV.splitlines()))
    assert isinstance(sites, list)
    assert sites[0] == ("hh-1", 8.0, 7.0, {})
    assert read_sites(iter(CSV.splitlines()), ["village"])[0][3] == {"village": "Lokoja"}
    assert sites[1][0] == "hh-2"
    assert math.isnan(sites[1][1])


def test_a_csv_without_the_columns_is_refused() -> None:
    lines = ["id,lat,lng", "a,8.0,7.0"]
    assert read_sites(iter(lines), ["ward"]) == "the CSV has no column named ward"
    assert read_sites(iter(["name,lat", "a,8.0"])) == "the CSV has no column named id or lng"


def test_each_site_gets_one_record_with_its_id() -> None:
    dry = Assessment(8.0, 7.0, NOW, None, None, 1, ())
    sites: list[Site] = [("hh-1", 8.0, 7.0, {"ward": "A"}), ("hh-2", math.nan, 7.0, {})]
    first, second = records(sites, [dry, Failure("not a coordinate in Nigeria")], NOW)
    assert (first["id"], first["verdict"], first["lead_hours"]) == ("hh-1", "NONE", None)
    assert first["ward"] == "A"
    assert set(dry.as_dict()) == FIELDS
    assert second == {"id": "hh-2", "error": "not a coordinate in Nigeria", "checked": STAMP}


def test_sites_roll_up_by_postcode_area_and_none_is_lost() -> None:
    dry = Assessment(8.0, 7.0, NOW, None, None, 1, (), area="KG-05")
    blind = replace(dry, gauges=0)
    soon = replace(HIT, starts=NOW + timedelta(hours=6))
    wet = replace(dry, riverine=soon, area="KG-07")
    rows = roll_up([dry, blind, wet, replace(wet, flash="LIKELY", riverine=None), FAILED], NOW)
    assert [(r["area"], r["sites"], r["verdict"], r["errors"]) for r in rows] == [
        ("KG-07", 2, "FORECAST", 0),
        ("KG-05", 2, "UNKNOWN", 0),
        (None, 1, None, 1),
    ]
    assert (rows[0]["FORECAST"], rows[0]["lead_hours"], rows[0]["checked"]) == (2, 6.0, STAMP)
    assert (rows[1]["NONE"], rows[1]["UNKNOWN"], rows[1]["lead_hours"]) == (1, 1, None)
    assert (rows[2]["version"], rows[2]["basins"]) == ("0.1.0", "HydroBASINS v1c level 8")


def test_a_batch_run_from_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def go(handler: Handler, *flags: str) -> tuple[int, list[dict[str, Any]]]:
        http = httpx.Client(transport=httpx.MockTransport(handler))
        monkeypatch.setattr("ng_flood.cli.Client", partial(Client, http=http))
        code = main(["--sites", str(sites), "--evidence", str(tmp_path / "kml"), *flags], NOW)
        return code, [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    monkeypatch.setenv("GOOGLE_FLOOD_API_KEY", "google")
    monkeypatch.setenv("NG_POSTCODE_API_KEY", "nipost")
    sites = tmp_path / "sites.csv"
    sites.write_text(CSV)
    code, rows = go(server(broken=("floodStatus",)))
    assert (code, [row["id"] for row in rows]) == (1, ["hh-1", "hh-2"])
    assert all(row["error"].startswith("HTTP 503") and row["checked"] for row in rows)
    code, rows = go(server([STATUS]))
    assert (code, rows[0]["verdict"], rows[1]["error"]) == (
        0,
        "FORECAST",
        "not a coordinate in Nigeria",
    )
    assert rows[0]["areas"] == {"here": sha256(KML["here"].encode()).hexdigest()}
    assert (tmp_path / "kml" / f"{rows[0]['areas']['here']}.kml").read_text() == KML["here"]
    assert (rows[0]["version"], rows[0]["stale_hours"]) == ("0.1.0", 48)
    assert rows[0]["area"] is None
    assert go(server([STATUS]), "--postcode")[1][0]["area"] == "KG-05-A12-FK"
    code, rows = go(server([STATUS]), "--by", "postcode")
    assert (code, [(row["area"], row["sites"], row["errors"]) for row in rows]) == (
        0,
        [(REVERSE["area"], 1, 0), (None, 1, 1)],
    )
    log = tmp_path / "log.jsonl"
    for _ in range(2):
        go(server([STATUS]), "--by", "postcode", "--records", str(log))
    logged = [json.loads(line) for line in log.read_text().splitlines()]
    assert [(row["id"], row.get("verdict")) for row in logged] == [
        ("hh-1", "FORECAST"),
        ("hh-2", None),
    ] * 2
    assert go(server([STATUS]), "--by", "postcode", "--records", str(tmp_path / "no" / "log")) == (
        2,
        [],
    )
    monkeypatch.delenv("NG_POSTCODE_API_KEY")
    code, rows = go(server([STATUS]), "--by", "lga")
    assert (code, [(r["area"], r["state"], r["lga"], r["sites"]) for r in rows]) == (
        0,
        [("NG023012", "Kogi", "Lokoja", 1), (None, None, None, 1)],
    )
    assert [r["area"] for r in go(server([STATUS]), "--by", "state")[1]] == ["Kogi", None]
    wards = tmp_path / "wards.geojson"
    square = [[[6, 7], [8, 7], [8, 9], [6, 9], [6, 7]]]
    geometry = {"type": "Polygon", "coordinates": square}
    wards.write_text(
        json.dumps({"features": [{"properties": {"ward": "W1"}, "geometry": geometry}]})
    )
    code, rows = go(server([STATUS]), "--by", "ward", "--areas", str(wards), "--records", str(log))
    assert (code, [(r["area"], r["sites"]) for r in rows]) == (0, [("W1", 1), (None, 1)])
    assert json.loads(log.read_text().splitlines()[-2])["ward"] == "W1"
    assert go(server([STATUS]), "--by", "name", "--areas", str(wards)) == (2, [])
    assert go(server([STATUS]), "--postcode") == (2, [])
    assert go(server([STATUS]), "--by", "postcode") == (2, [])
    sites.write_text("id,lat,lng\nhh-2,north,7.0\n")
    assert go(server([STATUS]))[0] == 1


def test_fetch_saves_a_whole_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def go(handler: Handler, name: str) -> tuple[int, Path]:
        http = httpx.Client(transport=httpx.MockTransport(handler))
        monkeypatch.setattr("ng_flood.cli.Client", partial(Client, http=http))
        return main(["--fetch", str(tmp_path / name)], NOW), tmp_path / name

    monkeypatch.setenv("GOOGLE_FLOOD_API_KEY", "google")
    code, path = go(server([STATUS]), "snap.json.gz")
    snapshot = Snapshot.loads(gzip.decompress(path.read_bytes()).decode())
    assert code == 0
    assert snapshot is not None
    assert (snapshot.fetched, snapshot.complete, sorted(snapshot.kml)) == (
        NOW,
        True,
        ["far", "here"],
    )
    assert go(server([STATUS]), "snap.json")[1].read_text() == snapshot.dumps()
    assert go(server(broken=("floodStatus",)), "down.json")[0] == 1
    assert not (tmp_path / "down.json").exists()
    assert go(server([STATUS]), "no/snap.json")[0] == 1
    monkeypatch.delenv("GOOGLE_FLOOD_API_KEY")
    assert go(server([STATUS]), "snap.json")[0] == 2
    with pytest.raises(SystemExit):
        main(["8.0", "7.0", "--fetch", "snap.json"])


def test_a_snapshot_is_judged_offline_without_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def never(request: httpx.Request) -> httpx.Response:
        raise AssertionError(request.url)

    monkeypatch.setenv("GOOGLE_FLOOD_API_KEY", "google")
    http = httpx.Client(transport=httpx.MockTransport(server([STATUS])))
    monkeypatch.setattr("ng_flood.cli.Client", partial(Client, http=http))
    snap = tmp_path / "snap.json.gz"
    assert main(["--fetch", str(snap)], NOW) == 0
    monkeypatch.delenv("GOOGLE_FLOOD_API_KEY")
    http = httpx.Client(transport=httpx.MockTransport(never))
    monkeypatch.setattr("ng_flood.cli.Client", partial(Client, http=http))
    capsys.readouterr()
    assert main(["8.0", "7.0", "--json", "--snapshot", str(snap)], NOW) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["verdict"] == "FORECAST"
    assert record["snapshot"] == sha256(gzip.decompress(snap.read_bytes())).hexdigest()
    late = (NOW + timedelta(days=3)).isoformat()
    assert main(["8.0", "7.0", "--json", "--snapshot", str(snap), "--at", late]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "UNKNOWN"
    with pytest.raises(SystemExit):
        main(["8.0", "7.0", "--snapshot", str(snap), "--at", "2026-10-04T00:00:00"])
    capsys.readouterr()
    (tmp_path / "bad.json").write_text("{}")
    for bad in ("bad.json", "missing.json", "snap.json.gz.bad"):
        assert main(["8.0", "7.0", "--snapshot", str(tmp_path / bad)], NOW) == 2
        assert "is not a snapshot" in capsys.readouterr().err
    assert main(["8.0", "7.0", "--postcode", "--snapshot", str(snap)], NOW) == 2
    assert "set NG_POSTCODE_API_KEY" in capsys.readouterr().err


def test_usage_errors_exit_2_before_any_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GOOGLE_FLOOD_API_KEY", raising=False)
    assert main(["8.0", "7.0"]) == 2
    assert main(["--sites", str(tmp_path / "missing.csv")]) == 2
    bad = tmp_path / "bad.csv"
    bad.write_text("name\na\n")
    assert main(["--sites", str(bad)]) == 2
    assert "no column named id or lat or lng" in capsys.readouterr().err
    bad.write_bytes(b"id,lat,lng\n\xff,8,7\n")
    assert main(["--sites", str(bad)]) == 2
    assert "is not a UTF-8 CSV" in capsys.readouterr().err
    for argv in (
        [],
        ["8.0"],
        ["8.0", "7.0", "--sites", str(bad)],
        ["8.0", "7.0", "--keep", "lga"],
        ["8.0", "7.0", "--by", "postcode"],
        ["--sites", str(bad), "--records", "log.jsonl"],
        ["--sites", str(bad), "--json"],
        ["--sites", str(bad), "--keep", "ward, area"],
        ["--sites", str(bad), "--by", "ward"],
        ["--sites", str(bad), "--areas", "wards.geojson"],
        ["--sites", str(bad), "--areas", "wards.geojson", "--by", "lga"],
    ):
        with pytest.raises(SystemExit):
            main(argv)
