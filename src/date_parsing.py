"""Resolve free-text date phrases ("yesterday", "last Monday", "May 4") to ISO dates."""
from datetime import date, datetime, timedelta

try:
    import dateparser as _dateparser
except ImportError:
    _dateparser = None


_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}


def _fallback_resolve(phrase: str, today: date) -> date | None:
    """Hand-rolled fallback for the most common phrases."""
    p = phrase.strip().lower()
    if p == "today":
        return today
    if p == "yesterday":
        return today - timedelta(days=1)
    if p in _WEEKDAYS:
        target_dow = _WEEKDAYS[p]
        # Most recent past occurrence (not today, even if today matches).
        delta = (today.weekday() - target_dow) % 7 or 7
        return today - timedelta(days=delta)
    if p.startswith("last "):
        rest = p[5:].strip()
        if rest in _WEEKDAYS:
            target_dow = _WEEKDAYS[rest]
            delta = (today.weekday() - target_dow) % 7 or 7
            return today - timedelta(days=delta)
    # Try ISO format
    try:
        return datetime.strptime(p, "%Y-%m-%d").date()
    except ValueError:
        return None


def resolve_date_phrase(phrase: str, today: date) -> date | None:
    """Resolve a phrase to a past or present date. Returns None if unparseable
    or if the resolved date is in the future."""
    if not phrase:
        return None

    resolved: date | None = None

    if _dateparser is not None:
        # `RELATIVE_BASE` lets us deterministically anchor "today"/"yesterday"
        # to the bot's local-time today, not the server clock.
        relative_base = datetime.combine(today, datetime.min.time())
        parsed = _dateparser.parse(
            phrase,
            settings={
                "RELATIVE_BASE": relative_base,
                "PREFER_DATES_FROM": "past",
                "RETURN_AS_TIMEZONE_AWARE": False,
            },
        )
        if parsed is not None:
            resolved = parsed.date()

    if resolved is None:
        resolved = _fallback_resolve(phrase, today)

    if resolved is None:
        return None
    if resolved > today:
        return None
    return resolved
