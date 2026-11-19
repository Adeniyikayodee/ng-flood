"""Render docs/demo.gif and docs/flow.gif. The demo runs the real ng-flood on a sample
snapshot, so no key is needed and every line on screen is the program's own output.

    uv run --with pillow scripts/record.py
"""

from __future__ import annotations

import gzip
import json
import random
import re
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ng_flood.areas import lgas

DOCS = Path(__file__).parent.parent / "docs"
FONTS = [
    "/System/Applications/Utilities/Terminal.app/Contents/Resources/Fonts/SF-Mono-Regular.otf",
    "/System/Library/Fonts/Menlo.ttc",
    "DejaVuSansMono.ttf",
]
BG, BAR, TITLE, TEXT, DIM, LINE = "#0d1117", "#161b22", "#8b949e", "#e6edf3", "#6e7681", "#30363d"
BLUE, GREEN = "#58a6ff", "#3fb950"
VERDICT = {"FORECAST": "#f85149", "WATCH": "#d29922", "NONE": "#3fb950", "UNKNOWN": "#8b949e"}
FILL = {"FORECAST": "#2d1a1a", "WATCH": "#2b2213", "NONE": "#14261a", "UNKNOWN": "#1c2128"}


def font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    raise SystemExit("no monospace font found")


# Sample data -------------------------------------------------------------------------


def snapshot(now: datetime) -> dict[str, object]:
    issued = (now - timedelta(hours=6)).isoformat()
    window = {
        "start": (now + timedelta(hours=48)).isoformat(),
        "end": (now + timedelta(hours=96)).isoformat(),
    }

    def gauge(gid: str, lat: float, lng: float, severity: str, **more: object) -> dict[str, object]:
        return {
            "gaugeId": gid,
            "gaugeLocation": {"latitude": lat, "longitude": lng},
            "issuedTime": issued,
            "severity": severity,
            "qualityVerified": True,
            "source": "HYBAS",
        } | more

    lokoja = gauge(
        "hybas_1080909890",
        7.83,
        6.75,
        "SEVERE",
        forecastTrend="RISE",
        forecastTimeRange=window,
        forecastChange={"valueChange": {"lowerBound": 0.4, "upperBound": 0.9}},
        inundationMapSet={
            "inundationMapType": "PROBABILITY",
            "inundationMaps": [{"level": "HIGH", "serializedPolygonId": "lokoja-high"}],
        },
    )
    benue = gauge("hybas_1080911340", 7.75, 8.70, "SEVERE", forecastTrend="RISE")
    kano = gauge("hybas_1080727850", 12.05, 8.50, "NO_FLOODING", forecastTrend="NO_CHANGE")
    band = "6.66,7.55 6.84,7.55 6.86,7.95 6.80,8.15 6.68,8.15 6.64,7.90 6.66,7.55"
    kml = (
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Polygon><outerBoundaryIs>'
        f"<LinearRing><coordinates>{band}</coordinates></LinearRing>"
        "</outerBoundaryIs></Polygon></Document></kml>"
    )
    return {
        "format": 1,
        "fetched": now.isoformat(),
        "statuses": {
            "NG": [{"floodStatuses": [lokoja, benue, kano]}],
            "BJ": [{}],
            "NE": [{}],
            "CM": [{}],
        },
        "flashes": [{}],
        "kml": {"lokoja-high": kml},
        "complete": True,
    }


def households() -> str:
    rows = [
        ("hh-0142", 7.8023, 6.7333),
        ("hh-0143", 9.0765, 7.3986),
        ("hh-0144", 12.0022, 8.5919),
        ("hh-0145", 7.7322, 8.5391),
    ]
    return "id,lat,lng\n" + "".join(f"{i},{lat},{lng}\n" for i, lat, lng in rows)


