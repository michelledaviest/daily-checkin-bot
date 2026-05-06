"""Hydration nudges: scheduled reminders to drink + log water.

The scheduler calls `maybe_compose_nudge()` at 11 AM / 2 PM / 5 PM ET (or whatever
hours are configured). It returns either a message to send, or None if any skip
condition applies (recent /log water, or today's goal already hit).

`/log water N` calls `set_last_log_now()` so the next scheduled nudge knows to
stay quiet within a 2h window.
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from . import sheets
from .config import STATE_DIR, WATER_GOAL_OZ
from .time_util import local_date_str, now_utc

log = logging.getLogger(__name__)

WATER_LOG_FILE: Path = STATE_DIR / "water_log.json"
SKIP_AFTER_LOG_SECONDS = 2 * 3600  # 2 hours


def set_last_log_now() -> None:
    """Best-effort write — failures are logged, never raised."""
    try:
        WATER_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        WATER_LOG_FILE.write_text(
            json.dumps({"last_log_at": now_utc().isoformat(timespec="seconds")})
        )
    except OSError:
        log.exception("Failed to write water log timestamp")


def _last_log_at() -> datetime | None:
    if not WATER_LOG_FILE.exists():
        return None
    try:
        data = json.loads(WATER_LOG_FILE.read_text())
        return datetime.fromisoformat(data["last_log_at"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        log.exception("Failed to read water log timestamp; treating as missing")
        return None


def _fmt_oz(v: float) -> str:
    return f"{int(v)}" if v.is_integer() else f"{v:g}"


async def maybe_compose_nudge() -> str | None:
    """Return the nudge text, or None if a skip condition applies."""
    # Skip 1: recent /log water within the suppression window.
    last = _last_log_at()
    if last is not None:
        delta = (now_utc() - last).total_seconds()
        if delta < SKIP_AFTER_LOG_SECONDS:
            log.info(
                "Skipping hydration nudge: last log was %.0f min ago", delta / 60
            )
            return None

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
