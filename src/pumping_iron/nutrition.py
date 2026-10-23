"""Block 6: the weekly meal plan and the food diary for nutrition monitoring.

  /nutrition       the plan (`diet_items`): foods per weekday and meal with calories and macros
  /nutrition/log   the diary (`food_log`): what was actually eaten on a given date,
                   compared with the plan of that weekday

Same pattern as workouts: GET renders the page, changes are form POSTs answered with
a 303 redirect. The AI trainer fills the plan through `trainer_tools`.
"""

import sqlite3
from datetime import date, timedelta
from typing import Annotated, Literal, get_args

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from pumping_iron.db import get_db, write
from pumping_iron.web import page
from pumping_iron.workouts import WEEKDAYS, WEEKDAYS_SHORT

router = APIRouter(prefix="/nutrition")

Db = Annotated[sqlite3.Connection, Depends(get_db)]

Meal = Literal["Завтрак", "Обед", "Ужин", "Перекус"]
MEALS = list(get_args(Meal))

NUTRIENTS = ("kcal", "protein_g", "fat_g", "carbs_g")

# Validated form fields shared by the plan and the diary.
Food = Annotated[str, Form(min_length=1, max_length=80)]
Grams = Annotated[int, Form(ge=1, le=5000)]
Kcal = Annotated[int, Form(ge=0, le=5000)]
Protein = Annotated[float, Form(ge=0, le=500)]
Fat = Annotated[float, Form(ge=0, le=500)]
Carbs = Annotated[float, Form(ge=0, le=1000)]


def group_by_meal(rows: list[sqlite3.Row]) -> tuple[list[dict], dict]:
    """Group food rows by meal (in MEALS order) and sum calories and macros.

    Returns ([{meal, items, totals}], totals for all rows). Works for plan and diary rows alike.
    """
    meals = {name: {"meal": name, "items": [], "totals": dict.fromkeys(NUTRIENTS, 0)} for name in MEALS}
    day = dict.fromkeys(NUTRIENTS, 0)
    for row in rows:
        meal = meals[row["meal"]]
        meal["items"].append(row)
        for key in NUTRIENTS:
            meal["totals"][key] += row[key]
            day[key] += row[key]
    return list(meals.values()), day


def build_diet(db: sqlite3.Connection, weekday: int) -> tuple[list[dict], dict]:
    """Return the plan of one weekday (0 = Monday) grouped by meal with per-meal and daily totals."""
    return group_by_meal(db.execute("SELECT * FROM diet_items WHERE weekday = ? ORDER BY id", (weekday,)).fetchall())


def build_diet_week(db: sqlite3.Connection) -> list[dict]:
    """Return the plan as seven day dicts: name, meals (see `group_by_meal`) and the totals of the day.

    One query loads the whole week; grouping by weekday happens in Python.
    """
    # PERF: one query for the week instead of seven build_diet calls; with a few dozen rows either is instant.
    rows = db.execute("SELECT * FROM diet_items ORDER BY id").fetchall()
    days = []
    for weekday, name in enumerate(WEEKDAYS):
        meals, totals = group_by_meal([row for row in rows if row["weekday"] == weekday])
        days.append({"name": name, "meals": meals, "totals": totals})
    return days


def day_label(day: date) -> str:
    """Format a date like 'чт, 22.10.2026'."""
    return f"{WEEKDAYS_SHORT[day.weekday()]}, {day:%d.%m.%Y}"


@router.get("", response_class=HTMLResponse)
def nutrition_page(request: Request, db: Db) -> HTMLResponse:
    """Show the meal plan of the week: per day, per meal, with totals and the average day."""
    days = build_diet_week(db)
    planned = [day["totals"] for day in days if day["totals"]["kcal"]]
    average = {key: sum(t[key] for t in planned) / len(planned) for key in NUTRIENTS} if planned else None
    return page(request, "nutrition.html", "orange", days=days, average=average, meals=MEALS)


@router.post("/add")
def add_item(db: Db, weekday: Annotated[int, Form(ge=0, le=6)], meal: Annotated[Meal, Form()], food: Food,
             grams: Grams, kcal: Kcal, protein_g: Protein, fat_g: Fat, carbs_g: Carbs) -> RedirectResponse:
    """Add a food to a meal of one weekday (0 = Monday) of the plan; the browser returns to that day."""
    write(db, "INSERT INTO diet_items (weekday, meal, food, grams, kcal, protein_g, fat_g, carbs_g) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
          (weekday, meal, food.strip(), grams, kcal, protein_g, fat_g, carbs_g))
    return RedirectResponse(f"/nutrition#day-{weekday}", status_code=303)


@router.get("/log", response_class=HTMLResponse)
def log_page(request: Request, db: Db, day: date | None = None) -> HTMLResponse:
    """Show the diary for one day (default today) against the plan totals of that weekday."""
    day = day or date.today()
    iso = day.isoformat()
    meals, eaten = group_by_meal(
        db.execute("SELECT * FROM food_log WHERE eaten_on = ? ORDER BY id", (iso,)).fetchall()
    )
    _, planned = build_diet(db, day.weekday())
    return page(
        request, "nutrition_log.html", "orange",
        day=iso, label=day_label(day), prev_day=(day - timedelta(days=1)).isoformat(),
        next_day=(day + timedelta(days=1)).isoformat(), meals=meals, eaten=eaten, planned=planned,
    )


@router.post("/log/add")
def add_log_item(db: Db, eaten_on: Annotated[date, Form()], meal: Annotated[Meal, Form()], food: Food,
                 grams: Grams, kcal: Kcal, protein_g: Protein, fat_g: Fat, carbs_g: Carbs) -> RedirectResponse:
    """Record a food eaten on a date."""
    write(db, "INSERT INTO food_log (eaten_on, meal, food, grams, kcal, protein_g, fat_g, carbs_g) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
          (eaten_on.isoformat(), meal, food.strip(), grams, kcal, protein_g, fat_g, carbs_g))
    return RedirectResponse(f"/nutrition/log?day={eaten_on.isoformat()}", status_code=303)


@router.post("/log/{entry_id}/delete")
def delete_log_item(entry_id: int, db: Db, eaten_on: Annotated[date | None, Form()] = None) -> RedirectResponse:
    """Delete a diary entry; `eaten_on` (optional) returns the browser to the same day."""
    write(db, "DELETE FROM food_log WHERE id = ?", (entry_id,))
    return RedirectResponse(f"/nutrition/log?day={eaten_on.isoformat()}" if eaten_on else "/nutrition/log",
                            status_code=303)


@router.post("/{item_id}/delete")
def delete_item(item_id: int, db: Db) -> RedirectResponse:
    """Remove a food from the plan."""
    row = db.execute("SELECT weekday FROM diet_items WHERE id = ?", (item_id,)).fetchone()
    write(db, "DELETE FROM diet_items WHERE id = ?", (item_id,))
    return RedirectResponse(f"/nutrition#day-{row['weekday']}" if row else "/nutrition", status_code=303)
