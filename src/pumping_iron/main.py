"""FastAPI application: home page and one page per main block."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pumping_iron.assistant import router as assistant_router

BASE_DIR = Path(__file__).parent

# (url path, template name, navbar title, plate colour); the order is the navbar
# and barbell order. The colour name maps to a `.plate-<name>` class in style.css.
PAGES = [
    ("/workouts", "workouts.html", "Тренировки", "red"),
    ("/body", "body.html", "Показатели", "blue"),
    ("/assistant", "assistant.html", "АИ тренер", "yellow"),
    ("/meditation", "meditation.html", "Медитации", "green"),
    ("/stats", "stats.html", "Статистика", "white"),
    ("/nutrition", "nutrition.html", "Питание", "orange"),
]

app = FastAPI(title="Pumping Iron")
app.include_router(assistant_router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["nav"] = [
    {"path": path, "title": title, "plate": plate} for path, _, title, plate in PAGES
]


def render(template: str, plate: str | None = None):
    """Build a GET handler that renders `template` with the shared navbar context."""

    async def handler(request: Request) -> HTMLResponse:
        context = {"current": request.url.path, "plate": plate}
        return templates.TemplateResponse(request, template, context)

    return handler


app.add_api_route("/", render("index.html"), response_class=HTMLResponse)
for path, template, _, plate in PAGES:
    app.add_api_route(path, render(template, plate), response_class=HTMLResponse)
