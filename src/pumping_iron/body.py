"""Block 2: body measurements, their dynamics and the labelled human figure.

The figure is the front-view muscle map from body_figure.py: every measured body part
colours its region (green = changed in the wanted direction, red = the opposite).

  GET  /body           figure with the latest value and the change in percent next to
                       every measured body part, weight and body fat, and a full table
  POST /body/measure   save one or more measurements for a date (re-entering overwrites)

The change is the latest value against a baseline value from `period` days earlier,
counted back from the date of the latest measurement of that type (not from today),
so a pause in measuring does not empty the page.
"""

import sqlite3
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from pumping_iron.body_figure import POLYGONS
from pumping_iron.db import get_db
from pumping_iron.web import page

router = APIRouter(prefix="/body")

Db = Annotated[sqlite3.Connection, Depends(get_db)]

# (period in days, label); 0 means "since the first measurement".
PERIODS = [(30, "30 дней"), (90, "90 дней"), (365, "Год"), (0, "Всё время")]
DEFAULT_PERIOD = 90

# The figure box: the body drawing (SVG viewBox 1000 x 1980, polygons from body_figure.py)
# fills the middle part of it, the labels sit in the side columns. These numbers are shared
# with style.css (.body-figure and .fig-body use the same proportions).
FIG_W, FIG_H = 1000, 1980
BODY_LEFT, BODY_WIDTH = 28.3, 43.4                  # % of the box width
BOX_RATIO = BODY_WIDTH / 100 * FIG_H / FIG_W        # box height / box width, 0.8593
LABEL_EDGE = {"left": 28, "right": 72}              # x (%) where a leader line leaves the label column

# key: (muscle regions it colours, side, label column, label y %, short label).
# side: "r" / "l" = the person's right / left (the figure faces the viewer, so "r" is on the
# viewer's left), None = both sides as one region. Label rows are at least 13 % apart so
# two-line labels never overlap, and the order along each column follows the body from top
# to bottom so leader lines do not cross. The left column holds the person's right side.
FIGURE = {
    "neck":      (("NECK",), None, "left", 8, "Шея"),
    "shoulders": (("FRONT_DELTOIDS",), None, "left", 21, "Плечи"),
    "biceps_r":  (("BICEPS",), "r", "left", 34, "Бицепс П"),
    "forearm_r": (("FOREARM",), "r", "left", 47, "Предплечье П"),
    "hips":      (("ABDUCTORS",), None, "left", 60, "Бёдра"),
    "thigh_r":   (("QUADRICEPS",), "r", "left", 73, "Бедро П"),
    "calf_r":    (("CALVES",), "r", "left", 90, "Голень П"),
    "chest":     (("CHEST",), None, "right", 14, "Грудь"),
    "biceps_l":  (("BICEPS",), "l", "right", 28, "Бицепс Л"),
    "waist":     (("ABS", "OBLIQUES"), None, "right", 42, "Талия"),
    "forearm_l": (("FOREARM",), "l", "right", 56, "Предплечье Л"),
    "thigh_l":   (("QUADRICEPS",), "l", "right", 72, "Бедро Л"),
    "calf_l":    (("CALVES",), "l", "right", 90, "Голень Л"),
}


def _centroid(points: str) -> tuple[float, float]:
    """Return the mean of the vertices of an SVG `points` string like "x1 y1 x2 y2 ..."."""
    numbers = [int(n) for n in points.split()]
    xs, ys = numbers[0::2], numbers[1::2]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _region(muscles: tuple[str, ...], side: str | None) -> tuple[list[str], tuple[float, float]]:
    """Return (polygons, centre) of a body region given its muscle names and side.

    A polygon belongs to the person's right side when its centre is left of the middle of
    the drawing (x < 500). The centre is where the label's leader line ends.
    """
    polygons = [
        points for muscle in muscles for points in POLYGONS[muscle]
        if side is None or (side == "r") == (_centroid(points)[0] < FIG_W / 2)
    ]
    centres = [_centroid(points) for points in polygons]
    return polygons, (sum(x for x, _ in centres) / len(centres), sum(y for _, y in centres) / len(centres))


REGIONS = {key: _region(muscles, side) for key, (muscles, side, *_rest) in FIGURE.items()}
# Computed once at import: the whole neutral body drawing does not depend on the data.
BASE_POLYGONS = [points for polygons in POLYGONS.values() for points in polygons]


