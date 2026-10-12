"""Times and amounts the way a person says them."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo


def when(ts: datetime, now: datetime, tz: ZoneInfo) -> str:
    """'14:05', 'yesterday 21:10', 'Saturday 21:10', 'Oct 3, 21:10'."""
    local, today = ts.astimezone(tz), now.astimezone(tz)
    clock = local.strftime("%H:%M")
    days = (today.date() - local.date()).days
    if days == 0:
        return clock
    if days == 1:
        return f"yesterday {clock}"
    if 1 < days < 7:
        return f"{local.strftime('%A')} {clock}"
    return f"{local.strftime('%b')} {local.day}, {clock}"


def span(delta: timedelta) -> str:
    """'12 min', '3 h', '2 days'."""
    minutes = delta.total_seconds() / 60
    if minutes < 90:
        return f"{max(1, round(minutes))} min"
    hours = minutes / 60
    if hours < 36:
        return f"{round(hours)} h"
    days = round(hours / 24)
    return f"{days} day" if days == 1 else f"{days} days"


def hours(seconds: float) -> str:
    """'45 min', '2 h', '4.5 h'."""
    if seconds < 3600:
        return f"{round(seconds / 60)} min"
    h = seconds / 3600
    return f"{h:.0f} h" if h >= 10 or abs(h - round(h)) < 0.05 else f"{h:.1f} h"


def usd(amount: float) -> str:
    return f"USD {amount:.0f}" if abs(amount) >= 10 else f"USD {amount:.2f}"


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {many or word + 's'}"


def short(delta: timedelta) -> str:
    """A card's big number: '35 min', '42 h', '3 d'."""
    minutes = delta.total_seconds() / 60
    if minutes < 60:
        return f"{max(1, round(minutes))} min"
    if minutes < 72 * 60:
        return f"{round(minutes / 60)} h"
    return f"{round(minutes / 1440)} d"


def short_when(ts: datetime, now: datetime, tz: ZoneInfo) -> str:
    """A tile's time: '14:05' today, 'Sat 21:10' this week, 'Oct 3' before."""
    local, today = ts.astimezone(tz), now.astimezone(tz)
    days = (today.date() - local.date()).days
    if days == 0:
        return local.strftime("%H:%M")
    if days < 7:
        return f"{local.strftime('%a')} {local.strftime('%H:%M')}"
    return f"{local.strftime('%b')} {local.day}"


def money(amount: float) -> str:
    """'$3.74', '$18'."""
    return f"${amount:.0f}" if abs(amount) >= 100 else f"${amount:.2f}"