def kogi() -> str:
    """240 households in three LGAs, scattered inside each LGA's own boundary."""
    rng, rows = random.Random(7), []
    boxes = {
        "Lokoja": (7.55, 8.10, 6.62, 6.86, 96),
        "Ajaokuta": (7.40, 7.75, 6.55, 6.80, 57),
        "Dekina": (7.35, 7.90, 7.00, 7.35, 87),
    }
    for name, (s, n, w, e, count) in boxes.items():
        while sum(r[0].startswith(name[:3].lower()) for r in rows) < count:
            lat, lng = rng.uniform(s, n), rng.uniform(w, e)
            if (lga := lgas().at(lat, lng)) and lga.name == name:
                rows.append((f"{name[:3].lower()}-{len(rows):03d}", round(lat, 4), round(lng, 4)))
    return "id,lat,lng\n" + "".join(f"{i},{lat},{lng}\n" for i, lat, lng in rows)


def run(command: str, cwd: Path) -> list[str]:
    out = subprocess.run(command, shell=True, cwd=cwd, capture_output=True, text=True, check=True)
    return out.stdout.rstrip("\n").splitlines()


# Terminal demo -----------------------------------------------------------------------

W, H, TOP, LEFT, LH = 920, 430, 40, 22, 22
FOOT = "Sample data. A real run needs a Google Flood Forecasting API key."


def paint_line(draw: ImageDraw.ImageDraw, y: int, text: str, f: ImageFont.FreeTypeFont) -> None:
    x = LEFT
    if text.startswith("$ "):
        draw.text((x, y), "$", font=f, fill=GREEN)
        x, text = x + f.getlength("$ "), text[2:]
        draw.text((x, y), text, font=f, fill=TEXT)
        return
    for i, part in enumerate(re.split(r'((?<="verdict":")[A-Z]+|(?<=^Verdict:  )[A-Z]+)', text)):
        draw.text((x, y), part, font=f, fill=VERDICT.get(part, TEXT) if i % 2 else TEXT)
        x += f.getlength(part)


def chrome(
    title: str, progress: float, f: ImageFont.FreeTypeFont, h: int = H
) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (W, h), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, W, TOP), fill=BAR)
    draw.rectangle((0, 0, int(W * progress), 3), fill=BLUE)
    draw.text((W / 2, TOP / 2 + 1), title, font=f, fill=TITLE, anchor="mm")
    return image, draw


