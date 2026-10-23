"""SQLite storage: schema, seed data and a per-request connection dependency.

Tables:
  muscle_groups  groups of muscles; `movement` links complementary groups
                 (push/pull/legs/core), `antagonist_id` points to the opposing group
  exercises      exercise library, each belongs to one muscle group
  program_items  the weekly plan: which exercise on which weekday (0 = Monday)
  log_entries    the training journal: one row per performed set
  diet_items     the weekly meal plan: foods per weekday (0 = Monday) and meal with calories and macros
  food_log       what was actually eaten, per date (nutrition monitoring)
  measure_types  reference list of body measurements (girths in cm, weight, body fat %)
  measurements   measured values per date and type, for the body block's dynamics
  photos         progress photos: metadata here, the image files in `data/photos/`

The database file path comes from env PUMPING_IRON_DB, default `data/pumping_iron.db`
in the project root (ignored by git).
"""

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from fastapi import HTTPException

DB_PATH = Path(os.environ.get("PUMPING_IRON_DB", Path(__file__).parents[2] / "data" / "pumping_iron.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS muscle_groups (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    movement      TEXT NOT NULL CHECK (movement IN ('push', 'pull', 'legs', 'core')),
    antagonist_id INTEGER REFERENCES muscle_groups(id)
);
CREATE TABLE IF NOT EXISTS exercises (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    muscle_group_id INTEGER NOT NULL REFERENCES muscle_groups(id)
);
CREATE TABLE IF NOT EXISTS program_items (
    id          INTEGER PRIMARY KEY,
    weekday     INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    exercise_id INTEGER NOT NULL REFERENCES exercises(id),
    sets        INTEGER NOT NULL CHECK (sets > 0),
    reps        INTEGER NOT NULL CHECK (reps > 0),
    weight_kg   REAL NOT NULL CHECK (weight_kg >= 0)
);
CREATE TABLE IF NOT EXISTS log_entries (
    id           INTEGER PRIMARY KEY,
    performed_on TEXT NOT NULL,          -- ISO date, YYYY-MM-DD
    exercise_id  INTEGER NOT NULL REFERENCES exercises(id),
    reps         INTEGER NOT NULL CHECK (reps > 0),
    weight_kg    REAL NOT NULL CHECK (weight_kg >= 0)
);
CREATE TABLE IF NOT EXISTS diet_items (
    id        INTEGER PRIMARY KEY,
    weekday   INTEGER NOT NULL DEFAULT 0 CHECK (weekday BETWEEN 0 AND 6),
    meal      TEXT NOT NULL CHECK (meal IN ('Завтрак', 'Обед', 'Ужин', 'Перекус')),
    food      TEXT NOT NULL,
    grams     INTEGER NOT NULL CHECK (grams > 0),
    kcal      INTEGER NOT NULL CHECK (kcal >= 0),
    protein_g REAL NOT NULL CHECK (protein_g >= 0),
    fat_g     REAL NOT NULL CHECK (fat_g >= 0),
    carbs_g   REAL NOT NULL CHECK (carbs_g >= 0)
);
CREATE TABLE IF NOT EXISTS food_log (
    id        INTEGER PRIMARY KEY,
    eaten_on  TEXT NOT NULL,             -- ISO date, YYYY-MM-DD
    meal      TEXT NOT NULL CHECK (meal IN ('Завтрак', 'Обед', 'Ужин', 'Перекус')),
    food      TEXT NOT NULL,
    grams     INTEGER NOT NULL CHECK (grams > 0),
    kcal      INTEGER NOT NULL CHECK (kcal >= 0),
    protein_g REAL NOT NULL CHECK (protein_g >= 0),
    fat_g     REAL NOT NULL CHECK (fat_g >= 0),
    carbs_g   REAL NOT NULL CHECK (carbs_g >= 0)
);
CREATE INDEX IF NOT EXISTS idx_food_log_date ON food_log(eaten_on);
CREATE TABLE IF NOT EXISTS measure_types (
    id      INTEGER PRIMARY KEY,
    key     TEXT NOT NULL UNIQUE,        -- stable slug used by the body figure and the input form
    name    TEXT NOT NULL UNIQUE,
    kind    TEXT NOT NULL CHECK (kind IN ('girth', 'weight', 'fat_pct')),
    unit    TEXT NOT NULL,
    min_val REAL NOT NULL,               -- plausible range, rejects typos like 3.8 instead of 38
    max_val REAL NOT NULL,
    better  TEXT NOT NULL CHECK (better IN ('up', 'down', 'none'))  -- which change is an improvement
);
CREATE TABLE IF NOT EXISTS measurements (
    id          INTEGER PRIMARY KEY,
    measured_on TEXT NOT NULL,           -- ISO date, YYYY-MM-DD
    type_id     INTEGER NOT NULL REFERENCES measure_types(id),
    value       REAL NOT NULL CHECK (value > 0),
    UNIQUE (measured_on, type_id)        -- one value per type and day; re-entering overwrites
);
CREATE INDEX IF NOT EXISTS idx_measurements_type_date ON measurements(type_id, measured_on);
CREATE TABLE IF NOT EXISTS photos (
    id       INTEGER PRIMARY KEY,
    taken_on TEXT NOT NULL,              -- ISO date, YYYY-MM-DD
    pose     TEXT NOT NULL CHECK (pose IN ('Спереди', 'Сбоку', 'Сзади', 'Другое')),
    filename TEXT NOT NULL UNIQUE,       -- generated name inside data/photos, never the uploaded one
    note     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_photos_date ON photos(taken_on);
CREATE INDEX IF NOT EXISTS idx_log_entries_date ON log_entries(performed_on);
CREATE INDEX IF NOT EXISTS idx_program_items_weekday ON program_items(weekday);
"""

# (name, movement); complementary groups share a movement.
SEED_GROUPS = [
    ("Грудь", "push"), ("Плечи", "push"), ("Трицепс", "push"),
    ("Спина", "pull"), ("Бицепс", "pull"),
    ("Квадрицепс", "legs"), ("Бицепс бедра", "legs"), ("Ягодицы", "legs"), ("Икры", "legs"),
    ("Пресс", "core"), ("Низ спины", "core"),
]

# Antagonist pairs; stored in both directions.
SEED_ANTAGONISTS = [
    ("Грудь", "Спина"), ("Бицепс", "Трицепс"),
    ("Квадрицепс", "Бицепс бедра"), ("Пресс", "Низ спины"),
]

# (exercise, muscle group)
SEED_EXERCISES = [
    ("Жим штанги лёжа", "Грудь"), ("Жим гантелей на наклонной скамье", "Грудь"),
    ("Сведение рук в кроссовере", "Грудь"),
    ("Жим штанги стоя", "Плечи"), ("Махи гантелей в стороны", "Плечи"),
    ("Французский жим", "Трицепс"), ("Разгибания рук на блоке", "Трицепс"),
    ("Тяга штанги в наклоне", "Спина"), ("Подтягивания", "Спина"), ("Тяга верхнего блока", "Спина"),
    ("Подъём штанги на бицепс", "Бицепс"), ("Молотки", "Бицепс"),
    ("Приседания со штангой", "Квадрицепс"), ("Жим ногами", "Квадрицепс"),
    ("Румынская тяга", "Бицепс бедра"), ("Сгибание ног лёжа", "Бицепс бедра"),
    ("Ягодичный мостик", "Ягодицы"),
    ("Подъём на носки стоя", "Икры"),
    ("Скручивания", "Пресс"), ("Подъём ног в висе", "Пресс"),
    ("Гиперэкстензия", "Низ спины"),
]


# (key, name, kind, unit, min, max, better). Girths are in cm, weight in kg, body fat in %.
SEED_MEASURE_TYPES = [
    ("neck", "Шея", "girth", "см", 25, 60, "up"),
    ("shoulders", "Плечи", "girth", "см", 80, 200, "up"),
    ("chest", "Грудь", "girth", "см", 60, 200, "up"),
    ("waist", "Талия", "girth", "см", 40, 200, "down"),
    ("hips", "Бёдра", "girth", "см", 60, 200, "none"),
    ("biceps_l", "Бицепс левый", "girth", "см", 15, 70, "up"),
    ("biceps_r", "Бицепс правый", "girth", "см", 15, 70, "up"),
    ("forearm_l", "Предплечье левое", "girth", "см", 15, 50, "up"),
    ("forearm_r", "Предплечье правое", "girth", "см", 15, 50, "up"),
    ("thigh_l", "Бедро левое", "girth", "см", 30, 100, "up"),
    ("thigh_r", "Бедро правое", "girth", "см", 30, 100, "up"),
    ("calf_l", "Голень левая", "girth", "см", 20, 70, "up"),
    ("calf_r", "Голень правая", "girth", "см", 20, 70, "up"),
    ("weight", "Вес", "weight", "кг", 30, 300, "none"),
    ("fat", "Жир", "fat_pct", "%", 3, 60, "down"),
]


def connect() -> sqlite3.Connection:
    """Open a connection with rows addressable by column name and foreign keys enforced.

    `check_same_thread=False` is needed because FastAPI may run the dependency and the
    sync endpoint in different worker threads; a connection is never shared between requests.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def seed_training(conn: sqlite3.Connection) -> None:
    """Fill the muscle groups, antagonist pairs and exercise library on an empty database."""
    conn.executemany("INSERT INTO muscle_groups (name, movement) VALUES (?, ?)", SEED_GROUPS)
    for a, b in SEED_ANTAGONISTS:
        conn.execute(
            "UPDATE muscle_groups SET antagonist_id = (SELECT id FROM muscle_groups WHERE name = ?) WHERE name = ?",
            (b, a),
        )
        conn.execute(
            "UPDATE muscle_groups SET antagonist_id = (SELECT id FROM muscle_groups WHERE name = ?) WHERE name = ?",
            (a, b),
        )
    conn.executemany(
        "INSERT INTO exercises (name, muscle_group_id) "
        "VALUES (?, (SELECT id FROM muscle_groups WHERE name = ?))",
        SEED_EXERCISES,
    )


def migrate_diet_weekday(conn: sqlite3.Connection) -> None:
    """Turn the old one-day meal plan into a weekly one in a database created before `weekday` existed.

    The old plan applied to every day, so its rows become Monday's and are copied to the other six days:
    the plan keeps meaning the same until the user edits a day. Does nothing when the column exists.
    """
    if any(col["name"] == "weekday" for col in conn.execute("PRAGMA table_info(diet_items)")):
        return
    conn.execute("ALTER TABLE diet_items ADD COLUMN weekday INTEGER NOT NULL DEFAULT 0 CHECK (weekday BETWEEN 0 AND 6)")
    for weekday in range(1, 7):
        conn.execute(
            """INSERT INTO diet_items (weekday, meal, food, grams, kcal, protein_g, fat_g, carbs_g)
               SELECT ?, meal, food, grams, kcal, protein_g, fat_g, carbs_g FROM diet_items WHERE weekday = 0""",
            (weekday,),
        )


def init_db() -> None:
    """Create the tables if missing and seed each reference table once.

    Every seed has its own emptiness check, so tables added later (like measure_types)
    are filled in databases that already exist.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        with conn:
            conn.executescript(SCHEMA)
            migrate_diet_weekday(conn)
            if not conn.execute("SELECT 1 FROM muscle_groups LIMIT 1").fetchone():
                seed_training(conn)
            if not conn.execute("SELECT 1 FROM measure_types LIMIT 1").fetchone():
                conn.executemany(
                    "INSERT INTO measure_types (key, name, kind, unit, min_val, max_val, better) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    SEED_MEASURE_TYPES,
                )
    finally:
        conn.close()


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: yield a connection for one request and close it afterwards.

    Endpoints commit their own writes (`with db:`) so the data is saved before the
    redirect response reaches the browser.
    """
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def write(db: sqlite3.Connection, sql: str, params: tuple = ()) -> sqlite3.Cursor:
    """Run one write statement in its own transaction and commit it.

    Takes the connection, the SQL and its parameters; returns the cursor.
    A violated constraint (unknown exercise, duplicate name) becomes HTTP 400.
    """
    try:
        with db:
            return db.execute(sql, params)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(400, f"Не удалось сохранить: {exc}") from exc
