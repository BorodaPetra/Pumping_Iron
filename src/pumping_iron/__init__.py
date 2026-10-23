"""Pumping Iron: web app for tracking and visualising gym training."""

import uvicorn


def main() -> None:
    """Run the development server on http://127.0.0.1:8000."""
    uvicorn.run("pumping_iron.main:app", reload=True)
