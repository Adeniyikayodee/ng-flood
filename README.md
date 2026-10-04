# ng-flood

Google's flood forecast for a Nigerian coordinate, labelled with its NIPOST
postcode area.

![ng-flood checking one coordinate with its lead time, then a CSV of three sites](docs/demo.gif)

The recording shows the real program on sample data.

## How it works

```mermaid
flowchart LR
    A[Coordinate] --> B[NIPOST reverse geocode]
    A --> C[Google riverine forecasts<br/>gauges within 150 km]
    A --> D[Google flash flood forecasts]
    C --> E[Is the point inside<br/>a forecast area?]
    D --> E
    B --> F[Postcode area label]
    E --> G{Verdict}
    F --> G
    G --> H[FORECAST]
    G --> I[NONE]
    G --> J[UNKNOWN<br/>anything stale or missing]
```

## Use it

Nothing to install: [uv](https://docs.astral.sh/uv/) fetches and runs it.

```sh
export GOOGLE_FLOOD_API_KEY=...   # required
export NG_POSTCODE_API_KEY=...    # optional, adds the postcode area
uvx ng-flood 7.8023 6.7333        # add --json for machine output
```

For many sites, give a CSV with `id`, `lat` and `lng` columns. The forecasts
are downloaded once and each row becomes one JSON record, ready to append to a
log:

```sh
uvx ng-flood --sites households.csv >> triggers.jsonl
```

```json
{"id": "hh-0142", "verdict": "FORECAST", "lead_hours": 48.0, "checked": "2026-10-04T12:00:00+00:00",
 "riverine": {"gauge_id": "...", "level": "HIGH", "severity": "SEVERE", "polygon_id": "...",
              "issued": "...", "starts": "...", "ends": "...", "change_m": [0.4, 0.9]}, "...": "..."}
```

Each record names the gauge, the forecast area, when Google issued it and when
it was checked, so a decision made on it can be audited later. `lead_hours` is
the time until the riverine forecast window opens. A row that cannot be read
gets an `error` and does not stop the rest.

From an AI assistant, as an MCP server with one tool, `check_flood_forecast`:

```json
{
  "mcpServers": {
    "ng-flood": {
      "command": "uvx",
      "args": ["--from", "ng-flood[mcp]", "ng-flood-mcp"],
      "env": { "GOOGLE_FLOOD_API_KEY": "...", "NG_POSTCODE_API_KEY": "..." }
    }
  }
}
```

From Python, after `pip install ng-flood`:

```python
from ng_flood import Client, Failure

with Client(google_key, nipost_key) as client:
    result = client.assess(7.8023, 6.7333)
if not isinstance(result, Failure):
    print(result.verdict, result.area, result.gaps)
```

## What the verdict means

| Verdict | Meaning |
| --- | --- |
| `FORECAST` | The point is inside a riverine or flash flood forecast area. |
| `NONE` | Fresh gauges are in range, every area was read, and none holds the point. |
| `UNKNOWN` | No gauge within 150 km, or something was stale, missing or unreadable. |

`NONE` is not a promise of safety. It says Google forecasts nothing here now.

## Limits

- Input is a coordinate. NIPOST does not return a coordinate for a postcode
  below its restricted lookup level, so a typed postcode cannot be checked.
- The postcode is a label, not a verification. Riverine maps are about 16 m
  cells with an F1 near 0.7, and flash flood cells are about 20 km, so the
  result is reported by area, not by building.
- Nothing from NIPOST or Google is cached or stored.
- With a NIPOST key, every coordinate is sent to NIPOST to find its postcode.
  Leave the key unset and no site location leaves your machine: Google's
  forecasts are downloaded whole and compared locally. Keep names out of the
  `id` column.
- The decoders follow Google's published schema. They have not yet been run
  against live responses: the API is behind a
  [waitlist](https://developers.google.com/flood-forecasting).

## Keys

- Google: join the waitlist, then enable the Flood Forecasting API on your
  Cloud project and create an API key.
- NIPOST: a free level 1 key from the
  [developer dashboard](https://dashboard.postcode.gov.ng).

## Develop

```sh
uv run --group dev ruff check . && uv run --group dev mypy && uv run --group dev pytest
```

## Attribution

Flood forecasts are from Google Flood Forecasting, licensed CC BY 4.0. Postcodes
are from NIPOST. This project is not affiliated with either, and is not an
official warning: follow NEMA and NIHSA.

MIT licensed.
