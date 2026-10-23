"""Block 5: statistics built from the training journal, the food diary and the measurements.

  GET /stats   summary tiles, workouts and tonnage per week, sets per muscle group (with
               antagonist imbalance hints), progress of one exercise, personal records,
               calories per day against the plan and the weight / body fat dynamics

Query: `period` (days back from today, 0 = all time; same choices as the body page) and
`exercise_id` (the exercise for the progress chart, default the most logged one).
The charts are inline SVG made by `charts.py`; nothing here writes to the database.
"""

import sqlite3
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from pumping_iron.body import DEFAULT_PERIOD, PERIODS, summarize
from pumping_iron.charts import bar_chart, line_chart
from pumping_iron.db import get_db
from pumping_iron.web import page

router = APIRouter(prefix="/stats")

Db = Annotated[sqlite3.Connection, Depends(get_db)]

NUTRITION_DAYS_MAX = 60   # the calorie chart never shows more days than this
IMBALANCE_RATIO = 2       # an antagonist pair is flagged when one side has this many times more sets...
IMBALANCE_MIN_SETS = 6    # ...and the bigger side has at least this many


def week_start(day: date) -> date:
    """Return the Monday of the week that contains `day`."""
    return day - timedelta(days=day.weekday())


def since_of(period: int) -> str:
    """Return the first ISO date of the period counted back from today ('' = no lower bound, all time)."""
    return (date.today() - timedelta(days=period)).isoformat() if period else ""


def training_totals(db: sqlite3.Connection, since: str) -> tuple[dict, dict[date, dict]]:
    """Return (summary tiles, {week start: {days, sets, tonnage}}) for the journal since `since`.

    The weeks run from the first week of the period to the current one, empty weeks included,
    so the charts show gaps. The tiles hold workouts (days with at least one set), sets,
    tonnage in kg (reps x weight), the average workouts per week and the current streak of
    weeks in a row with a workout (counted over the whole journal, not only the period).
    """
    # PERF: SQL could bucket by week itself (GROUP BY date(performed_on, 'weekday 0', '-6 days'));
    # grouping the per-day rows in Python is kept because a journal has a few hundred days at most.
    rows = db.execute(
        """SELECT performed_on, COUNT(*) AS sets, SUM(reps * weight_kg) AS tonnage
           FROM log_entries WHERE performed_on >= ? GROUP BY performed_on ORDER BY performed_on""",
        (since,),
    ).fetchall()
    first = date.fromisoformat(rows[0]["performed_on"]) if rows and not since else \
        date.fromisoformat(since) if since else None
    weeks: dict[date, dict] = {}
    if first:
        monday = week_start(first)
        while monday <= week_start(date.today()):
            weeks[monday] = {"days": 0, "sets": 0, "tonnage": 0.0}
            monday += timedelta(days=7)
    for row in rows:
        week = weeks[week_start(date.fromisoformat(row["performed_on"]))]
        week["days"] += 1
        week["sets"] += row["sets"]
        week["tonnage"] += row["tonnage"]

    trained = {week_start(date.fromisoformat(r["performed_on"]))
               for r in db.execute("SELECT DISTINCT performed_on FROM log_entries")}
    monday = week_start(date.today())
    if monday not in trained:  # the current week may still be empty without breaking the streak
        monday -= timedelta(days=7)
    streak = 0
    while monday in trained:
        streak += 1
        monday -= timedelta(days=7)

    workouts = sum(w["days"] for w in weeks.values())
    tiles = {
        "workouts": workouts,
        "sets": sum(w["sets"] for w in weeks.values()),
        "tonnage": sum(w["tonnage"] for w in weeks.values()),
        "per_week": workouts / len(weeks) if weeks else 0,
        "streak": streak,
    }
    return tiles, weeks


