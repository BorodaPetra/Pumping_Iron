"""What the AI trainer can see and do: program/diet/journal/body context and its tools.

The context (exercise library, weekly program, last training days and records, body
measurements, meal plan, today's diary) is put into
the system prompt on every request, so the model always works with the live data.
The tools (`add_to_program`, `add_to_diet`) are described to the model in the OpenAI
function-calling format that LM Studio accepts.
"""

import json
import sqlite3
from datetime import date

from pumping_iron.body import load_summaries
from pumping_iron.nutrition import MEALS, build_diet
from pumping_iron.stats import personal_records
from pumping_iron.workouts import WEEKDAYS, WEEKDAYS_SHORT, build_week, exercise_options

# How much history goes into the prompt: the last training days, and the period of the body changes.
JOURNAL_DAYS = 5
BODY_PERIOD_DAYS = 90

# Same limit as the web forms (nutrition.py, workouts.py), so the model cannot create longer names.
MAX_NAME_LEN = 80

ADD_TO_PROGRAM_TOOL = {
    "type": "function",
    "function": {
        "name": "add_to_program",
        "description": (
            "Добавить упражнение в недельную программу тренировок пользователя. "
            "Вызывай по одному разу на каждое упражнение."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "weekday": {"type": "integer", "minimum": 1, "maximum": 7,
                            "description": "День недели: 1 = понедельник, 7 = воскресенье"},
                "exercise": {"type": "string",
                             "description": "Название упражнения, лучше точно из справочника"},
                "muscle_group": {"type": "string",
                                 "description": "Группа мышц из списка; нужна только если упражнения нет в справочнике"},
                "sets": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Подходы"},
                "reps": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Повторения в подходе"},
                "weight_kg": {"type": "number", "minimum": 0, "maximum": 1000,
                              "description": "Рабочий вес в кг, 0 для упражнений с собственным весом"},
            },
            "required": ["weekday", "exercise", "sets", "reps"],
        },
    },
}


ADD_TO_DIET_TOOL = {
    "type": "function",
    "function": {
        "name": "add_to_diet",
        "description": (
            "Добавить продукт или блюдо в рацион питания пользователя (план на день). "
            "Вызывай по одному разу на каждый продукт. Калории и БЖУ указывай на всю порцию в граммах, "
            "это оценка по справочным значениям."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "meal": {"type": "string", "enum": MEALS, "description": "Приём пищи"},
                "food": {"type": "string", "description": "Название продукта или блюда"},
                "grams": {"type": "integer", "minimum": 1, "maximum": 5000, "description": "Масса порции, г"},
                "kcal": {"type": "integer", "minimum": 0, "maximum": 5000, "description": "Калории порции"},
                "protein_g": {"type": "number", "minimum": 0, "description": "Белки порции, г"},
                "fat_g": {"type": "number", "minimum": 0, "description": "Жиры порции, г"},
                "carbs_g": {"type": "number", "minimum": 0, "description": "Углеводы порции, г"},
            },
            "required": ["meal", "food", "grams", "kcal", "protein_g", "fat_g", "carbs_g"],
        },
    },
}

TOOLS = [ADD_TO_PROGRAM_TOOL, ADD_TO_DIET_TOOL]


def journal_context(db: sqlite3.Connection) -> str:
    """Describe the last JOURNAL_DAYS training days and the personal records as text.

    A set is written "weight kg x reps"; a set without added weight is "N повт.". Returns
    a line saying the journal is empty when nothing was logged.
    """
    # PERF: `personal_records` groups the whole journal on every chat message; a table of
    # records updated when a set is logged would make this constant-time as the journal grows.
    rows = db.execute(
        """SELECT l.performed_on, e.name, l.reps, l.weight_kg
           FROM log_entries l JOIN exercises e ON e.id = l.exercise_id
           WHERE l.performed_on IN (SELECT DISTINCT performed_on FROM log_entries ORDER BY performed_on DESC LIMIT ?)
           ORDER BY l.performed_on DESC, l.id""",
        (JOURNAL_DAYS,),
    ).fetchall()
    if not rows:
        return "Журнал тренировок пуст."

    def set_text(weight: float, reps: int) -> str:
        return f"{weight:g}×{reps}" if weight else f"{reps} повт."

    days: dict[str, dict[str, list[str]]] = {}
    for row in rows:
        days.setdefault(row["performed_on"], {}).setdefault(row["name"], []).append(set_text(row["weight_kg"], row["reps"]))
    lines = []
    for iso, exercises in days.items():
        day = date.fromisoformat(iso)
        lines.append(f"- {WEEKDAYS_SHORT[day.weekday()]} {day:%d.%m.%Y}: "
                     + "; ".join(f"{name} {', '.join(sets)}" for name, sets in exercises.items()))
    records = "; ".join(f"{r['name']} {set_text(r['weight_kg'], r['reps'])}" for r in personal_records(db))
    return ("Журнал тренировок, последние дни (подход = вес кг × повторения):\n" + "\n".join(lines)
            + f"\n\nЛичные рекорды (самый тяжёлый подход): {records}")


