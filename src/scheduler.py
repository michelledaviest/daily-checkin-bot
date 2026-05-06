import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from telegram import Bot

from . import archive, monitoring, state
from .config import (
    EVENING_HOUR,
    MORNING_HOUR,
    TELEGRAM_ALLOWED_CHAT_ID,
    TIMEZONE,
)
from .prompts import slot_opener
from .time_util import LOCAL_TZ

log = logging.getLogger(__name__)


def _start_slot_factory(bot: Bot, slot: str):
    async def _start_slot() -> None:
        chat_id = TELEGRAM_ALLOWED_CHAT_ID
        # Don't trample an in-progress conversation from earlier.
        existing = state.load(chat_id)
        if existing is not None and not existing.is_expired():
            log.info("Skipping %s nudge: conversation already in progress.", slot)
            return
        state.start(chat_id, slot)
        try:
            await bot.send_message(chat_id, slot_opener(slot))
        except Exception:
            log.exception("Failed to send %s nudge", slot)

    return _start_slot


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
        _start_slot_factory(bot, "evening"),
        CronTrigger(hour=EVENING_HOUR, minute=0, timezone=LOCAL_TZ),
        id="evening_nudge",
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
