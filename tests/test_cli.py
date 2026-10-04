from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ng_flood import Assessment, Failure
from ng_flood.cli import main, read_sites, records

CSV = "id,lat,lng,village\nhh-1,8.0,7.0,Lokoja\nhh-2,north,7.0,Lokoja\n"


def test_reads_sites_and_keeps_a_bad_row_in_place() -> None:
    sites = read_sites(iter(CSV.splitlines()))
    assert isinstance(sites, list)
    assert sites[0] == ("hh-1", 8.0, 7.0)
    assert sites[1][0] == "hh-2"
    assert math.isnan(sites[1][1])


def test_a_csv_without_the_columns_is_refused() -> None:
    assert read_sites(iter(["name,latitude", "a,8.0"])) == "the CSV needs id, lat and lng columns"


def test_each_site_gets_one_record_with_its_id() -> None:
    dry = Assessment(8.0, 7.0, datetime(2026, 10, 4, tzinfo=UTC), None, None, 1, ())
    sites = [("hh-1", 8.0, 7.0), ("hh-2", math.nan, 7.0)]
    first, second = records(sites, [dry, Failure("not a coordinate in Nigeria")])
    assert (first["id"], first["verdict"], first["lead_hours"]) == ("hh-1", "NONE", None)
    assert second == {"id": "hh-2", "error": "not a coordinate in Nigeria"}


def test_usage_errors_exit_2_before_any_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GOOGLE_FLOOD_API_KEY", raising=False)
    assert main(["8.0", "7.0"]) == 2
    assert main(["--sites", str(tmp_path / "missing.csv")]) == 2
    bad = tmp_path / "bad.csv"
    bad.write_text("name\na\n")
    assert main(["--sites", str(bad)]) == 2
    assert "needs id, lat and lng" in capsys.readouterr().err
    for argv in ([], ["8.0"], ["8.0", "7.0", "--sites", str(bad)]):
        with pytest.raises(SystemExit):
            main(argv)
