"""SQLite storage: schema, seed data and a per-request connection dependency.

Tables:
  muscle_groups  groups of muscles; `movement` links complementary groups
                 (push/pull/legs/core), `antagonist_id` points to the opposing group
  exercises      exercise library, each belongs to one muscle group
  program_items  the training program: which exercise on which of its 3 days (0 = day 1)
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
    muscle_group_id INTEGER NOT NULL REFERENCES muscle_groups(id),
    description     TEXT NOT NULL DEFAULT ''   -- a few words shown under the name in the library
);
CREATE TABLE IF NOT EXISTS program_items (
    id          INTEGER PRIMARY KEY,
    day         INTEGER NOT NULL CHECK (day BETWEEN 0 AND 2),
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
CREATE INDEX IF NOT EXISTS idx_program_items_day ON program_items(day);
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

# (exercise, muscle group, short description shown in the library); `seed_exercises` adds the missing
# ones on every start and fills empty descriptions, names and descriptions already set are kept.
SEED_EXERCISES = [
    # Грудь
    ("Жим штанги лёжа", "Грудь", "базовое, вся грудь"),
    ("Жим гантелей на наклонной скамье", "Грудь", "верх груди"),
    ("Сведение рук в кроссовере", "Грудь", "изоляция груди"),
    ("Жим гантелей лёжа", "Грудь", "вся грудь, большая амплитуда"),
    ("Жим штанги на наклонной скамье", "Грудь", "верх груди"),
    ("Жим штанги на скамье головой вниз", "Грудь", "низ груди"),
    ("Жим гантелей на скамье головой вниз", "Грудь", "низ груди"),
    ("Жим в тренажёре на грудь", "Грудь", "вся грудь, безопасно"),
    ("Сведение рук в тренажёре (бабочка)", "Грудь", "изоляция груди"),
    ("Разведение гантелей лёжа", "Грудь", "растяжка груди"),
    ("Разведение гантелей на наклонной скамье", "Грудь", "растяжка верха груди"),
    ("Отжимания на брусьях (грудь)", "Грудь", "низ груди, наклон вперёд"),
    ("Отжимания от пола", "Грудь", "собственный вес"),
    ("Пуловер с гантелью", "Грудь", "грудь и широчайшие"),
    ("Сведение рук в кроссовере снизу вверх", "Грудь", "верх груди"),
    # Плечи
    ("Жим штанги стоя", "Плечи", "передняя и средняя дельта"),
    ("Махи гантелей в стороны", "Плечи", "средняя дельта"),
    ("Жим гантелей сидя", "Плечи", "все пучки дельт"),
    ("Жим Арнольда", "Плечи", "все пучки дельт, с разворотом"),
    ("Жим в тренажёре на плечи", "Плечи", "безопасный жим"),
    ("Махи гантелей перед собой", "Плечи", "передняя дельта"),
    ("Махи гантелей в наклоне", "Плечи", "задняя дельта"),
    ("Разведение рук в тренажёре на заднюю дельту", "Плечи", "задняя дельта"),
    ("Протяжка штанги к подбородку", "Плечи", "средняя дельта и трапеции"),
    ("Махи в сторону на нижнем блоке", "Плечи", "средняя дельта, ровная нагрузка"),
    ("Тяга каната к лицу", "Плечи", "задняя дельта, здоровье плеч"),
    ("Жим штанги от груди сидя", "Плечи", "передняя и средняя дельта"),
    # Трицепс
    ("Французский жим", "Трицепс", "трицепс лёжа"),
    ("Разгибания рук на блоке", "Трицепс", "изоляция трицепса"),
    ("Жим штанги узким хватом", "Трицепс", "трицепс и грудь"),
    ("Отжимания на брусьях (трицепс)", "Трицепс", "трицепс, корпус прямо"),
    ("Разгибание руки с гантелью из-за головы", "Трицепс", "длинная головка"),
    ("Французский жим с гантелью сидя", "Трицепс", "длинная головка"),
    ("Разгибание руки с гантелью в наклоне", "Трицепс", "изоляция, пик сокращения"),
    ("Разгибания рук с канатом на блоке", "Трицепс", "наружная головка"),
    ("Отжимания от скамьи сзади", "Трицепс", "собственный вес"),
    ("Разгибания рук на блоке обратным хватом", "Трицепс", "медиальная головка"),
    ("Жим гантелей узким хватом", "Трицепс", "трицепс и грудь"),
    ("Отжимания узким хватом", "Трицепс", "собственный вес"),
    # Спина
    ("Тяга штанги в наклоне", "Спина", "толщина спины"),
    ("Подтягивания", "Спина", "ширина спины"),
    ("Тяга верхнего блока", "Спина", "ширина спины"),
    ("Тяга нижнего блока к поясу", "Спина", "толщина спины"),
    ("Тяга гантели одной рукой в наклоне", "Спина", "широчайшие, по одной руке"),
    ("Тяга Т-грифа", "Спина", "середина спины"),
    ("Тяга в тренажёре сидя", "Спина", "середина спины"),
    ("Подтягивания обратным хватом", "Спина", "широчайшие и бицепс"),
    ("Тяга верхнего блока узким хватом", "Спина", "низ широчайших"),
    ("Тяга Пендли", "Спина", "взрывная тяга штанги"),
    ("Шраги со штангой", "Спина", "трапеции"),
    ("Шраги с гантелями", "Спина", "трапеции"),
    ("Пуловер на блоке прямыми руками", "Спина", "изоляция широчайших"),
    ("Становая тяга", "Спина", "вся задняя цепь"),
    ("Рычажная тяга в тренажёре", "Спина", "толщина спины"),
    ("Тяга штанги обратным хватом", "Спина", "низ спины и бицепс"),
    # Бицепс
    ("Подъём штанги на бицепс", "Бицепс", "базовое, масса бицепса"),
    ("Молотки", "Бицепс", "брахиалис и предплечья"),
    ("Подъём гантелей на бицепс стоя", "Бицепс", "бицепс с супинацией"),
    ("Подъём гантелей на бицепс сидя на наклонной скамье", "Бицепс", "растяжка бицепса"),
    ("Концентрированный подъём на бицепс", "Бицепс", "пик бицепса"),
    ("Сгибание рук на скамье Скотта", "Бицепс", "низ бицепса"),
    ("Подъём EZ-штанги на бицепс", "Бицепс", "бицепс, щадит запястья"),
    ("Сгибание рук на нижнем блоке", "Бицепс", "ровная нагрузка"),
    ("Сгибание рук в кроссовере", "Бицепс", "пик сокращения"),
    ("Молотки на блоке с канатом", "Бицепс", "брахиалис"),
    ("Подъём штанги обратным хватом", "Бицепс", "брахиалис и предплечья"),
    ("Сгибание рук в тренажёре", "Бицепс", "изоляция бицепса"),
    # Квадрицепс
    ("Приседания со штангой", "Квадрицепс", "базовое, ноги и ягодицы"),
    ("Жим ногами", "Квадрицепс", "квадрицепс без нагрузки на спину"),
    ("Фронтальные приседания", "Квадрицепс", "квадрицепс, корпус прямо"),
    ("Разгибание ног в тренажёре", "Квадрицепс", "изоляция квадрицепса"),
    ("Выпады с гантелями", "Квадрицепс", "квадрицепс и ягодицы"),
    ("Болгарские сплит-приседания", "Квадрицепс", "одна нога, квадрицепс"),
    ("Гакк-приседания", "Квадрицепс", "квадрицепс"),
    ("Приседания в тренажёре Смита", "Квадрицепс", "фиксированная траектория"),
    ("Гоблет-приседания", "Квадрицепс", "техника приседа"),
    ("Шаги на платформу", "Квадрицепс", "квадрицепс и ягодицы"),
    ("Выпады со штангой", "Квадрицепс", "квадрицепс и ягодицы"),
    ("Выпады назад", "Квадрицепс", "щадят колени"),
    # Бицепс бедра
    ("Румынская тяга", "Бицепс бедра", "задняя поверхность бедра"),
    ("Сгибание ног лёжа", "Бицепс бедра", "изоляция бицепса бедра"),
    ("Сгибание ног сидя", "Бицепс бедра", "изоляция бицепса бедра"),
    ("Сгибание ног стоя в тренажёре", "Бицепс бедра", "по одной ноге"),
    ("Становая тяга на прямых ногах", "Бицепс бедра", "растяжка бицепса бедра"),
    ("Румынская тяга с гантелями", "Бицепс бедра", "бицепс бедра и ягодицы"),
    ("Румынская тяга на одной ноге", "Бицепс бедра", "баланс и бицепс бедра"),
    # Ягодицы
    ("Ягодичный мостик", "Ягодицы", "изоляция ягодиц"),
    ("Ягодичный мостик со штангой", "Ягодицы", "ягодицы с весом"),
    ("Разведение ног в тренажёре", "Ягодицы", "средняя ягодичная"),
    ("Отведение ноги назад в кроссовере", "Ягодицы", "большая ягодичная"),
    ("Приседания сумо", "Ягодицы", "ягодицы и внутренняя часть бедра"),
    ("Махи ногой назад в тренажёре", "Ягодицы", "большая ягодичная"),
    ("Сведение ног в тренажёре", "Ягодицы", "внутренняя часть бедра"),
    ("Становая тяга сумо", "Ягодицы", "ягодицы и приводящие"),
    ("Отведение ноги в сторону на блоке", "Ягодицы", "средняя ягодичная"),
    # Икры
    ("Подъём на носки стоя", "Икры", "икроножная мышца"),
    ("Подъём на носки сидя", "Икры", "камбаловидная мышца"),
    ("Подъём на носки в тренажёре для жима ногами", "Икры", "икры, безопасно"),
    ("Подъём на носки в тренажёре Смита", "Икры", "икры с весом"),
    ("Подъём на носки на одной ноге с гантелью", "Икры", "по одной ноге"),
    ("Подъём на носки в наклоне (ослик)", "Икры", "икроножная, растяжка"),
    # Пресс
    ("Скручивания", "Пресс", "верх пресса"),
    ("Подъём ног в висе", "Пресс", "низ пресса"),
    ("Скручивания на блоке", "Пресс", "пресс с весом"),
    ("Планка", "Пресс", "статика, кор"),
    ("Боковая планка", "Пресс", "косые мышцы"),
    ("Подъём ног лёжа", "Пресс", "низ пресса"),
    ("Велосипед", "Пресс", "прямая и косые мышцы"),
    ("Скручивания на наклонной скамье", "Пресс", "пресс, нагрузка выше"),
    ("Подъём коленей в висе", "Пресс", "низ пресса, легче"),
    ("Складной нож", "Пресс", "весь пресс"),
    ("Ролик для пресса", "Пресс", "пресс и кор"),
    ("Русские скручивания", "Пресс", "косые мышцы"),
    ("Дровосек на блоке", "Пресс", "косые мышцы"),
    ("Скручивания в тренажёре", "Пресс", "пресс с весом"),
    # Низ спины
    ("Гиперэкстензия", "Низ спины", "разгибатели спины"),
    ("Гиперэкстензия с отягощением", "Низ спины", "разгибатели с весом"),
    ("Обратная гиперэкстензия", "Низ спины", "низ спины и ягодицы"),
    ("Супермен", "Низ спины", "собственный вес"),
    ("Наклоны со штангой на плечах", "Низ спины", "низ спины и бицепс бедра"),
    ("Разгибание спины в тренажёре", "Низ спины", "разгибатели спины"),
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


def seed_exercises(conn: sqlite3.Connection) -> None:
    """Add the library exercises that are missing and fill their empty descriptions.

    Names already present (also user-made ones) stay as they are, and so does a description
    that is already set. Runs on every start, so a database created with a shorter library
    gets the new exercises too.
    """
    conn.executemany(
        "INSERT OR IGNORE INTO exercises (name, muscle_group_id, description) "
        "VALUES (?, (SELECT id FROM muscle_groups WHERE name = ?), ?)",
        SEED_EXERCISES,
    )
    conn.executemany(
        "UPDATE exercises SET description = ? WHERE name = ? AND description = ''",
        [(description, name) for name, _, description in SEED_EXERCISES],
    )


def migrate_exercise_description(conn: sqlite3.Connection) -> None:
    """Add the `description` column to an exercise table created before it existed."""
    if not any(col["name"] == "description" for col in conn.execute("PRAGMA table_info(exercises)")):
        conn.execute("ALTER TABLE exercises ADD COLUMN description TEXT NOT NULL DEFAULT ''")


def migrate_program_days(conn: sqlite3.Connection) -> None:
    """Turn the weekday program (`weekday` 0-6) into a 3-day one (`day` 0-2) in an older database.

    The table is rebuilt because the CHECK range changes. Monday, Tuesday and Wednesday become days 1-3;
    rows of later weekdays are merged into day 3, so no exercise is lost. Does nothing on a new
    database or when the column is already `day`. Must run before the schema script, whose index uses `day`.
    """
    if not any(col["name"] == "weekday" for col in conn.execute("PRAGMA table_info(program_items)")):
        return
    conn.execute("ALTER TABLE program_items RENAME TO program_items_old")
    conn.execute("DROP INDEX IF EXISTS idx_program_items_weekday")
    conn.executescript(SCHEMA)  # creates the new program_items (the other tables exist and are skipped)
    conn.execute(
        """INSERT INTO program_items (id, day, exercise_id, sets, reps, weight_kg)
           SELECT id, MIN(weekday, 2), exercise_id, sets, reps, weight_kg FROM program_items_old ORDER BY id"""
    )
    conn.execute("DROP TABLE program_items_old")


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
            migrate_program_days(conn)
            conn.executescript(SCHEMA)
            migrate_diet_weekday(conn)
            migrate_exercise_description(conn)
            if not conn.execute("SELECT 1 FROM muscle_groups LIMIT 1").fetchone():
                seed_training(conn)
            seed_exercises(conn)
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