def summarize(series: list[tuple[str, float]], period: int, better: str) -> dict:
    """Describe one measurement series: latest value, change in percent and how to colour it.

    `series` is [(ISO date, value)] sorted by date; `period` is days back from the latest
    date (0 = from the first measurement); `better` is "up", "down" or "none".
    Returns {value, date, pct_text, tone, base}; pct_text is empty when there is no earlier
    value to compare with. tone is "good", "bad" or "flat" (no judgement or no change).
    `base` is the number a value typed on the page today is compared with (see body.js):
    the baseline, or the latest value when it is from an earlier day, else None.
    """
    latest_date, latest = series[-1]
    summary = {"value": latest, "date": latest_date, "pct_text": "", "tone": "flat",
               "base": latest if latest_date != date.today().isoformat() else None}
    cutoff = date.fromisoformat(latest_date) - timedelta(days=period) if period else None
    # PERF: the series is already sorted, so bisect on the date strings would find the
    # baseline in O(log n) instead of scanning; series here are a few dozen points at most.
    baseline = next(((d, v) for d, v in series if cutoff is None or date.fromisoformat(d) >= cutoff), None)
    if baseline is None or baseline[0] == latest_date:
        return summary
    summary["base"] = baseline[1]
    pct = (latest - baseline[1]) / baseline[1] * 100
    summary["pct_text"] = f"{pct:+.1f} %".replace("-", "−")
    if abs(pct) >= 0.05 and better != "none":
        summary["tone"] = "good" if (pct > 0) == (better == "up") else "bad"
    return summary


def load_summaries(db: sqlite3.Connection, period: int) -> tuple[list[sqlite3.Row], dict[str, dict]]:
    """Return (all measure types, {key: summary}) for the types that have at least one value."""
    types = db.execute("SELECT * FROM measure_types ORDER BY id").fetchall()
    series: dict[int, list[tuple[str, float]]] = {}
    for row in db.execute("SELECT type_id, measured_on, value FROM measurements ORDER BY measured_on"):
        series.setdefault(row["type_id"], []).append((row["measured_on"], row["value"]))
    summaries = {t["key"]: summarize(series[t["id"]], period, t["better"]) for t in types if t["id"] in series}
    return types, summaries


@router.get("", response_class=HTMLResponse)
def body_page(request: Request, db: Db, period: int = DEFAULT_PERIOD) -> HTMLResponse:
    """Show the figure, weight and fat tiles, the table and the input form for the chosen period."""
    if period not in dict(PERIODS):
        period = DEFAULT_PERIOD
    types, summaries = load_summaries(db, period)
    labels = [
        {
            "key": key, "side": column, "top": label_y, "name": short, "edge": LABEL_EDGE[column],
            "dot_x": BODY_LEFT + BODY_WIDTH * REGIONS[key][1][0] / FIG_W,  # % of the box width
            "dot_y": REGIONS[key][1][1] / FIG_H * 100,                     # % of the box height
            "info": summaries.get(key),
        }
        for key, (_muscles, _side, column, label_y, short) in FIGURE.items()
    ]
    # Every polygon is drawn neutral first; the measured regions are painted over it by tone.
    # Every region gets its own group (id reg-<key>) so body.js can recolour it while typing.
    overlays = [
        {"key": key, "tone": summaries[key]["tone"] if key in summaries else "", "polygons": REGIONS[key][0]}
        for key in FIGURE
    ]
    return page(
        request, "body.html", "blue", types=types, summaries=summaries, labels=labels,
        base=BASE_POLYGONS, overlays=overlays, ratio=BOX_RATIO, by_key={t["key"]: t for t in types},
        period=period, periods=PERIODS, today=date.today().isoformat(),
        tiles=[(t, summaries.get(t["key"])) for t in types if t["kind"] != "girth"],
    )


@router.post("/measure")
async def add_measurements(request: Request, db: Db) -> RedirectResponse:
    """Save the filled-in fields of the form as measurements for one date.

    The fields are named after `measure_types.key`; blank ones are skipped. The date is
    today unless a `measured_on` field is sent. Values are checked against each type's
    plausible range and the date must not be in the future.
    Saving a type again for the same date replaces the old value.
    """
    form = await request.form()
    try:
        measured_on = date.fromisoformat(str(form.get("measured_on") or date.today().isoformat()))
    except ValueError:
        raise HTTPException(422, "Дата замера некорректна") from None
    if measured_on > date.today():
        raise HTTPException(422, "Дата замера в будущем")

    rows = []
    for t in db.execute("SELECT id, key, name, unit, min_val, max_val FROM measure_types"):
        raw = str(form.get(t["key"], "")).strip().replace(",", ".")
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            raise HTTPException(422, f"{t['name']}: не число") from None
        if not t["min_val"] <= value <= t["max_val"]:  # also rejects nan and inf
            raise HTTPException(422, f"{t['name']}: допустимо от {t['min_val']:g} до {t['max_val']:g} {t['unit']}")
        rows.append((measured_on.isoformat(), t["id"], value))
    if not rows:
        raise HTTPException(422, "Введи хотя бы один замер")

    with db:
        db.executemany(
            "INSERT INTO measurements (measured_on, type_id, value) VALUES (?, ?, ?) "
            "ON CONFLICT (measured_on, type_id) DO UPDATE SET value = excluded.value",
            rows,
        )
    return RedirectResponse("/body", status_code=303)
