"""Small inline SVG icons for the templates: `{{ icon('plus') }}`.

Each icon is the inside of a 24 x 24 stroke drawing; `icon()` wraps it in an <svg> that takes
the colour and size of the surrounding text (see `.icon` in style.css). The icons are drawn
for this project, there is no icon font or external request.
"""

from markupsafe import Markup

ICONS = {
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    "camera": '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
    "send": '<path d="M21 3L3 11l7 3 3 7z"/>',
    "dumbbell": '<path d="M6 7v10M3 9.5v5M18 7v10M21 9.5v5M6 12h12"/>',
    "notebook": '<path d="M6 3h12v18H6zM6 7H4M6 12H4M6 17H4M10 8h5"/>',
    "utensils": '<path d="M7 3v8M5 3v5a2 2 0 004 0V3M7 11v10M17 21V3c-2 1-3 4-3 8h3"/>',
    "ruler": '<path d="M3 15L15 3l6 6L9 21zM7 11l2 2M10 8l2 2M13 5l2 2"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.5"/><path d="M21 17l-5-5-9 8"/>',
    "calendar": '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/>',
    "moon": '<path d="M20 14.5A8 8 0 019.5 4a8 8 0 1010.5 10.5z"/>',
    "column": '<path d="M4 20h16M6 20V9M10 20V9M14 20V9M18 20V9M3 9h18L12 4z"/>',
    "flame": '<path d="M12 3c1 4 5 5 5 10a5 5 0 01-10 0c0-2 1-3 2-4 0 2 1 3 2 3 0-3-1-5 1-9z"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5h.01"/>',
}


def icon(name: str) -> Markup:
    """Return the <svg> markup of the icon `name` (a key of ICONS); raises KeyError for an unknown name."""
    return Markup(f'<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">{ICONS[name]}</svg>')
