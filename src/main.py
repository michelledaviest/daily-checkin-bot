import logging
import signal

from telegram.ext import Application

from . import telegram_handlers
from .config import TELEGRAM_BOT_TOKEN, ensure_dirs
from .scheduler import build as build_scheduler

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)

log = logging.getLogger("checkin")


async def _on_startup(app: Application) -> None:
    scheduler = build_scheduler(app.bot)
    scheduler.start()
    app.bot_data["scheduler"] = scheduler
    log.info("Scheduler started.")


async def _on_shutdown(app: Application) -> None:
    scheduler = app.bot_data.get("scheduler")
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        log.info("Scheduler stopped.")


def main() -> None:
    ensure_dirs()
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_on_startup)
        .post_shutdown(_on_shutdown)
        .build()
    )
    telegram_handlers.register(app)
    log.info("Starting bot (long-polling)...")
    app.run_polling(stop_signals=(signal.SIGINT, signal.SIGTERM))


if __name__ == "__main__":
    main()