def muscle_balance(db: sqlite3.Connection, since: str) -> tuple[list[dict], list[str]]:
    """Return (sets per muscle group, imbalance hints) for the journal since `since`.

    Every group is listed, also with zero sets, so a neglected group is visible. `width` is the
    bar width in percent of the busiest group. A hint names an antagonist pair (chest / back)
    where one side has at least IMBALANCE_RATIO times more sets than the other.
    """
    rows = db.execute(
        """SELECT g.id, g.name, g.antagonist_id, COUNT(l.id) AS sets
           FROM muscle_groups g
           LEFT JOIN exercises e ON e.muscle_group_id = g.id
           LEFT JOIN log_entries l ON l.exercise_id = e.id AND l.performed_on >= ?
           GROUP BY g.id ORDER BY g.id""",
        (since,),
    ).fetchall()
    busiest = max((r["sets"] for r in rows), default=0) or 1
    groups = [{"name": r["name"], "sets": r["sets"], "width": 100 * r["sets"] / busiest} for r in rows]
    by_id = {r["id"]: r for r in rows}
    hints = []
    for r in rows:
        other = by_id.get(r["antagonist_id"])
        if other and r["id"] < other["id"]:
            more, less = (r, other) if r["sets"] >= other["sets"] else (other, r)
            if more["sets"] >= IMBALANCE_MIN_SETS and more["sets"] >= IMBALANCE_RATIO * less["sets"]:
                hints.append(f"{more['name']}: {more['sets']} подходов, {less['name']}: {less['sets']}")
    return groups, hints


def exercise_progress(db: sqlite3.Connection, since: str, exercise_id: int | None) -> dict:
    """Return the logged exercises and the progress points of the chosen one.

    The chosen exercise defaults to the most logged one in the period. The progress value of a
    day is the best set of that day: the estimated one-rep max (Epley: weight x (1 + reps / 30),
    kg) or, for an exercise always done without added weight, the best number of repetitions.
    Returns {options, selected, points, unit, formula}; `points` is [(date, value)].
    """
    options = db.execute(
        """SELECT e.id, e.name, COUNT(*) AS sets FROM log_entries l JOIN exercises e ON e.id = l.exercise_id
           WHERE l.performed_on >= ? GROUP BY e.id ORDER BY sets DESC, e.name""",
        (since,),
    ).fetchall()
    chosen = next((o for o in options if o["id"] == exercise_id), options[0] if options else None)
    result = {"options": options, "selected": chosen, "points": [], "unit": "кг", "formula": True}
    if chosen is None:
        return result
    rows = db.execute(
        "SELECT performed_on, reps, weight_kg FROM log_entries WHERE exercise_id = ? AND performed_on >= ? ORDER BY performed_on",
        (chosen["id"], since),
    ).fetchall()
    weighted = any(r["weight_kg"] > 0 for r in rows)
    result["unit"], result["formula"] = ("кг", True) if weighted else ("повторений", False)
    best: dict[date, float] = {}
    for r in rows:
        value = r["weight_kg"] * (1 + r["reps"] / 30) if weighted else r["reps"]
        day = date.fromisoformat(r["performed_on"])
        best[day] = max(best.get(day, 0), value)
    result["points"] = sorted(best.items())
    return result