def demo(cwd: Path) -> None:
    f = font(14)
    scenes = [
        "ng-flood 7.8023 6.7333 --snapshot today.gz | head -6",
        "cat households.csv",
        "ng-flood --sites households.csv --snapshot today.gz | jq -c '{id, lga, verdict}'",
        "wc -l < kogi.csv",
        "ng-flood --sites kogi.csv --snapshot today.gz --by lga"
        " | jq -c '{lga, sites, verdict, FORECAST}'",
    ]
    screens = [[0], [1, 2], [3, 4]]
    outputs = [run(c, cwd) for c in scenes]
    steps: list[tuple[list[str], int]] = []
    for screen in screens:
        shown: list[str] = []
        for i in screen:
            for n in range(0, len(scenes[i]) + 1, 3):
                steps.append(([*shown, f"$ {scenes[i][:n]}"], 30))
            shown = [*shown, f"$ {scenes[i]}"]
            steps.append((shown, 400))
            shown = [*shown, *outputs[i], ""]
            steps.append((shown, 2200))
        steps[-1] = (shown, 3600)
    total = sum(ms for _, ms in steps)
    frames, durations, elapsed = [], [], 0
    for lines, ms in steps:
        for part in range(max(1, ms // 250)):
            image, draw = chrome("ng-flood", (elapsed + part * 250) / total, f)
            for row, text in enumerate(lines):
                paint_line(draw, TOP + 18 + row * LH, text, f)
            draw.text((LEFT, H - 28), FOOT, font=f, fill=DIM)
            frames.append(image)
            durations.append(min(ms, 250) if ms >= 250 else ms)
        elapsed += ms
    save(frames, durations, DOCS / "demo.gif")


# Diagram -----------------------------------------------------------------------------

FH = 500
NODES = {
    "point": ((20, 175, 130, 225), "Coordinate"),
    "lga": ((165, 65, 385, 115), "OCHA boundaries"),
    "label": ((420, 65, 610, 115), "State and LGA\nlabel"),
    "river": ((165, 170, 385, 230), "Google river forecasts\non the same river"),
    "flash": ((165, 280, 385, 330), "Google flash flood\nforecasts"),
    "inside": ((420, 225, 610, 280), "Is the point inside\na flood map?"),
    "verdict": ((645, 175, 735, 225), "Verdict"),
    "FORECAST": ((770, 60, 900, 105), "FORECAST"),
    "WATCH": ((770, 135, 900, 180), "WATCH"),
    "NONE": ((770, 210, 900, 255), "NONE"),
    "UNKNOWN": ((770, 285, 900, 330), "UNKNOWN"),
    "rows": ((645, 380, 900, 430), "One row for each LGA"),
}
EDGES = [
    ("point", "lga"),
    ("point", "river"),
    ("point", "flash"),
    ("lga", "label"),
    ("river", "inside"),
    ("flash", "inside"),
    ("label", "verdict"),
    ("inside", "verdict"),
    ("verdict", "FORECAST"),
    ("verdict", "WATCH"),
    ("verdict", "NONE"),
    ("verdict", "UNKNOWN"),
    ("verdict", "rows"),
]
STEPS = [
    ({"point"}, "Start with a place: a latitude and a longitude."),
    (
        {"lga", "river", "flash"},
        "The forecasts download once. Everything is checked on your machine.",
    ),
    (
        {"river"},
        "A gauge counts only if it is on the same river: same basin, upstream or just below.",
    ),
    ({"label", "inside"}, "Is the place inside a flood map? And which state and LGA is it in?"),
    ({"verdict"}, "Every place gets one of four answers."),
    ({"FORECAST"}, "FORECAST: a flood map covers this place."),
    ({"WATCH"}, "WATCH: a gauge on this river is rising, with no map for this place."),
    ({"NONE"}, "NONE: the gauges on this river are calm, and no map covers this place."),
    ({"UNKNOWN"}, "UNKNOWN: not enough fresh, trusted data to say. It never guesses."),
    ({"rows"}, "--by lga rolls every place up into one row for each LGA, worst first."),
]


def anchor(box: tuple[int, int, int, int], side: str) -> tuple[float, float]:
    x0, y0, x1, y1 = box
    return {"r": (x1, (y0 + y1) / 2), "l": (x0, (y0 + y1) / 2), "b": ((x0 + x1) / 2, y1)}[side]


def flow() -> None:
    f, small = font(13), font(13)
    frames, durations = [], []
    lit: set[str] = set()
    total = len(STEPS) * 1600 + 2400
    for step, (nodes, caption) in enumerate(STEPS):
        lit |= nodes
        for part in range(8 if step < len(STEPS) - 1 else 18):
            image, draw = chrome(
                "ng-flood: how it works", (step * 1600 + part * 200) / total, f, FH
            )
            for a, b in EDGES:
                on = a in lit and b in lit
                (ax, ay), (bx, by) = anchor(NODES[a][0], "r"), anchor(NODES[b][0], "l")
                if b == "rows":
                    ax, ay = anchor(NODES[a][0], "b")
                    bx, by = ax, NODES[b][0][1]
                colour = VERDICT.get(b, BLUE) if on else LINE
                draw.line((ax, ay, bx, by), fill=colour, width=2)
            for name, (box, text) in NODES.items():
                on = name in lit
                verdict = name in VERDICT
                edge = (VERDICT[name] if verdict else BLUE) if on else LINE
                fill = (FILL[name] if verdict else "#1c2433") if on else BG
                ink = (VERDICT[name] if verdict else TEXT) if on else DIM
                draw.rounded_rectangle(box, radius=7, fill=fill, outline=edge, width=2)
                centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
                draw.multiline_text(
                    centre, text, font=small, fill=ink, anchor="mm", align="center", spacing=5
                )
            draw.text((LEFT, FH - 30), caption, font=f, fill=TEXT)
            frames.append(image)
            durations.append(200)
    save(frames, durations, DOCS / "flow.gif")


def save(frames: list[Image.Image], durations: list[int], path: Path) -> None:
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=1,
    )
    print(path, len(frames), "frames", path.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        cwd = Path(tmp)
        (cwd / "today.gz").write_bytes(
            gzip.compress(json.dumps(snapshot(datetime.now(UTC))).encode())
        )
        (cwd / "households.csv").write_text(households())
        (cwd / "kogi.csv").write_text(kogi())
        demo(cwd)
    flow()
