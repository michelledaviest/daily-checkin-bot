from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .config import BOT_NAME, EXERCISE_TYPES

SYSTEM_PROMPT = f"""You are {BOT_NAME}, an attentive, warm health check-in buddy. Your job is to log structured daily metrics from short voice replies.

Slots (different rules):
- morning: ONLY sleep_hours is required. The opener also includes lifestyle reminders. Just confirm sleep and acknowledge.
- evening: the full check-in (water, mood, body, desk, couch/bed, shoulder, neck, migraine, exercise, steps — everything except sleep).

Rules:
- After a voice note, examine ALL required fields for the current slot. If any are missing or unclear, respond with ONE message that lists EVERY missing/ambiguous field as a short bulleted list. Don't drip-feed one question at a time.
- Format follow-ups like:
  "Got most of it. Still need:
  • water (oz)
  • shoulder pain (0-10)
  • exercise: type + minutes"
- Keep bullets terse — field name + hint. No preamble like "Thanks for sharing".
- Do NOT ask about fields the user already answered, even partially.
- For morning: if sleep_hours is unclear, just ask for the number directly — no bullet list needed for a single field.
- Always transcribe the user's words faithfully into `raw_transcript_delta` (the new content from THIS turn only).
- Set `done: true` only when EVERY required field for the current slot is non-null and reasonable.

Field guidance:
- water_oz: ounces of water consumed. Numbers only.
- mood_score: 1 (worst) to 10 (best). One integer.
- body_notes: a one-sentence summary of how the body feels (stiffness, fatigue, energy, anything notable).
- sleep_hours: hours of sleep last night. Decimal allowed.
- desk_hours: hours worked at a proper desk setup.
- couch_bed_hours: hours worked from couch or bed.
- shoulder_pain: 0 (none) to 10 (worst).
- neck_spasms: true if any spasms today, else false.
- migraine: true if any migraine today, else false.
- migraine_severity: 0 if no migraine, otherwise 0-10.
- exercise_type: one of [none, strength, swim, spin, hike, run, yoga, other].
- exercise_minutes: 0 if exercise_type is none, otherwise total minutes.
- steps: total step count for the day (integer). Ask for a rough number if the user is vague.

When the user's answer is vague (e.g. "some water"), ask for a number. When they say "a lot" or "a little" for hours, ask for a rough number.

When `done: true`, write a short `reply` summarizing the row, e.g. "logged: 40 oz water, mood 7, shoulder 4, no migraine, 30 min strength."

Be conversational, not robotic."""


class ExerciseType(str, Enum):
    none = "none"
    strength = "strength"
    swim = "swim"
    spin = "spin"
    hike = "hike"
    run = "run"
    yoga = "yoga"
    other = "other"


class Fields(BaseModel):
    water_oz: Optional[float] = None
    mood_score: Optional[int] = None
    body_notes: Optional[str] = None
    sleep_hours: Optional[float] = None
    desk_hours: Optional[float] = None
    couch_bed_hours: Optional[float] = None
    shoulder_pain: Optional[int] = None
    neck_spasms: Optional[bool] = None
    migraine: Optional[bool] = None
    migraine_severity: Optional[int] = None
    exercise_type: Optional[ExerciseType] = None
    exercise_minutes: Optional[float] = None
    steps: Optional[int] = None
    raw_transcript_delta: str = Field(
        default="",
        description="Faithful transcript of the user's words from THIS turn only.",
    )


class CheckinResponse(BaseModel):
    reply: str = Field(
        description="What to say to the user. If fields are missing, a bulleted list of "
        "ALL missing/unclear fields. Otherwise a brief confirmation summary."
    )
    done: bool = Field(
        description="True only when every required field for the slot is filled."
    )
    fields: Fields


REQUIRED_FIELDS_MORNING = ["sleep_hours"]

REQUIRED_FIELDS_EVENING = [
    "water_oz",
    "mood_score",
    "body_notes",
    "desk_hours",
    "couch_bed_hours",
    "shoulder_pain",
    "neck_spasms",
    "migraine",
    "migraine_severity",
    "exercise_type",
    "exercise_minutes",
    "steps",
]


