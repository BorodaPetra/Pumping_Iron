"""FastAPI application: home page and one page per main block."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).parent

# (url path, template name, navbar title); the order is the navbar order.
PAGES = [
    ("/workouts", "workouts.html", "Тренировки"),
    ("/body", "body.html", "Показатели"),
    ("/assistant", "assistant.html", "АИ тренер"),
    ("/meditation", "meditation.html", "Медитации"),
    ("/stats", "stats.html", "Статистика"),
]

app = FastAPI(title="Pumping Iron")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["nav"] = [(path, title) for path, _, title in PAGES]


def render(template: str):
    """Build a GET handler that renders `template` with the shared navbar context."""

    async def handler(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, template, {"current": request.url.path})

    return handler


app.add_api_route("/", render("index.html"), response_class=HTMLResponse)
for path, template, _ in PAGES:
    app.add_api_route(path, render(template), response_class=HTMLResponse)
