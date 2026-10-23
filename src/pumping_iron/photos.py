"""Progress photos: private storage on disk plus a reminder to take a new one.

Files live in `data/photos/` next to the database under generated names; the table
`photos` keeps the date, pose and note. Images are served only through
`/body/photos/{id}/file`, so a client never chooses a path. A banner (see
`photo_reminder`, used by base.html) asks for a new photo every REMINDER_DAYS days.
It is shown when the app is open; nothing runs in the background.
"""

import sqlite3
import uuid
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, get_args

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from pumping_iron.db import DB_PATH, connect, get_db
from pumping_iron.web import page, templates

router = APIRouter(prefix="/body/photos")

Db = Annotated[sqlite3.Connection, Depends(get_db)]

PHOTOS_DIR = DB_PATH.parent / "photos"
MAX_PHOTO_BYTES = 10 * 1024 * 1024
REMINDER_DAYS = 14

Pose = Literal["Спереди", "Сбоку", "Сзади", "Другое"]
POSES = list(get_args(Pose))

# The extension and media type come from the file's first bytes, never from the
# uploaded name or the client's Content-Type.
SIGNATURES = [
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
]


def detect_image(data: bytes) -> tuple[str, str] | None:
    """Return (extension, media type) for JPEG, PNG or WebP data, or None for anything else."""
    for signature, extension, media_type in SIGNATURES:
        if data.startswith(signature):
            return extension, media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def days_word(n: int) -> str:
    """Return 'день', 'дня' or 'дней' for the number n."""
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "дня"
    return "дней"


def photo_reminder() -> str | None:
    """Return the reminder text when the last photo is REMINDER_DAYS or more days old, else None.

    Used from base.html on every page. With no photos at all nothing is shown: the
    photos page itself invites to add the first one.
    """
    # PERF: this opens a connection on every page render; caching the answer for a few
    # minutes (reset on upload/delete) would avoid it. MAX(taken_on) itself uses idx_photos_date.
    conn = connect()
    try:
        latest = conn.execute("SELECT MAX(taken_on) FROM photos").fetchone()[0]
    finally:
        conn.close()
    if latest is None:
        return None
    days = (date.today() - date.fromisoformat(latest)).days
    if days < REMINDER_DAYS:
        return None
    return f"Последнее фото прогресса было {days} {days_word(days)} назад. Пора сделать новое."


templates.env.globals["photo_reminder"] = photo_reminder


@router.get("", response_class=HTMLResponse)
def photos_page(request: Request, db: Db) -> HTMLResponse:
    """Show the upload form and all photos grouped by date, newest first."""
    groups: dict[str, list[sqlite3.Row]] = {}
    for row in db.execute("SELECT * FROM photos ORDER BY taken_on DESC, id DESC"):
        groups.setdefault(row["taken_on"], []).append(row)
    return page(request, "body_photos.html", "blue", groups=groups, poses=POSES,
                today=date.today().isoformat(), reminder_days=REMINDER_DAYS)


@router.post("")
def upload_photo(db: Db, file: UploadFile, taken_on: Annotated[date, Form()], pose: Annotated[Pose, Form()],
                 note: Annotated[str, Form(max_length=200)] = "") -> RedirectResponse:
    """Validate and store an uploaded photo.

    At most MAX_PHOTO_BYTES are read; larger files get 413. Only JPEG, PNG and WebP are
    accepted (checked by signature), future dates get 422. The file is saved under a
    random name; if the database insert fails the file is removed again.
    """
    if taken_on > date.today():
        raise HTTPException(422, "Дата фото в будущем")
    data = file.file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(413, f"Файл больше {MAX_PHOTO_BYTES // 1024 // 1024} МБ")
    detected = detect_image(data)
    if detected is None:
        raise HTTPException(422, "Нужен файл JPEG, PNG или WebP")

    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{detected[0]}"
    (PHOTOS_DIR / filename).write_bytes(data)
    try:
        with db:
            db.execute("INSERT INTO photos (taken_on, pose, filename, note) VALUES (?, ?, ?, ?)",
                       (taken_on.isoformat(), pose, filename, note.strip()))
    except sqlite3.Error:
        (PHOTOS_DIR / filename).unlink(missing_ok=True)
        raise
    return RedirectResponse("/body/photos", status_code=303)


@router.get("/{photo_id}/file")
def photo_file(photo_id: int, db: Db) -> FileResponse:
    """Send one stored image (404 if the id or the file is missing)."""
    row = db.execute("SELECT filename FROM photos WHERE id = ?", (photo_id,)).fetchone()
    path = PHOTOS_DIR / row["filename"] if row else None
    if path is None or not path.is_file():
        raise HTTPException(404, "Фото не найдено")
    media_type = detect_image(path.read_bytes()[:12])[1]
    return FileResponse(path, media_type=media_type,
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"})


@router.post("/{photo_id}/delete")
def delete_photo(photo_id: int, db: Db) -> RedirectResponse:
    """Delete the database row and the image file."""
    row = db.execute("SELECT filename FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Фото не найдено")
    with db:
        db.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
    (PHOTOS_DIR / row["filename"]).unlink(missing_ok=True)
    return RedirectResponse("/body/photos", status_code=303)
