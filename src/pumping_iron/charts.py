"""Small server-side SVG charts for the statistics page: bars and a line.

The charts are drawn as inline SVG (no script, no external library, no request), coloured
through CSS classes (`.chart`, `.col`, `.line`, `.dot`, `.ref`, `.axis` in style.css) so they
follow the section's plate colour. Every bar and point carries a <title>, which browsers show
as a tooltip, and the whole figure has an `aria-label` for screen readers.
"""

from datetime import date
from math import ceil

from markupsafe import Markup, escape

# viewBox size and the empty margins around the plot; the SVG scales to the width of its box.
W, H = 480, 170
PAD_L, PAD_R, PAD_T, PAD_B = 44, 8, 18, 24
MAX_X_LABELS = 6


def _fmt(value: float) -> str:
    """Format a number for labels: no trailing zeros, a thin space between thousands."""
    return f"{value:,.1f}".rstrip("0").rstrip(".").replace(",", " ")


def _open(title: str) -> str:
    """Return the opening <svg> tag with the accessible name."""
    return f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="{escape(title)}">'


def _x_labels(labels: list[str], centers: list[float]) -> str:
    """Return <text> elements under the plot for about MAX_X_LABELS evenly spread labels."""
    step = ceil(len(labels) / MAX_X_LABELS) or 1
    return "".join(
        f'<text x="{centers[i]:.1f}" y="{H - 6}" text-anchor="middle">{escape(labels[i])}</text>'
        for i in range(0, len(labels), step)
    )


def bar_chart(values: list[float], labels: list[str], title: str, unit: str = "",
              reference: float | None = None) -> Markup:
    """Draw one bar per value; `labels[i]` names bar i (x axis and tooltip).

    `unit` is appended to the tooltip and the top label, `reference` draws a dashed horizontal
    line (for example the planned calories). Zero values draw no bar. Returns the <svg> markup.
    """
    top = max(max(values, default=0), reference or 0) or 1
    plot_w, plot_h = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    slot = plot_w / max(len(values), 1)
    bar_w = slot * 0.7
    base_y = PAD_T + plot_h
    parts = [_open(title), f'<line class="axis" x1="{PAD_L}" x2="{W - PAD_R}" y1="{base_y}" y2="{base_y}"/>']
    centers = []
    for i, (value, label) in enumerate(zip(values, labels)):
        centers.append(PAD_L + i * slot + slot / 2)
        if value > 0:
            height = plot_h * value / top
            parts.append(
                f'<rect class="col" x="{centers[i] - bar_w / 2:.1f}" y="{base_y - height:.1f}" '
                f'width="{bar_w:.1f}" height="{height:.1f}"><title>{escape(label)}: {_fmt(value)} {escape(unit)}</title></rect>'
            )
    if reference:
        y = base_y - plot_h * reference / top
        parts.append(f'<line class="ref" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y:.1f}" y2="{y:.1f}"/>'
                     f'<text x="{PAD_L - 4}" y="{y + 4:.1f}" text-anchor="end">план</text>')
    parts.append(f'<text x="{PAD_L - 4}" y="{PAD_T + 4}" text-anchor="end">{_fmt(top)}</text>')
    parts.append(f'<text x="{PAD_L - 4}" y="{base_y + 4}" text-anchor="end">0</text>')
    parts.append(_x_labels(labels, centers))
    parts.append("</svg>")
    return Markup("".join(parts))


def line_chart(points: list[tuple[date, float]], title: str, unit: str = "") -> Markup:
    """Draw values over time; the x position follows the real dates (gaps stay gaps).

    `points` is [(date, value)] sorted by date. The y axis spans the smallest to the largest
    value with a small margin and is labelled with those two values; the first and last date
    label the x axis. Returns the <svg> markup.
    """
    plot_w, plot_h = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    values = [v for _, v in points]
    lo, hi = min(values), max(values)
    margin = (hi - lo) * 0.1 or max(abs(hi) * 0.05, 1)
    lo, hi = lo - margin, hi + margin
    first, last = points[0][0], points[-1][0]
    span = (last - first).days

    def x_of(day: date) -> float:
        return PAD_L + (plot_w * (day - first).days / span if span else plot_w / 2)

    def y_of(value: float) -> float:
        return PAD_T + plot_h * (hi - value) / (hi - lo)

    coords = [(x_of(day), y_of(value)) for day, value in points]
    parts = [_open(title), f'<line class="axis" x1="{PAD_L}" x2="{W - PAD_R}" y1="{PAD_T + plot_h}" y2="{PAD_T + plot_h}"/>']
    if len(coords) > 1:
        parts.append('<polyline class="line" points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in coords) + '"/>')
    for (day, value), (x, y) in zip(points, coords):
        parts.append(f'<circle class="dot" cx="{x:.1f}" cy="{y:.1f}" r="3.5"><title>{day:%d.%m.%Y}: {_fmt(value)} {escape(unit)}</title></circle>')
    parts.append(f'<text x="{PAD_L - 4}" y="{PAD_T + 4}" text-anchor="end">{_fmt(max(values))}</text>')
    parts.append(f'<text x="{PAD_L - 4}" y="{PAD_T + plot_h}" text-anchor="end">{_fmt(min(values))}</text>')
    parts.append(f'<text x="{PAD_L}" y="{H - 6}" text-anchor="start">{first:%d.%m.%y}</text>')
    if span:
        parts.append(f'<text x="{W - PAD_R}" y="{H - 6}" text-anchor="end">{last:%d.%m.%y}</text>')
    parts.append("</svg>")
    return Markup("".join(parts))
