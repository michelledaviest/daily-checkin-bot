import logging
from datetime import date
from typing import Iterable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import analytics, archive, date_parsing, gemini, hydration, sheets, state
from .config import MAX_TURNS, TELEGRAM_ALLOWED_CHAT_ID
from .prompts import CheckinResponse, UpdateResponse, required_fields, slot_opener
from .time_util import iso_utc, local_date_str, now_local, now_utc

log = logging.getLogger(__name__)


def _allowed(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.id == TELEGRAM_ALLOWED_CHAT_ID


# Which transcript column an updated metric should append its audit line to.
FIELD_SLOT_OWNER = {
    "sleep_hours": "morning",
    "water_oz": "evening",
    "mood_score": "evening",
    "body_notes": "evening",
    "desk_hours": "evening",
    "couch_bed_hours": "evening",
    "shoulder_pain": "evening",
    "neck_spasms": "evening",
    "migraine": "evening",
    "migraine_severity": "evening",
    "exercise_type": "evening",
    "exercise_minutes": "evening",
    "steps": "evening",
}

YES_WORDS = {"yes", "y", "yeah", "yep", "yup", "ok", "okay", "confirm", "go", "sure",
             "correct", "right", "do it", "do it.", "✓"}


# --- /log configuration -----------------------------------------------------

# Aliases the user can type. Maps to the canonical column name.
LOG_FIELD_ALIASES = {
    "water": "water_oz", "water_oz": "water_oz",
    "mood": "mood_score", "mood_score": "mood_score",
    "sleep": "sleep_hours", "sleep_hours": "sleep_hours",
    "desk": "desk_hours", "desk_hours": "desk_hours",
    "couch": "couch_bed_hours", "couch_bed": "couch_bed_hours",
    "couch_bed_hours": "couch_bed_hours", "bed": "couch_bed_hours",
    "shoulder": "shoulder_pain", "shoulder_pain": "shoulder_pain",
    "neck": "neck_spasms", "neck_spasms": "neck_spasms",
    "migraine": "migraine",
    "severity": "migraine_severity", "migraine_severity": "migraine_severity",
    "exercise": "exercise_type", "exercise_type": "exercise_type",
    "minutes": "exercise_minutes", "exercise_minutes": "exercise_minutes",
    "steps": "steps",
    "notes": "body_notes", "body": "body_notes", "body_notes": "body_notes",
}

# How to parse a value for each field.
LOG_FIELD_TYPES = {
    "water_oz": "float",
    "mood_score": "int",
    "sleep_hours": "float",
    "desk_hours": "float",
    "couch_bed_hours": "float",
    "shoulder_pain": "int",
    "neck_spasms": "bool",
    "migraine": "bool",
    "migraine_severity": "int",
    "exercise_type": "enum",
    "exercise_minutes": "float",
    "steps": "int",
    "body_notes": "string",
}

# Cumulative fields are added to whatever is already in the row; the rest replace.
ADDITIVE_FIELDS = {
    "water_oz", "steps", "desk_hours", "couch_bed_hours", "exercise_minutes",
}


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
        "Hi 👋 I'll check in with you at 9 AM and 7 PM ET.\n\n"
        "Reply with a voice note or text — I'll handle both.\n\n"
        "Commands:\n"
        "• /now morning|evening — start a check-in\n"
        "• /today — show today's row\n"
        "• /log <field> <value> — quick-log one number (e.g. /log water 32)\n"
        "• /migraine — analytics summary\n"
        "• /status — what's mid-flight\n"
        "• /cancel — abort an in-progress conversation",
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


async def cmd_migraine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    chat_id = update.effective_chat.id
    try:
        summary = await analytics.migraine_summary()
        await context.bot.send_message(chat_id, summary)
    except Exception:
        log.exception("/migraine failed")
        await context.bot.send_message(
            chat_id, "Couldn't pull the migraine summary right now."
        )


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


# --- /today + /log ---------------------------------------------------------

# Display order for /today and the diff lines — keep terse and readable.
_TODAY_DISPLAY_ORDER = [
    ("sleep_hours", "sleep", "h"),
    ("water_oz", "water", "oz"),
    ("mood_score", "mood", ""),
    ("body_notes", "body", ""),
    ("desk_hours", "desk", "h"),
    ("couch_bed_hours", "couch/bed", "h"),
    ("shoulder_pain", "shoulder", ""),
    ("neck_spasms", "neck spasms", ""),
    ("migraine", "migraine", ""),
    ("migraine_severity", "severity", ""),
    ("exercise_type", "exercise", ""),
    ("exercise_minutes", "exercise min", ""),
    ("steps", "steps", ""),
]


def _is_filled(raw: str) -> bool:
    return raw is not None and str(raw).strip() != ""


def _format_today(row: dict | None) -> str:
    today = local_date_str()
    if not row:
        return f"📅 Today ({today})\n\nNothing logged yet."

    lines = [f"📅 Today ({today})", ""]

    morning_done = _is_filled(row.get("morning_logged_at", ""))
    evening_done = _is_filled(row.get("evening_logged_at", ""))
    badge = []
    if morning_done:
        badge.append("morning ✓")
    if evening_done:
        badge.append("evening ✓")
    if badge:
        lines.append(" · ".join(badge))
        lines.append("")

    filled, missing = [], []
    for col, label, suffix in _TODAY_DISPLAY_ORDER:
        raw = row.get(col, "")
        if not _is_filled(raw):
            missing.append(label)
            continue
        # Format: try to clean up integer-looking floats; honor booleans.
        v = str(raw).strip()
        if v.upper() == "TRUE":
            display = "yes"
        elif v.upper() == "FALSE":
            display = "no"
        else:
            try:
                f = float(v)
                display = f"{int(f)}" if f.is_integer() else f"{f:g}"
            except ValueError:
                display = v
        filled.append(f"✓ {label} {display}{suffix}".rstrip())

    if filled:
        lines.extend(filled)
    if missing:
        lines.append("")
        lines.append("Missing: " + ", ".join(missing))
    return "\n".join(lines)


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    chat_id = update.effective_chat.id
    try:
        row = await sheets.fetch_row_by_date(local_date_str())
    except Exception:
        log.exception("/today fetch failed")
        await context.bot.send_message(chat_id, "Couldn't pull the sheet right now.")
        return
    await context.bot.send_message(chat_id, _format_today(row or {}))


def _parse_log_value(raw: str, type_: str) -> object:
    raw = raw.strip()
    if type_ == "bool":
        s = raw.lower()
        if s in ("yes", "y", "true", "t", "1"):
            return True
        if s in ("no", "n", "false", "f", "0"):
            return False
        raise ValueError(f"expected yes/no, got {raw!r}")
    if type_ == "int":
        return int(float(raw))
    if type_ == "float":
        return float(raw)
    if type_ == "enum":
        from .config import EXERCISE_TYPES
        s = raw.lower()
        if s not in EXERCISE_TYPES:
            raise ValueError(f"expected one of {EXERCISE_TYPES}, got {raw!r}")
        return s
    if type_ == "string":
        return raw
    raise ValueError(f"unknown type {type_}")


def _existing_numeric(row: dict | None, col: str) -> float:
    if not row:
        return 0.0
    raw = row.get(col, "")
    try:
        return float(raw)
    except (ValueError, TypeError):
        return 0.0


async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    chat_id = update.effective_chat.id
    args = context.args or []

    if len(args) < 2:
        await context.bot.send_message(
            chat_id,
            "Usage: /log <field> <value>\n"
            "Examples:\n"
            "  /log water 32\n"
            "  /log mood 7\n"
            "  /log sleep 7.5\n"
            "  /log shoulder 6\n"
            "  /log neck no\n"
            "  /log exercise spin\n"
            "  /log minutes 45\n"
            "  /log steps 8400",
        )
        return

    raw_field = args[0].lower()
    raw_value = " ".join(args[1:])

    field = LOG_FIELD_ALIASES.get(raw_field)
    if field is None:
        valid = sorted({a for a in LOG_FIELD_ALIASES})
        await context.bot.send_message(
            chat_id,
            f"unknown field {raw_field!r}. Valid fields: {', '.join(valid)}",
        )
        return

    type_ = LOG_FIELD_TYPES[field]
    try:
        value = _parse_log_value(raw_value, type_)
    except ValueError as e:
        await context.bot.send_message(chat_id, f"couldn't parse value: {e}")
        return

    today = local_date_str()
    try:
        before = await sheets.fetch_row_by_date(today) or {}
    except Exception:
        log.exception("/log fetch_row_by_date failed")
        await context.bot.send_message(chat_id, "Couldn't read the sheet right now.")
        return

    payload: dict = {}
    if field in ADDITIVE_FIELDS:
        new_total = _existing_numeric(before, field) + float(value)
        # Preserve int-ness for fields that are int-typed.
        payload[field] = int(new_total) if type_ == "int" else new_total
        running = payload[field]
    else:
        payload[field] = value
        running = value

    # Special-case: /log migraine no → also zero out severity.
    if field == "migraine" and value is False:
        payload["migraine_severity"] = 0

    try:
        await sheets.upsert_row(today, payload)
    except Exception:
        log.exception("/log upsert failed")
        await context.bot.send_message(
            chat_id, "Sheet write failed — try again in a moment."
        )
        return

    if field == "water_oz":
        try:
            hydration.set_last_log_now()
        except Exception:
            log.exception("set_last_log_now failed; ignoring")

    # Build reply.
    label, unit = next(
        ((l, s) for c, l, s in _TODAY_DISPLAY_ORDER if c == field),
        (field, ""),
    )
    if field in ADDITIVE_FIELDS:
        added = int(value) if type_ == "int" else float(value)
        added_str = f"{int(added)}" if float(added).is_integer() else f"{added:g}"
        running_str = f"{int(running)}" if float(running).is_integer() else f"{running:g}"
        await context.bot.send_message(
            chat_id, f"+{added_str}{unit} {label} → {running_str}{unit} today."
        )
    else:
        if isinstance(value, bool):
            v_str = "yes" if value else "no"
        else:
            try:
                v_str = (
                    f"{int(float(value))}" if float(value).is_integer() else f"{float(value):g}"
                )
            except (ValueError, TypeError):
                v_str = str(value)
        await context.bot.send_message(chat_id, f"{label} = {v_str}{unit}.")


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
    """Shared path for voice and text replies. Dispatches to checkin or update flow."""
    if audio_bytes is None and text is None:
        return

    s = state.load(chat_id)

    # No active state → run the router to classify intent.
    if s is None:
        try:
            decision = await gemini.route(audio_bytes=audio_bytes, text=text)
        except Exception:
            log.exception("router call failed; defaulting to checkin")
            decision = None

        intent = decision.intent if decision is not None else "checkin"
        if decision is not None:
            log.info("router decision: %s (%s)", decision.intent, decision.reason)

        if intent == "update":
            s = state.start_update(chat_id)
        else:
            s = state.start(chat_id, _autostart_slot())

    # Confirm-yes shortcut: if the user typed "yes" while we're waiting for confirmation,
    # apply the proposed update without re-parsing.
    if (
        s.mode == "update"
        and s.awaiting_confirmation
        and text is not None
        and text.strip().lower() in YES_WORDS
    ):
        await _apply_update(chat_id, s, context)
        return

    if s.turn_count() >= MAX_TURNS:
        await context.bot.send_message(
            chat_id,
            "Hit the per-conversation turn limit. Saving what I have and starting "
            "fresh — use /now to retry.",
        )
        if s.mode == "checkin":
            await _commit(chat_id, s, context, force=True)
        else:
            state.clear(chat_id)
        return

    if audio_bytes is not None:
        state.append_user_audio(s, audio_bytes)
    elif text is not None:
        state.append_user_text(s, text)
    state.save(s)

    if s.mode == "update":
        await _process_update_turn(chat_id, s, context)
    else:
        await _process_checkin_turn(chat_id, s, context)


async def _process_checkin_turn(
    chat_id: int,
    s: state.ConversationState,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    try:
        resp: CheckinResponse = await gemini.call(s)
    except Exception:
        log.exception("gemini call failed")
        await context.bot.send_message(
            chat_id, "AI hiccup — try again in a moment please."
        )
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


async def _process_update_turn(
    chat_id: int,
    s: state.ConversationState,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    try:
        resp: UpdateResponse = await gemini.call_update(s)
    except Exception:
        log.exception("gemini.call_update failed")
        await context.bot.send_message(
            chat_id, "AI hiccup — try again in a moment please."
        )
        s.turns.pop()
        state.save(s)
        return

    state.append_model_reply(s, resp.reply)

    # Merge field updates. Drop the transcript-delta field — it's a check-in
    # artifact and shouldn't land in the sheet during an update.
    fu = resp.field_updates.model_dump()
    fu.pop("raw_transcript_delta", None)
    for k, v in fu.items():
        if v is not None:
            s.partial_fields[k] = v

    # Resolve the date phrase if Gemini returned one.
    if resp.target_date_phrase:
        resolved = date_parsing.resolve_date_phrase(
            resp.target_date_phrase, now_local().date()
        )
        if resolved is not None:
            s.target_date = resolved.isoformat()

    # If we have everything we need, propose the update.
    if resp.done and s.target_date and any(
        v is not None for v in s.partial_fields.values()
    ):
        await _propose_update(chat_id, s, context)
        return

    state.save(s)
    fallback_msg = resp.reply or (
        "Which date and field would you like to update?"
        if not s.target_date
        else "What value(s)?"
    )
    await context.bot.send_message(chat_id, fallback_msg)


def _fmt_value(v) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        return f"{int(v)}"
    return str(v)


def _diff_text(target_date: str, before_row: dict, updates: dict) -> str:
    """Render 'water_oz 45 → 64, shoulder_pain 5 → 6' for the proposal/result message."""
    parts = []
    for k, v in updates.items():
        if v is None:
            continue
        before = before_row.get(k, "") if before_row else ""
        parts.append(f"{k} {_fmt_value(before)} → {_fmt_value(v)}")
    return ", ".join(parts)


async def _propose_update(
    chat_id: int,
    s: state.ConversationState,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Read the existing row, build the diff, send a confirmation with Yes/No buttons."""
    assert s.target_date is not None
    try:
        before = await sheets.fetch_row_by_date(s.target_date) or {}
    except Exception:
        log.exception("fetch_row_by_date failed")
        await context.bot.send_message(
            chat_id, "Couldn't read the sheet right now — try again in a moment."
        )
        return

    if not before:
        await context.bot.send_message(
            chat_id,
            f"no row exists for {s.target_date}, can't update.",
        )
        state.clear(chat_id)
        return

    diff = _diff_text(s.target_date, before, s.partial_fields)
    if not diff:
        await context.bot.send_message(chat_id, "Nothing to update.")
        state.clear(chat_id)
        return

    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Yes ✓", callback_data="update:yes"),
            InlineKeyboardButton("No ✗", callback_data="update:no"),
        ]]
    )
    msg = await context.bot.send_message(
        chat_id,
        f"Confirm: update {s.target_date} — {diff}?",
        reply_markup=keyboard,
    )

    s.awaiting_confirmation = True
    s.propose_message_id = msg.message_id
    state.save(s)


def _audit_line(target_date: str, before_row: dict, updates: dict) -> str:
    diff = _diff_text(target_date, before_row, updates)
    return f"[updated {now_local().date().isoformat()}: {diff}]"


def _audit_target_column(updated_field_names: list[str]) -> str:
    """Pick which transcript column the audit line goes in."""
    owners = {FIELD_SLOT_OWNER.get(f, "evening") for f in updated_field_names}
    return "morning_transcript" if owners == {"morning"} else "evening_transcript"


async def _apply_update(
    chat_id: int,
    s: state.ConversationState,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Write the proposed update to the sheet, prepend audit line, edit the
    proposal message in place, clear state."""
    if not s.target_date:
        await context.bot.send_message(chat_id, "Internal error: no target date.")
        state.clear(chat_id)
        return

    updates = {k: v for k, v in s.partial_fields.items() if v is not None}
    if not updates:
        await context.bot.send_message(chat_id, "Nothing to update.")
        state.clear(chat_id)
        return

    try:
        before = await sheets.fetch_row_by_date(s.target_date) or {}
    except Exception:
        log.exception("fetch_row_by_date failed during apply")
        await context.bot.send_message(
            chat_id, "Couldn't read the sheet — try again."
        )
        return

    if not before:
        await context.bot.send_message(
            chat_id, f"no row exists for {s.target_date}, can't update."
        )
        state.clear(chat_id)
        return

    audit = _audit_line(s.target_date, before, updates)
    transcript_col = _audit_target_column(list(updates.keys()))
    existing_transcript = before.get(transcript_col, "") or ""
    new_transcript = (
        f"{audit} {existing_transcript}".strip()
        if existing_transcript
        else audit
    )

    payload = {**updates, transcript_col: new_transcript}

    try:
        ok = await sheets.update_existing_row(s.target_date, payload)
    except Exception:
        log.exception("sheets update_existing_row failed")
        await context.bot.send_message(
            chat_id, "Sheet write failed — try again in a moment."
        )
        return

    diff = _diff_text(s.target_date, before, updates)
    final_text = (
        f"Updated {s.target_date}: {diff}." if ok else
        f"no row exists for {s.target_date}, can't update."
    )

    # Edit the proposal message in place when possible.
    if s.propose_message_id is not None:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=s.propose_message_id,
                text=final_text,
            )
        except Exception:
            await context.bot.send_message(chat_id, final_text)
    else:
        await context.bot.send_message(chat_id, final_text)

    state.clear(chat_id)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Yes/No taps on the update confirmation message."""
    query = update.callback_query
    if query is None:
        return
    if not _allowed(update):
        await query.answer("Not authorized.")
        return
    await query.answer()  # remove the loading spinner
    chat_id = update.effective_chat.id
    data = query.data or ""

    if data == "update:yes":
        s = state.load(chat_id)
        if s is None or s.mode != "update" or not s.awaiting_confirmation:
            try:
                await query.edit_message_text("(this confirmation has expired)")
            except Exception:
                pass
            return
        await _apply_update(chat_id, s, context)
        return

    if data == "update:no":
        try:
            await query.edit_message_text(
                "Cancelled. Send the correction or /cancel to abandon."
            )
        except Exception:
            pass
        state.clear(chat_id)
        return


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
        message = f"logged ✓ {summary}"

        if s.slot == "evening":
            try:
                streak_lines = await analytics.streak_summary()
            except Exception:
                log.exception("streak_summary failed; sending bare confirmation")
                streak_lines = []
            if streak_lines:
                message = message + "\n\n" + "\n".join(streak_lines)

        await context.bot.send_message(chat_id, message)
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
    app.add_handler(CommandHandler("migraine", cmd_migraine))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("log", cmd_log))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_text)
    )
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^update:(yes|no)$"))
