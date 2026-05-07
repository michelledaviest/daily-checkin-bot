import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from telegram import Bot

from . import archive, hydration, monitoring, sheets, state, weather
from .config import (
    EVENING_HOUR,
    HYDRATION_NUDGE_HOURS,
    MORNING_HOUR,
    TELEGRAM_ALLOWED_CHAT_ID,
    TIMEZONE,
)
from .prompts import slot_opener
from .time_util import LOCAL_TZ, local_date_str

log = logging.getLogger(__name__)


async def _slot_already_logged(slot: str) -> bool:
    """Check today's row to see if this slot has already been committed.
    Returns False on any error so a flaky network doesn't suppress nudges."""
    try:
        row = await sheets.fetch_row_by_date(local_date_str())
    except Exception:
        log.exception("fetch_row_by_date failed during slot check; sending nudge anyway")
        return False
    if row is None:
        return False
    col = "morning_logged_at" if slot == "morning" else "evening_logged_at"
    return bool(row.get(col, "").strip())


def _start_slot_factory(bot: Bot, slot: str):
    async def _start_slot() -> None:
        chat_id = TELEGRAM_ALLOWED_CHAT_ID
        # Don't trample an in-progress conversation from earlier.
        existing = state.load(chat_id)
        if existing is not None and not existing.is_expired():
            log.info("Skipping %s nudge: conversation already in progress.", slot)
            return
        if await _slot_already_logged(slot):
            log.info("Skipping %s nudge: already logged today.", slot)
            return
        state.start(chat_id, slot)
        try:
            await bot.send_message(chat_id, slot_opener(slot))
        except Exception:
            log.exception("Failed to send %s nudge", slot)

    return _start_slot


def _hydration_nudge_factory(bot: Bot):
    async def _nudge() -> None:
        try:
            msg = await hydration.maybe_compose_nudge()
        except Exception:
            log.exception("hydration.maybe_compose_nudge raised")
            return
        if msg is None:
            return
        try:
            await bot.send_message(TELEGRAM_ALLOWED_CHAT_ID, msg)
        except Exception:
            log.exception("Failed to send hydration nudge")

    return _nudge


async def _weather_job() -> None:
    await weather.fetch_and_write(local_date_str())


async def _heartbeat_job() -> None:
    monitoring.heartbeat(success=True)


async def _daily_job() -> None:
    if archive.has_entries_today():
        monitoring.daily(success=True)
    else:
        log.warning("No check-ins logged today — not pinging daily check.")


def build(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=LOCAL_TZ)

    scheduler.add_job(
        _start_slot_factory(bot, "morning"),
        CronTrigger(hour=MORNING_HOUR, minute=0, timezone=LOCAL_TZ),
        id="morning_nudge",
        replace_existing=True,
    )
    scheduler.add_job(
        _weather_job,
        CronTrigger(hour=MORNING_HOUR, minute=0, timezone=LOCAL_TZ),
        id="weather_fetch",
        replace_existing=True,
    )
    scheduler.add_job(
        _start_slot_factory(bot, "evening"),
        CronTrigger(hour=EVENING_HOUR, minute=0, timezone=LOCAL_TZ),
        id="evening_nudge",
        replace_existing=True,
    )
    for hour in HYDRATION_NUDGE_HOURS:
        scheduler.add_job(
            _hydration_nudge_factory(bot),
            CronTrigger(hour=hour, minute=0, timezone=LOCAL_TZ),
            id=f"hydration_nudge_{hour}",
            replace_existing=True,
        )

    scheduler.add_job(
        _heartbeat_job,
        IntervalTrigger(minutes=30),
        id="heartbeat",
        replace_existing=True,
    )
    scheduler.add_job(
        _daily_job,
        CronTrigger(hour=23, minute=55, timezone=LOCAL_TZ),
        id="daily_report",
        replace_existing=True,
    )

    log.info(
        "Scheduler configured: morning %02d:00, evening %02d:00, tz=%s",
        MORNING_HOUR,
        EVENING_HOUR,
        TIMEZONE,
    )
    return scheduler
