"""The page's icons: one hidden SVG sprite in the page, and `<use>`
references wherever an icon is drawn (the archive's panels repeat them
hundreds of times a day). Drawing attributes sit on each symbol, not in
CSS: page styles don't reach inside a `<use>`. Colour follows the text
(`currentColor`)."""

from __future__ import annotations

_FILLED = ' fill="currentColor" stroke="none"'
_STROKED = (
    ' fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"'
    ' stroke-linejoin="round"'
)

# name -> (filled, the symbol's body on a 24x24 grid)
ICONS: dict[str, tuple[bool, str]] = {
    "him": (False, '<circle cx="12" cy="8" r="4"/><path d="M4.5 21a7.5 7.5 0 0 1 15 0"/>'),
    "huxley": (False, '<path d="M4 5h16v11h-9l-5 4v-4H4z"/><path d="M8 10.5h8"/>'),
    "mic": (
        False,
        '<path d="M12 3a3 3 0 0 0-3 3v5a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z"/>'
        '<path d="M6 11a6 6 0 0 0 12 0M12 17v4"/>',
    ),
    "model": (False, '<path d="M4 12h1M8 9v6M12 5v14M16 8v8M20 11v2"/>'),
    "play": (True, '<path d="M8 5.5v13l10.5-6.5z"/>'),
    "pause": (True, '<path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z"/>'),
    "music": (
        False,
        '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
    ),
    "book": (
        False,
        '<path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z"/><path d="M5 17a3 3 0 0 1 3-3h11"/>',
    ),
    "radio": (
        False,
        '<rect x="3" y="8" width="18" height="12" rx="2"/><path d="M7 8l9-5"/>'
        '<circle cx="15.5" cy="14" r="2.5"/><path d="M6 12h4M6 16h4"/>',
    ),
    "clock": (False, '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
    "search": (False, '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>'),
    "phone": (
        False,
        '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2'
        'A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>',
    ),
    "bell": (False, '<path d="M6 16v-5a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 21h4"/>'),
    "cloud": (
        False,
        '<path d="M7 18h10a4 4 0 0 0 .4-8 5.5 5.5 0 0 0-10.6 1.4A3.3 3.3 0 0 0 7 18z"/>',
    ),
    "message": (
        False,
        '<path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-5 3.5V17.2A2.5 2.5 0 0 1 4 14.5z"/>',
    ),
    "chat": (False, '<path d="M5 19V7a3 3 0 0 1 3-3h8a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3H9l-4 3z"/>'),
    "volume": (
        False,
        '<path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16.5 9a4 4 0 0 1 0 6M19 6.5a8 8 0 0 1 0 11"/>',
    ),
    "news": (
        False,
        '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 8h10M7 12h10M7 16h6"/>',
    ),
    "battery": (
        False,
        '<rect x="2" y="7" width="18" height="10" rx="2"/><path d="M22 11v2M6 10v4M10 10v4"/>',
    ),
    "tool": (
        False,
        '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1'
        'M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>',
    ),
    "chart": (False, '<path d="M4 20h16M7 16v-5M12 16V6M17 16v-8"/>'),
    "walkie": (
        False,
        '<rect x="6.5" y="7" width="11" height="15" rx="2.5"/><path d="M9 7V2"/>'
        '<path d="M9.5 11h5M9.5 14h5"/><circle cx="12" cy="18.2" r="1"/>',
    ),
    "prev": (False, '<path d="M15 5l-7 7 7 7"/>'),
    "next": (False, '<path d="M9 5l7 7-7 7"/>'),
    "close": (False, '<path d="M6 6l12 12M18 6L6 18"/>'),
}


def sprite() -> str:
    """Every icon as a <symbol>, once per page."""
    symbols = "".join(
        f'<symbol id="i-{name}" viewBox="0 0 24 24"{_FILLED if filled else _STROKED}>{body}</symbol>'
        for name, (filled, body) in ICONS.items()
    )
    return (
        f'<svg width="0" height="0" style="position:absolute" aria-hidden="true">{symbols}</svg>'
    )


def icon(name: str, cls: str = "ico") -> str:
    if name not in ICONS:
        raise KeyError(name)
    return f'<svg class="{cls}" aria-hidden="true"><use href="#i-{name}"/></svg>'
