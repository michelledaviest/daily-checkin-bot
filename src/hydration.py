"""Hydration nudges: scheduled reminders to drink + log water.

The scheduler calls `maybe_compose_nudge()` at 11 AM / 2 PM / 5 PM ET (or whatever
hours are configured). It returns either a message to send, or None if the daily
goal has already been hit.
"""
import logging

from . import sheets
from .config import WATER_GOAL_OZ
from .time_util import local_date_str

log = logging.getLogger(__name__)


def _fmt_oz(v: float) -> str:
    return f"{int(v)}" if v.is_integer() else f"{v:g}"


async def maybe_compose_nudge() -> str | None:
    """Return the nudge text, or None if today's goal is already hit."""
    # Read today's water from the sheet. Fail open (still send the nudge) on
    # network errors — silence is worse than a possibly-redundant nudge.
    water = 0.0
    try:
        row = await sheets.fetch_row_by_date(local_date_str())
    except Exception:
        log.exception("hydration nudge: fetch_row_by_date failed; sending anyway")
        row = None

    if row is not None:
        raw = row.get("water_oz")
        if raw is not None and str(raw).strip() != "":
            try:
                water = float(raw)
            except (ValueError, TypeError):
                water = 0.0

    # Skip 2: already at goal.
    if water >= WATER_GOAL_OZ:
        log.info("Skipping hydration nudge: at goal (%s oz)", _fmt_oz(water))
        return None

    if water <= 0:
        return (
            "💧 Water break — nothing logged yet. "
            "Drink up, then `/log water N` to count it."
        )
    return (
        f"💧 Water break — {_fmt_oz(water)}oz logged so far. "
        f"Drink up, then `/log water N` to count it."
    )
