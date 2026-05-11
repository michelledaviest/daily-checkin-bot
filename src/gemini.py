import asyncio
import logging

from google import genai
from google.genai import types

from .config import GEMINI_API_KEY, GEMINI_MODEL
from .prompts import (
    CheckinResponse,
    ROUTER_SYSTEM_PROMPT,
    RouterResponse,
    SYSTEM_PROMPT,
    UPDATE_SYSTEM_PROMPT,
    UpdateResponse,
    required_fields,
)
from .state import ConversationState
from .time_util import local_date_str

log = logging.getLogger(__name__)

_client = genai.Client(api_key=GEMINI_API_KEY)


def _build_contents(state: ConversationState) -> list[types.Content]:
    """Replay history as Gemini Contents. User turns carry inline OGG audio OR
    typed text; model turns carry the JSON they returned (so the model sees its
    own running tally)."""
    contents: list[types.Content] = []
    for turn in state.turns:
        if turn.role == "user" and turn.audio_path:
            with open(turn.audio_path, "rb") as f:
                audio_bytes = f.read()
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_bytes(
                            data=audio_bytes, mime_type="audio/ogg"
                        )
                    ],
                )
            )
        elif turn.role == "user" and turn.text:
            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=turn.text)],
                )
            )
        elif turn.role == "model" and turn.text:
            contents.append(
                types.Content(
                    role="model",
                    parts=[types.Part.from_text(text=turn.text)],
                )
            )
    return contents


def _slot_context_prefix(slot: str, prefilled: dict | None = None) -> str:
    all_required = required_fields(slot)
    prefilled = prefilled or {}
    remaining = [f for f in all_required if prefilled.get(f) is None]
    lines = [
        f"\n\nCurrent slot: {slot}.",
        f"Required fields for this slot: {', '.join(remaining) if remaining else 'none'}.",
        "Use these and only these as the completeness check.",
    ]
    already = {k: v for k, v in prefilled.items() if k in all_required and v is not None}
    if already:
        summary = ", ".join(f"{k}={v}" for k, v in already.items())
        lines.append(f"Already logged today — do NOT ask about these: {summary}.")
    return "\n".join(lines)


def _system_instruction(slot: str, prefilled: dict | None = None) -> str:
    return SYSTEM_PROMPT + _slot_context_prefix(slot, prefilled)


def _generate_sync(state: ConversationState) -> CheckinResponse:
    contents = _build_contents(state)
    config = types.GenerateContentConfig(
        system_instruction=_system_instruction(
            state.slot, state.partial_fields or None
        ),
        response_mime_type="application/json",
        response_schema=CheckinResponse,
        temperature=0.3,
    )
    resp = _client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=config,
    )
    parsed = resp.parsed
    if isinstance(parsed, CheckinResponse):
        return parsed
    if isinstance(parsed, dict):
        return CheckinResponse.model_validate(parsed)
    return CheckinResponse.model_validate_json(resp.text)


async def call(state: ConversationState) -> CheckinResponse:
    """Async wrapper around the sync SDK call so handlers don't block the event loop."""
    return await asyncio.to_thread(_generate_sync, state)


# --- Routing (intent classification) ---------------------------------------


def _route_sync(audio_bytes: bytes | None, text: str | None) -> RouterResponse:
    parts: list = []
    if audio_bytes is not None:
        parts.append(
            types.Part.from_bytes(data=audio_bytes, mime_type="audio/ogg")
        )
    if text:
        parts.append(types.Part.from_text(text=text))
    if not parts:
        # Defensive fallback — caller should always supply at least one.
        parts.append(types.Part.from_text(text=""))

    contents = [types.Content(role="user", parts=parts)]
    config = types.GenerateContentConfig(
        system_instruction=ROUTER_SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=RouterResponse,
        temperature=0.0,
    )
    resp = _client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=config,
    )
    parsed = resp.parsed
    if isinstance(parsed, RouterResponse):
        return parsed
    if isinstance(parsed, dict):
        return RouterResponse.model_validate(parsed)
    return RouterResponse.model_validate_json(resp.text)


async def route(
    *, audio_bytes: bytes | None = None, text: str | None = None
) -> RouterResponse:
    return await asyncio.to_thread(_route_sync, audio_bytes, text)


# --- Update flow -----------------------------------------------------------


def _update_system_instruction() -> str:
    return UPDATE_SYSTEM_PROMPT.format(today_local=local_date_str())


def _generate_update_sync(state: ConversationState) -> UpdateResponse:
    contents = _build_contents(state)
    config = types.GenerateContentConfig(
        system_instruction=_update_system_instruction(),
        response_mime_type="application/json",
        response_schema=UpdateResponse,
        temperature=0.2,
    )
    resp = _client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=config,
    )
    parsed = resp.parsed
    if isinstance(parsed, UpdateResponse):
        return parsed
    if isinstance(parsed, dict):
        return UpdateResponse.model_validate(parsed)
    return UpdateResponse.model_validate_json(resp.text)


async def call_update(state: ConversationState) -> UpdateResponse:
    return await asyncio.to_thread(_generate_update_sync, state)
