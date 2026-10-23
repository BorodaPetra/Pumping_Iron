"""Shared web setup: the page list, Jinja templates and a page-render helper."""

from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from pumping_iron.icons import icon

BASE_DIR = Path(__file__).parent

# (url path, template name or None when a router renders the page, navbar title,
# plate colour, navbar icon name from icons.py); the order is the navbar and barbell
# order. The colour name maps to a `.plate-<name>` class in style.css.
PAGES = [
    ("/workouts", None, "Тренировки", "red", "dumbbell"),
    ("/body", None, "Показатели", "blue", "ruler"),
    ("/assistant", None, "АИ тренер", "yellow", "bot"),
    ("/stats", None, "Статистика", "white", "chart"),
    ("/nutrition", None, "Питание", "orange", "utensils"),
    ("/meditation", "meditation.html", "Медитации", "green", "moon"),
]

templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["nav"] = [
    {"path": path, "title": title, "plate": plate, "icon": icon_name} for path, _, title, plate, icon_name in PAGES
]
templates.env.globals["icon"] = icon


def page(request: Request, template: str, plate: str | None = None, **context) -> HTMLResponse:
    """Render `template` with the navbar context; `plate` colours the section, `context` is passed through."""
    return templates.TemplateResponse(
        request, template, {"current": request.url.path, "plate": plate, **context}
    )
