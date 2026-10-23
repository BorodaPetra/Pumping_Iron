"""Quote of the day for the home page.

`quotes.txt` holds 365 quotes, one per line: kind|author|source|russian|english|russian_by.
Kind `s` is a thought from the Stoics and other sages, `d` is a push from the gym world. Every
quote has an English and a Russian text; other languages are not shown. At load time each kind is
shuffled with a fixed seed and the kinds are interleaved evenly, so the day of the year picks the
same quote on every run and the authors do not come in long runs.
"""

import random
from datetime import date
from pathlib import Path
from typing import NamedTuple

QUOTES_FILE = Path(__file__).with_name("quotes.txt")

class Quote(NamedTuple):
    """One line of quotes.txt."""

    kind: str
    author: str
    source: str
    ru: str
    en: str
    ru_by: str  # translator of the Russian text; "Claude" when no published translation was found


def _load() -> list[Quote]:
    """Read quotes.txt, skipping blank lines and `#` comments."""
    lines = QUOTES_FILE.read_text(encoding="utf-8").splitlines()
    return [Quote(*line.split("|")) for line in lines if line and not line.startswith("#")]


def _interleave(quotes: list[Quote]) -> list[Quote]:
    """Shuffle each kind (seeded), then merge the kinds so each is spread evenly over the list."""
    rng = random.Random(365)
    slots: list[tuple[float, Quote]] = []
    for kind in sorted({q.kind for q in quotes}):
        group = [q for q in quotes if q.kind == kind]
        rng.shuffle(group)
        slots += [((i + 0.5) / len(group), q) for i, q in enumerate(group)]
    return [q for _, q in sorted(slots, key=lambda slot: slot[0])]


QUOTES = _interleave(_load())


def quote_of_the_day(today: date | None = None, shift: int = 0) -> Quote:
    """Return the quote for `today` (default: the current date), picked by the day of the year.

    `shift` moves forward through the list (the "next quote" button); it wraps around, as does
    day 366 of a leap year.
    """
    day = (today or date.today()).timetuple().tm_yday
    return QUOTES[(day - 1 + shift) % len(QUOTES)]