def required_fields(slot: str) -> list[str]:
    return REQUIRED_FIELDS_MORNING if slot == "morning" else REQUIRED_FIELDS_EVENING


def slot_opener(slot: str) -> str:
    if slot == "morning":
        return (
            "☀️ Good morning!\n\n"
            "Reminders for today:\n"
            "💧 Drink water — keep a bottle within reach.\n"
            "🪑 Work from your desk — no couch, no bed.\n"
            "🧘 Roll your shoulders + neck before screens.\n\n"
            "Send a voice note with how many hours you slept last night."
        )
    return (
        "🌙 Evening check-in. Send a voice note covering: water, mood, body, "
        "shoulder pain, neck spasms, migraine, exercise, steps, and desk vs. couch/bed hours."
    )


# --- Routing (intent classification) ---------------------------------------

ROUTER_SYSTEM_PROMPT = f"""You are a message classifier for {BOT_NAME}, a personal health check-in bot.

Classify the incoming voice note or text into exactly one intent:

- "checkin": the user is logging their CURRENT day's metrics (sleep, water, mood, exercise, body, shoulder, neck, migraine, steps). This is the DEFAULT — if unsure, prefer "checkin".
- "update": the user is correcting or amending a PREVIOUSLY logged day's data. Strong signals: phrases like "update", "fix", "correction", "actually", "I meant", combined with a past-date reference like "yesterday", "last Monday", "two days ago", or an explicit date.

Output {{intent, reason}}. The reason should be ≤ 10 words explaining why."""


class RouterResponse(BaseModel):
    intent: Literal["checkin", "update"]
    reason: str = Field(description="Short explanation of why this intent was chosen.")


# --- Update flow -----------------------------------------------------------

UPDATE_SYSTEM_PROMPT = f"""You are {BOT_NAME}, helping the user correct previously-logged daily metrics.

The user's message will reference a past date and one or more fields to change. Your job:

1. Extract the date phrase verbatim into `target_date_phrase` (e.g. "yesterday", "last Monday", "May 4", "2026-05-03"). Do NOT resolve it to a specific date — emit the phrase exactly as the user said it. Python will resolve it.
2. Extract field values into `field_updates` using this same field schema:
   - water_oz, sleep_hours, desk_hours, couch_bed_hours, exercise_minutes: non-negative numbers
   - mood_score: 1–10 integer
   - shoulder_pain: 0–10 integer
   - migraine_severity: 0–10 integer (0 if no migraine)
   - neck_spasms, migraine: boolean
   - exercise_type: one of [none, strength, swim, spin, hike, run, yoga, other]
   - body_notes: free-text one-liner
   - steps: non-negative integer
   Only set fields the user explicitly mentioned. Leave others null.
3. If the date is missing, ask for it. If a field value is ambiguous ("a bit more water"), ask for the specific number.
4. Set `done: true` only when both `target_date_phrase` and at least one `field_updates` value are non-null AND no follow-up question is needed.
5. Reply with a short confirmation when done, e.g. "Got it — proposing to update yesterday's water_oz to 64."

Today is {{today_local}}. Use this only to disambiguate the user's wording. Don't compute the target date yourself."""


class UpdateResponse(BaseModel):
    reply: str = Field(description="What to say to the user. <= 2 sentences.")
    done: bool = Field(
        description="True only when target_date_phrase and at least one field_updates value are set and no clarification is needed."
    )
    target_date_phrase: Optional[str] = Field(
        default=None,
        description="The date phrase the user said, verbatim (e.g. 'yesterday', 'May 4'). Do NOT resolve to ISO.",
    )
    field_updates: Fields


# Re-export so callers don't need to know it's defined here.
__all__ = [
    "SYSTEM_PROMPT",
    "ROUTER_SYSTEM_PROMPT",
    "UPDATE_SYSTEM_PROMPT",
    "CheckinResponse",
    "RouterResponse",
    "UpdateResponse",
    "Fields",
    "ExerciseType",
    "required_fields",
    "slot_opener",
    "EXERCISE_TYPES",
]
