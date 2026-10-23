"""Block 1: weekly training program and training journal.

Pages (GET) render HTML; every change is a plain HTML form POST that answers with a
303 redirect back to the page (post/redirect/get), so no client-side JS is needed.
"""

import sqlite3
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from pumping_iron.db import get_db, write
from pumping_iron.web import page

router = APIRouter(prefix="/workouts")

Db = Annotated[sqlite3.Connection, Depends(get_db)]

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
# The training program has three numbered days instead of weekdays.
PROGRAM_DAYS = ["День 1", "День 2", "День 3"]

# Validated form fields shared by several endpoints.
Sets = Annotated[int, Form(ge=1, le=20)]
Reps = Annotated[int, Form(ge=1, le=100)]
Weight = Annotated[float, Form(ge=0, le=1000)]


def exercise_options(db: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    """Return the exercise library as {muscle group name: [exercise rows]} for <optgroup> selects."""
    rows = db.execute(
        """SELECT e.id, e.name, g.name AS group_name
           FROM exercises e JOIN muscle_groups g ON g.id = e.muscle_group_id
           ORDER BY g.id, e.name"""
    ).fetchall()
    options: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        options.setdefault(row["group_name"], []).append(row)
    return options


def build_program(db: sqlite3.Connection) -> list[dict]:
    """Return the program as three day dicts: name, items, muscle groups and missing antagonists.

    One joined query loads the whole program; grouping by day happens in Python.
    A group's antagonist counts as missing when no exercise of that group is on the same day.
    """
    rows = db.execute(
        """SELECT p.id, p.day, p.sets, p.reps, p.weight_kg,
                  e.name AS exercise, g.id AS group_id, g.name AS group_name,
                  g.antagonist_id, a.name AS antagonist_name
           FROM program_items p
           JOIN exercises e ON e.id = p.exercise_id
           JOIN muscle_groups g ON g.id = e.muscle_group_id
           LEFT JOIN muscle_groups a ON a.id = g.antagonist_id
           ORDER BY p.day, p.id"""
    ).fetchall()
    days = [{"name": name, "items": []} for name in PROGRAM_DAYS]
    for row in rows:
        days[row["day"]]["items"].append(row)
    for day in days:
        present = {item["group_id"] for item in day["items"]}
        day["groups"] = list(dict.fromkeys(item["group_name"] for item in day["items"]))
        day["missing"] = list(dict.fromkeys(
            item["antagonist_name"]
            for item in day["items"]
            if item["antagonist_id"] and item["antagonist_id"] not in present
        ))
    return days


@router.get("", response_class=HTMLResponse)
def program_page(request: Request, db: Db) -> HTMLResponse:
    """Show the 3-day program with edit forms."""
    return page(request, "workouts.html", "red", days=build_program(db), options=exercise_options(db))


@router.get("/exercises", response_class=HTMLResponse)
def library_page(request: Request, db: Db) -> HTMLResponse:
    """Show the exercise library grouped by muscle group, with the form for a new exercise."""
    groups = db.execute("SELECT id, name FROM muscle_groups ORDER BY id").fetchall()
    return page(request, "exercises.html", "red", groups=groups, options=exercise_options(db))


@router.post("/program/add")
def add_program_item(db: Db, day: Annotated[int, Form(ge=0, le=2)],
                     exercise_id: Annotated[int, Form()],
                     sets: Sets, reps: Reps, weight_kg: Weight) -> RedirectResponse:
    """Add an exercise to a day (0-2) of the program."""
    write(db, "INSERT INTO program_items (day, exercise_id, sets, reps, weight_kg) VALUES (?, ?, ?, ?, ?)",
          (day, exercise_id, sets, reps, weight_kg))
    return RedirectResponse(f"/workouts#day-{day}", status_code=303)


def program_day(db: sqlite3.Connection, item_id: int) -> int:
    """Return the day (0-2) of a program item, or raise HTTP 404 if it does not exist."""
    row = db.execute("SELECT day FROM program_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Упражнения нет в программе")
    return row["day"]


@router.post("/program/{item_id}/update")
def update_program_item(item_id: int, db: Db, sets: Sets, reps: Reps, weight_kg: Weight) -> RedirectResponse:
    """Change sets, reps and weight of a program item."""
    day = program_day(db, item_id)
    write(db, "UPDATE program_items SET sets = ?, reps = ?, weight_kg = ? WHERE id = ?",
          (sets, reps, weight_kg, item_id))
    return RedirectResponse(f"/workouts#day-{day}", status_code=303)


@router.post("/program/{item_id}/delete")
def delete_program_item(item_id: int, db: Db) -> RedirectResponse:
    """Remove an exercise from the program."""
    day = program_day(db, item_id)
    write(db, "DELETE FROM program_items WHERE id = ?", (item_id,))
    return RedirectResponse(f"/workouts#day-{day}", status_code=303)


@router.post("/exercises")
def add_exercise(db: Db, name: Annotated[str, Form(min_length=1, max_length=80)],
                 muscle_group_id: Annotated[int, Form()]) -> RedirectResponse:
    """Add a new exercise to the library."""
    write(db, "INSERT INTO exercises (name, muscle_group_id) VALUES (?, ?)", (name.strip(), muscle_group_id))
    return RedirectResponse("/workouts/exercises", status_code=303)


@router.get("/journal", response_class=HTMLResponse)
def journal_page(request: Request, db: Db, exercise_id: int | None = None, reps: int | None = None,
                 weight_kg: float | None = None, performed_on: date | None = None) -> HTMLResponse:
    """Show the set-logging form and recent entries grouped by day, newest first.

    The optional query parameters prefill the form with the previous set, so several
    sets of one exercise can be logged quickly.
    """
    # PERF: LIMIT 200 sets is a stopgap; once the journal grows, paginate by date range
    # (WHERE performed_on < ?) instead of loading the newest rows every time.
    rows = db.execute(
        """SELECT l.id, l.performed_on, l.reps, l.weight_kg, e.name AS exercise, g.name AS group_name
           FROM log_entries l
           JOIN exercises e ON e.id = l.exercise_id
           JOIN muscle_groups g ON g.id = e.muscle_group_id
           ORDER BY l.performed_on DESC, l.id DESC LIMIT 200"""
    ).fetchall()
    entries: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        entries.setdefault(row["performed_on"], []).append(row)
    days = [
        {"label": f"{WEEKDAYS_SHORT[d.weekday()]}, {d:%d.%m.%Y}", "rows": day_rows}
        for d, day_rows in ((date.fromisoformat(key), value) for key, value in entries.items())
    ]
    prefill = {
        "exercise_id": exercise_id, "reps": reps, "weight_kg": weight_kg,
        "performed_on": (performed_on or date.today()).isoformat(),
    }
    return page(request, "journal.html", "red", days=days, options=exercise_options(db), prefill=prefill)


@router.post("/log")
def add_log_entry(db: Db, performed_on: Annotated[date, Form()], exercise_id: Annotated[int, Form()],
                  reps: Reps, weight_kg: Weight) -> RedirectResponse:
    """Record one performed set and return to the journal with the form prefilled."""
    write(db, "INSERT INTO log_entries (performed_on, exercise_id, reps, weight_kg) VALUES (?, ?, ?, ?)",
          (performed_on.isoformat(), exercise_id, reps, weight_kg))
    return RedirectResponse(
        f"/workouts/journal?exercise_id={exercise_id}&reps={reps}&weight_kg={weight_kg}"
        f"&performed_on={performed_on.isoformat()}",
        status_code=303,
    )


@router.post("/log/{entry_id}/delete")
def delete_log_entry(entry_id: int, db: Db) -> RedirectResponse:
    """Delete one journal entry."""
    write(db, "DELETE FROM log_entries WHERE id = ?", (entry_id,))
    return RedirectResponse("/workouts/journal", status_code=303)
