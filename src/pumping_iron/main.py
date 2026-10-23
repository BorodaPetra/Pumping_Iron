"""FastAPI application: home page, section pages and routers."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from pumping_iron.assistant import router as assistant_router
from pumping_iron.body import router as body_router
from pumping_iron.db import init_db
from pumping_iron.nutrition import router as nutrition_router
from pumping_iron.photos import router as photos_router
from pumping_iron.quotes import quote_of_the_day
from pumping_iron.web import BASE_DIR, PAGES, page
from pumping_iron.workouts import router as workouts_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Create and seed the SQLite database before the first request."""
    init_db()
    yield


app = FastAPI(title="Pumping Iron", lifespan=lifespan)
app.include_router(assistant_router)
app.include_router(workouts_router)
app.include_router(body_router)
app.include_router(photos_router)
app.include_router(nutrition_router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


def render(template: str, plate: str | None = None):
    """Build a GET handler that renders the static `template` with the shared navbar context."""

    async def handler(request: Request) -> HTMLResponse:
        return page(request, template, plate)

    return handler


async def index(request: Request) -> HTMLResponse:
    """Home page: the barbell plus the quote of the day."""
    return page(request, "index.html", quote=quote_of_the_day())


app.add_api_route("/", index, response_class=HTMLResponse)
for path, template, _, plate in PAGES:
    if template:  # pages with a template None are served by a router
        app.add_api_route(path, render(template, plate), response_class=HTMLResponse)
