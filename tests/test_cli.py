from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from functools import partial
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_client import KML, STATUS, Handler, server

from ng_flood import Assessment, Client, Failure
from ng_flood.cli import Site, main, read_sites, records

NOW = datetime(2026, 10, 4, tzinfo=UTC)
STAMP = "2026-10-04T00:00:00+00:00"
CSV = "id,lat,lng,village\nhh-1,8.0,7.0,Lokoja\nhh-2,north,7.0,Lokoja\n"


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
    sites: list[Site] = [("hh-1", 8.0, 7.0, {"lga": "Lokoja"}), ("hh-2", math.nan, 7.0, {})]
    first, second = records(sites, [dry, Failure("not a coordinate in Nigeria")], NOW)
    assert (first["id"], first["verdict"], first["lead_hours"]) == ("hh-1", "NONE", None)
    assert first["lga"] == "Lokoja"
    assert second == {"id": "hh-2", "error": "not a coordinate in Nigeria", "checked": STAMP}


def test_a_batch_run_from_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def go(handler: Handler, *flags: str) -> tuple[int, list[dict[str, Any]]]:
        http = httpx.Client(transport=httpx.MockTransport(handler))
        monkeypatch.setattr("ng_flood.cli.Client", partial(Client, http=http))
        code = main(["--sites", str(sites), "--evidence", str(tmp_path / "kml"), *flags])
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
    assert (rows[0]["version"], rows[0]["cover_km"], rows[0]["stale_hours"]) == ("0.1.0", 50, 48)
    assert rows[0]["area"] is None
    assert go(server([STATUS]), "--postcode")[1][0]["area"] == "KG-05-A12-FK"
    monkeypatch.delenv("NG_POSTCODE_API_KEY")
    assert go(server([STATUS]), "--postcode") == (2, [])
    sites.write_text("id,lat,lng\nhh-2,north,7.0\n")
    assert go(server([STATUS]))[0] == 1


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
    for argv in ([], ["8.0"], ["8.0", "7.0", "--sites", str(bad)], ["8.0", "7.0", "--keep", "lga"]):
        with pytest.raises(SystemExit):
            main(argv)
