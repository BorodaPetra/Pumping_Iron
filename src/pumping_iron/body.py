"""Block 2: body measurements, their dynamics and the labelled human figure.

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

from pumping_iron.db import get_db
from pumping_iron.web import page

router = APIRouter(prefix="/body")

Db = Annotated[sqlite3.Connection, Depends(get_db)]

# (period in days, label); 0 means "since the first measurement".
PERIODS = [(30, "30 дней"), (90, "90 дней"), (365, "Год"), (0, "Всё время")]
DEFAULT_PERIOD = 90

# Where each girth sits on the figure. The figure is a square box; the body drawing
# occupies its middle 40 % (x from 30 % to 70 %), the SVG body is 200 x 500 units.
# key: (column, label_y %, dot_x units, dot_y units, short label).
# Label rows are at least 13 % apart so two-line labels never overlap, and the order
# along each column follows the body from top to bottom so leader lines do not cross.
# The left column is the person's right side (the figure faces the viewer).
FIGURE = {
    "neck":      ("left", 6, 100, 72, "Шея"),
    "shoulders": ("left", 19, 58, 102, "Плечи"),
    "biceps_r":  ("left", 32, 39, 150, "Бицепс П"),
    "forearm_r": ("left", 47, 31, 240, "Предплечье П"),
    "hips":      ("left", 60, 68, 272, "Бёдра"),
    "thigh_r":   ("left", 73, 78, 330, "Бедро П"),
    "calf_r":    ("left", 90, 78, 445, "Голень П"),
    "chest":     ("right", 14, 124, 140, "Грудь"),
    "biceps_l":  ("right", 28, 161, 150, "Бицепс Л"),
    "waist":     ("right", 43, 130, 215, "Талия"),
    "forearm_l": ("right", 57, 169, 240, "Предплечье Л"),
    "thigh_l":   ("right", 72, 122, 330, "Бедро Л"),
    "calf_l":    ("right", 90, 122, 445, "Голень Л"),
}


def summarize(series: list[tuple[str, float]], period: int, better: str) -> dict:
    """Describe one measurement series: latest value, change in percent and how to colour it.

    `series` is [(ISO date, value)] sorted by date; `period` is days back from the latest
    date (0 = from the first measurement); `better` is "up", "down" or "none".
    Returns {value, date, pct_text, tone}; pct_text is empty when there is no earlier
    value to compare with. tone is "good", "bad" or "flat" (no judgement or no change).
    """
    latest_date, latest = series[-1]
    summary = {"value": latest, "date": latest_date, "pct_text": "", "tone": "flat"}
    cutoff = date.fromisoformat(latest_date) - timedelta(days=period) if period else None
    # PERF: the series is already sorted, so bisect on the date strings would find the
    # baseline in O(log n) instead of scanning; series here are a few dozen points at most.
    baseline = next(((d, v) for d, v in series if cutoff is None or date.fromisoformat(d) >= cutoff), None)
    if baseline is None or baseline[0] == latest_date:
        return summary
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
            "side": side, "top": label_y, "name": short,
            "dot_x": 30 + dot_x * 0.2, "dot_y": dot_y / 5,  # units -> % of the figure box
            "info": summaries.get(key),
        }
        for key, (side, label_y, dot_x, dot_y, short) in FIGURE.items()
    ]
    return page(
        request, "body.html", "blue", types=types, summaries=summaries, labels=labels,
        period=period, periods=PERIODS, today=date.today().isoformat(),
        tiles=[(t, summaries.get(t["key"])) for t in types if t["kind"] != "girth"],
    )


@router.post("/measure")
async def add_measurements(request: Request, db: Db) -> RedirectResponse:
    """Save the filled-in fields of the form as measurements for one date.

    The fields are named after `measure_types.key`; blank ones are skipped. Values are
    checked against each type's plausible range and the date must not be in the future.
    Saving a type again for the same date replaces the old value.
    """
    form = await request.form()
    try:
        measured_on = date.fromisoformat(str(form.get("measured_on", "")))
    except ValueError:
        raise HTTPException(422, "Укажи дату замера") from None
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
