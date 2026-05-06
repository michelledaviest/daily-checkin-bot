import logging
from typing import Iterable

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import archive, gemini, sheets, state
from .config import MAX_TURNS, TELEGRAM_ALLOWED_CHAT_ID
from .prompts import CheckinResponse, required_fields, slot_opener
from .time_util import iso_utc, local_date_str, now_utc

log = logging.getLogger(__name__)


def _allowed(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.id == TELEGRAM_ALLOWED_CHAT_ID


def _all_required_filled(slot: str, fields: dict) -> list[str]:
    """Return list of required field names that are still missing/null."""
    missing = []
    for f in required_fields(slot):
        v = fields.get(f)
        if v is None:
            missing.append(f)
    return missing


def _slot_fields(s: state.ConversationState) -> dict:
    """Return only the columns this slot owns. The day's row is upserted by date,
    so morning fills the morning columns and evening fills the rest."""
    f = s.partial_fields
    if s.slot == "morning":
        return {
            "morning_logged_at": iso_utc(),
            "sleep_hours": f.get("sleep_hours"),
            "morning_transcript": s.raw_transcript,
            "morning_turns": s.turn_count(),
        }
    return {
        "evening_logged_at": iso_utc(),
        "water_oz": f.get("water_oz"),
        "mood_score": f.get("mood_score"),
        "body_notes": f.get("body_notes"),
        "desk_hours": f.get("desk_hours"),
        "couch_bed_hours": f.get("couch_bed_hours"),
        "shoulder_pain": f.get("shoulder_pain"),
        "neck_spasms": f.get("neck_spasms"),
        "migraine": f.get("migraine"),
        "migraine_severity": f.get("migraine_severity"),
        "exercise_type": f.get("exercise_type"),
        "exercise_minutes": f.get("exercise_minutes"),
        "steps": f.get("steps"),
        "evening_transcript": s.raw_transcript,
        "evening_turns": s.turn_count(),
    }


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    chat = update.effective_chat
    await context.bot.send_message(
        chat.id,
        "Hi 👋 I'll check in with you at 9 AM and 7 PM ET. "
        "Reply with a voice note covering hydration, body, sleep, exercise, "
        "shoulder pain, neck, migraines, and desk hours. "
        "Use /now morning or /now evening to start one immediately, /cancel to abort.",
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    chat_id = update.effective_chat.id
    state.clear(chat_id)
    await context.bot.send_message(chat_id, "Cancelled.")


async def cmd_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    chat_id = update.effective_chat.id
    args: Iterable[str] = context.args or []
    slot = next(iter(args), "morning").lower()
    if slot not in ("morning", "evening"):
        slot = "morning"
    state.start(chat_id, slot)
    await context.bot.send_message(chat_id, slot_opener(slot))


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    chat_id = update.effective_chat.id
    s = state.load(chat_id)
    if s is None:
        await context.bot.send_message(
            chat_id,
            f"No active check-in. Last archive: "
            f"{'has entries' if archive.has_entries_today() else 'none'} for today.",
        )
        return
    missing = _all_required_filled(s.slot, s.partial_fields)
    msg = (
        f"In progress: slot={s.slot}, turns={s.turn_count()}, "
        f"missing={missing or 'none'}"
    )
    await context.bot.send_message(chat_id, msg)


def _autostart_slot() -> str:
    from .config import EVENING_HOUR, MORNING_HOUR
    from .time_util import now_local

    hour = now_local().hour
    return "morning" if hour < (MORNING_HOUR + EVENING_HOUR) // 2 else "evening"


async def _process_user_turn(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    audio_bytes: bytes | None = None,
    text: str | None = None,
) -> None:
    """Shared path for voice and text replies."""
    s = state.load(chat_id)
    if s is None:
        s = state.start(chat_id, _autostart_slot())

    if s.turn_count() >= MAX_TURNS:
        await context.bot.send_message(
            chat_id,
            "Hit the per-check-in turn limit. Saving what I have and starting "
            "fresh — use /now to retry.",
        )
        await _commit(chat_id, s, context, force=True)
        return

    if audio_bytes is not None:
        state.append_user_audio(s, audio_bytes)
    elif text is not None:
        state.append_user_text(s, text)
    else:
        return
    state.save(s)

    try:
        resp: CheckinResponse = await gemini.call(s)
    except Exception:
        log.exception("gemini call failed")
        await context.bot.send_message(
            chat_id, "AI hiccup — try again in a moment please."
        )
        # Roll back this user turn so history isn't left unanswered.
        s.turns.pop()
        state.save(s)
        return

    state.merge_fields(s, resp.fields.model_dump())
    state.append_model_reply(s, resp.reply)

    missing = _all_required_filled(s.slot, s.partial_fields)
    if resp.done and not missing:
        state.save(s)
        await _commit(chat_id, s, context)
        return

    if resp.done and missing:
        log.info(
            "Gemini said done but missing %s — overriding to ask follow-up.", missing
        )

    state.save(s)
    await context.bot.send_message(chat_id, resp.reply or "Got it — anything else?")


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    chat_id = update.effective_chat.id
    voice = update.message.voice or update.message.audio
    if voice is None:
        return
    file = await voice.get_file()
    audio_bytes = bytes(await file.download_as_bytearray())
    await _process_user_turn(chat_id, context, audio_bytes=audio_bytes)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    if not text:
        return
    await _process_user_turn(chat_id, context, text=text)


async def _commit(
    chat_id: int,
    s: state.ConversationState,
    context: ContextTypes.DEFAULT_TYPE,
    force: bool = False,
) -> None:
    fields = _slot_fields(s)
    today = local_date_str()
    archive.append({"local_date": today, "slot": s.slot, **fields})
    try:
        await sheets.upsert_row(today, fields)
        from . import monitoring

        monitoring.heartbeat(success=True)
        summary = _summary_line(s.slot, fields)
        await context.bot.send_message(chat_id, f"logged ✓ {summary}")
    except Exception:
        log.exception("sheets commit failed")
        await context.bot.send_message(
            chat_id,
            "Sheet write failed — saved locally. I'll retry in the background.",
        )
    finally:
        state.clear(chat_id)


def _summary_line(slot: str, fields: dict) -> str:
    parts: list[str] = []
    if slot == "morning":
        if fields.get("sleep_hours") is not None:
            parts.append(f"slept {fields['sleep_hours']:g}h")
        return ", ".join(parts) or "morning saved"
    if fields.get("water_oz") is not None:
        parts.append(f"{fields['water_oz']:g} oz")
    if fields.get("mood_score") is not None:
        parts.append(f"mood {fields['mood_score']}")
    if fields.get("shoulder_pain") is not None:
        parts.append(f"shoulder {fields['shoulder_pain']}")
    if fields.get("migraine"):
        parts.append(f"migraine {fields.get('migraine_severity', '?')}")
    elif fields.get("migraine") is False:
        parts.append("no migraine")
    if fields.get("exercise_type") and fields["exercise_type"] != "none":
        mins = fields.get("exercise_minutes") or 0
        parts.append(f"{mins:g}m {fields['exercise_type']}")
    if fields.get("steps") is not None:
        parts.append(f"{fields['steps']} steps")
    return ", ".join(parts) or "evening saved"


def register(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("now", cmd_now))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_text)
    )