def body_context(db: sqlite3.Connection) -> str:
    """Describe the latest body measurements and their change over BODY_PERIOD_DAYS as text."""
    types, summaries = load_summaries(db, BODY_PERIOD_DAYS)
    lines = []
    for t in types:
        s = summaries.get(t["key"])
        if s:
            change = f", {s['pct_text']} за {BODY_PERIOD_DAYS} дн." if s["pct_text"] else ""
            lines.append(f"- {t['name']}: {s['value']:g} {t['unit']} (замер {s['date']}{change})")
    return "Замеры тела:\n" + "\n".join(lines) if lines else "Замеров тела пока нет."


def build_context(db: sqlite3.Connection) -> str:
    """Describe the library, program, meal plan, intake, journal and body measurements as text for the system prompt."""
    library = "\n".join(
        f"- {group}: {', '.join(e['name'] for e in exercises)}"
        for group, exercises in exercise_options(db).items()
    )
    program = "\n".join(
        f"- {day['name']} (день {number}): "
        + ("; ".join(f"{i['exercise']} {i['sets']}×{i['reps']}, {i['weight_kg']:g} кг" for i in day["items"])
           or "отдых")
        for number, day in enumerate(build_week(db), start=1)
    )
    meals, plan_total = build_diet(db)
    diet = "\n".join(
        f"- {m['meal']}: "
        + ("; ".join(f"{i['food']} {i['grams']} г ({i['kcal']} ккал)" for i in m["items"]) or "пусто")
        for m in meals
    )
    eaten = db.execute(
        "SELECT COALESCE(SUM(kcal), 0) AS kcal FROM food_log WHERE eaten_on = ?", (date.today().isoformat(),)
    ).fetchone()
    today = date.today()
    return (
        f"Сегодня {WEEKDAYS_SHORT[today.weekday()]} {today:%d.%m.%Y}.\n\n"
        f"Справочник упражнений по группам мышц:\n{library}\n\nТекущая программа пользователя:\n{program}\n\n"
        f"{journal_context(db)}\n\n{body_context(db)}\n\n"
        f"Текущий рацион на день (итого {plan_total['kcal']} ккал, Б {plan_total['protein_g']:g} / "
        f"Ж {plan_total['fat_g']:g} / У {plan_total['carbs_g']:g}):\n{diet}\n\n"
        f"Сегодня по дневнику съедено {eaten['kcal']} ккал."
    )


def _clean_name(value) -> str:
    """Collapse all whitespace (newlines included) in a name from the model into single spaces.

    Names end up in the system prompt on later requests, so line breaks are removed
    to keep a stored name from looking like an instruction block.
    """
    return " ".join(str(value).split())


def _error(message: str) -> tuple[str, None]:
    """Build a failed tool result: the model gets the reason, the user sees nothing."""
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False), None