def personal_records(db: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return the heaviest set of every logged exercise (all time): name, weight, reps, date.

    `weight_kg * 100000 + reps` is maximised so that, at equal weight, the set with more
    repetitions wins, and for exercises without added weight the best repetitions win. SQLite
    returns the other columns from the row that holds that maximum.
    """
    return db.execute(
        """SELECT e.name, l.weight_kg, l.reps, l.performed_on, MAX(l.weight_kg * 100000 + l.reps)
           FROM log_entries l JOIN exercises e ON e.id = l.exercise_id
           GROUP BY e.id ORDER BY e.name"""
    ).fetchall()


def nutrition_days(db: sqlite3.Connection, period: int) -> dict:
    """Return calories per day for the last days of the period, the plan and the average intake.

    The window is the period capped at NUTRITION_DAYS_MAX days (all time shows the cap too).
    Days without any diary entry stay empty. Averages are over the days that have entries.
    """
    days = min(period or NUTRITION_DAYS_MAX, NUTRITION_DAYS_MAX)
    today = date.today()
    window = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    rows = {
        r["eaten_on"]: r
        for r in db.execute(
            """SELECT eaten_on, SUM(kcal) AS kcal, SUM(protein_g) AS protein_g, SUM(fat_g) AS fat_g, SUM(carbs_g) AS carbs_g
               FROM food_log WHERE eaten_on >= ? GROUP BY eaten_on""",
            (window[0].isoformat(),),
        )
    }
    logged = [rows[d.isoformat()] for d in window if d.isoformat() in rows]
    # The plan differs by weekday, so the reference line is the average planned day (days with an empty plan do not count).
    plan = round(db.execute(
        "SELECT COALESCE(AVG(kcal), 0) FROM (SELECT SUM(kcal) AS kcal FROM diet_items GROUP BY weekday)").fetchone()[0])
    average = {key: sum(r[key] for r in logged) / len(logged) for key in ("kcal", "protein_g", "fat_g", "carbs_g")} if logged else None
    return {
        "days": days, "logged": len(logged), "plan": plan, "average": average,
        "values": [rows[d.isoformat()]["kcal"] if d.isoformat() in rows else 0 for d in window],
        "labels": [f"{d:%d.%m}" for d in window],
        "tips": [f"{d:%d.%m.%Y}" for d in window],
    }


def body_series(db: sqlite3.Connection, key: str, period: int) -> dict | None:
    """Return the dynamics of one measurement (weight / fat): points, the change summary and the type.

    The period is counted back from the latest measurement of that type, as on the body page,
    so a pause in measuring does not empty the chart. None when nothing was measured yet.
    """
    kind = db.execute("SELECT id, name, unit, better FROM measure_types WHERE key = ?", (key,)).fetchone()
    series = [(r["measured_on"], r["value"]) for r in db.execute(
        "SELECT measured_on, value FROM measurements WHERE type_id = ? ORDER BY measured_on", (kind["id"],))]
    if not series:
        return None
    cutoff = date.fromisoformat(series[-1][0]) - timedelta(days=period) if period else date.min
    points = [(date.fromisoformat(d), v) for d, v in series if date.fromisoformat(d) >= cutoff]
    return {"type": kind, "points": points, "summary": summarize(series, period, kind["better"])}


@router.get("", response_class=HTMLResponse)
def stats_page(request: Request, db: Db, period: int = DEFAULT_PERIOD, exercise_id: int | None = None) -> HTMLResponse:
    """Show the statistics page for the chosen period (unknown periods fall back to the default)."""
    if period not in dict(PERIODS):
        period = DEFAULT_PERIOD
    since = since_of(period)
    tiles, weeks = training_totals(db, since)
    muscles, hints = muscle_balance(db, since)
    progress = exercise_progress(db, since, exercise_id)
    nutrition = nutrition_days(db, period)
    week_labels = [f"{w:%d.%m}" for w in weeks]
    week_tips = [f"неделя с {w:%d.%m.%Y}" for w in weeks]
    trained = tiles["workouts"] > 0  # an all-empty weekly chart would say nothing: the page shows a hint instead
    charts = {
        "days": bar_chart([w["days"] for w in weeks.values()], week_labels, "Тренировок по неделям", "тренировок", tips=week_tips)
        if trained else None,
        "tonnage": bar_chart([w["tonnage"] for w in weeks.values()], week_labels, "Тоннаж по неделям", "кг", tips=week_tips)
        if trained else None,
        "progress": line_chart(progress["points"], f"Прогресс: {progress['selected']['name']}", progress["unit"])
        if progress["points"] else None,
        "kcal": bar_chart(nutrition["values"], nutrition["labels"], "Калории по дням", "ккал", nutrition["plan"] or None,
                          tips=nutrition["tips"])
        if nutrition["logged"] else None,
    }
    body = {}
    for key in ("weight", "fat"):
        series = body_series(db, key, period)
        if series:
            series["chart"] = line_chart(series["points"], series["type"]["name"], series["type"]["unit"])
        body[key] = series
    return page(
        request, "stats.html", "white", period=period, periods=PERIODS, tiles=tiles, charts=charts,
        muscles=muscles, hints=hints, progress=progress, records=personal_records(db), nutrition=nutrition, body=body,
    )