def add_to_program(db: sqlite3.Connection, args: dict) -> tuple[str, str | None]:
    """Insert one exercise into the program.

    Takes the connection and the model's arguments; returns (result for the model,
    notice for the user or None on failure). Known library names match
    case-insensitively; an unknown name is created only when a valid `muscle_group` is given.
    """
    try:
        weekday = int(args["weekday"])
        sets, reps = int(args["sets"]), int(args["reps"])
        weight = float(args.get("weight_kg") or 0)
        name = _clean_name(args["exercise"])
    except (KeyError, TypeError, ValueError):
        return _error("нужны weekday, exercise, sets, reps (числа) и необязательный weight_kg")
    if not (1 <= weekday <= 7 and 1 <= sets <= 20 and 1 <= reps <= 100 and 0 <= weight <= 1000 and name):
        return _error("значения вне допустимых диапазонов: weekday 1-7, sets 1-20, reps 1-100, weight_kg 0-1000")
    if len(name) > MAX_NAME_LEN:
        return _error(f"название упражнения длиннее {MAX_NAME_LEN} символов")

    # SQLite lower() is ASCII-only, so names are compared with casefold() in Python.
    library = {row["name"].casefold(): row for row in db.execute("SELECT id, name FROM exercises")}
    created = False
    exercise = library.get(name.casefold())
    if exercise is None:
        groups = {row["name"].casefold(): row["id"] for row in db.execute("SELECT id, name FROM muscle_groups")}
        group_id = groups.get(str(args.get("muscle_group") or "").strip().casefold())
        if group_id is None:
            return _error("упражнения нет в справочнике: выбери название из справочника или укажи muscle_group из списка групп")
        with db:
            cursor = db.execute("INSERT INTO exercises (name, muscle_group_id) VALUES (?, ?)", (name, group_id))
        exercise = {"id": cursor.lastrowid, "name": name}
        created = True

    day = weekday - 1
    if db.execute("SELECT 1 FROM program_items WHERE weekday = ? AND exercise_id = ?", (day, exercise["id"])).fetchone():
        return _error(f"«{exercise['name']}» уже есть в программе на этот день")
    with db:
        db.execute(
            "INSERT INTO program_items (weekday, exercise_id, sets, reps, weight_kg) VALUES (?, ?, ?, ?, ?)",
            (day, exercise["id"], sets, reps, weight),
        )
    notice = f"✅ В программу добавлено: {WEEKDAYS[day]}, {exercise['name']}, {sets}×{reps}, {weight:g} кг"
    if created:
        notice += " (новое упражнение в справочнике)"
    return json.dumps({"ok": True, "added": exercise["name"], "weekday": weekday}, ensure_ascii=False), notice


def add_to_diet(db: sqlite3.Connection, args: dict) -> tuple[str, str | None]:
    """Insert one food into the meal plan.

    Takes the connection and the model's arguments; returns (result for the model,
    notice for the user or None on failure). The same food in the same meal is rejected.
    """
    try:
        meal, food = str(args["meal"]).strip(), _clean_name(args["food"])
        grams, kcal = int(args["grams"]), int(args["kcal"])
        protein, fat, carbs = (float(args[k]) for k in ("protein_g", "fat_g", "carbs_g"))
    except (KeyError, TypeError, ValueError):
        return _error("нужны meal, food, grams, kcal, protein_g, fat_g, carbs_g (числа)")
    if meal not in MEALS:
        return _error(f"meal должен быть одним из: {', '.join(MEALS)}")
    if len(food) > MAX_NAME_LEN:
        return _error(f"название продукта длиннее {MAX_NAME_LEN} символов")
    if not (food and 1 <= grams <= 5000 and 0 <= kcal <= 5000 and 0 <= protein <= 500 and 0 <= fat <= 500 and 0 <= carbs <= 1000):
        return _error("значения вне допустимых диапазонов: grams 1-5000, kcal 0-5000, белки/жиры 0-500, углеводы 0-1000")
    if any(row["food"].casefold() == food.casefold()
           for row in db.execute("SELECT food FROM diet_items WHERE meal = ?", (meal,))):
        return _error(f"«{food}» уже есть в приёме «{meal}»")
    with db:
        db.execute(
            "INSERT INTO diet_items (meal, food, grams, kcal, protein_g, fat_g, carbs_g) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (meal, food, grams, kcal, protein, fat, carbs),
        )
    notice = f"✅ В рацион добавлено: {meal}, {food}, {grams} г, {kcal} ккал (Б {protein:g} / Ж {fat:g} / У {carbs:g})"
    return json.dumps({"ok": True, "added": food, "meal": meal}, ensure_ascii=False), notice


TOOL_FUNCTIONS = {"add_to_program": add_to_program, "add_to_diet": add_to_diet}


def run_tool(db: sqlite3.Connection, name: str, arguments: str) -> tuple[str, str | None]:
    """Execute a tool call from the model; `arguments` is the raw JSON string it produced.

    Returns (result text for the model, notice for the user or None).
    """
    function = TOOL_FUNCTIONS.get(name)
    if function is None:
        return _error(f"неизвестный инструмент {name}")
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return _error("аргументы не являются корректным JSON")
    if not isinstance(args, dict):
        return _error("аргументы должны быть JSON-объектом")
    return function(db, args)
