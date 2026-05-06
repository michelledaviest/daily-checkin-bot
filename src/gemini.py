import asyncio
import logging

from google import genai
from google.genai import types

from .config import GEMINI_API_KEY, GEMINI_MODEL
from .prompts import CheckinResponse, SYSTEM_PROMPT, required_fields
from .state import ConversationState

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


def _slot_context_prefix(slot: str) -> str:
    fields = ", ".join(required_fields(slot))
    return (
        f"\n\nCurrent slot: {slot}.\n"
        f"Required fields for this slot: {fields}.\n"
        f"Use these and only these as the completeness check."
    )


def _system_instruction(slot: str) -> str:
    return SYSTEM_PROMPT + _slot_context_prefix(slot)


def _generate_sync(state: ConversationState) -> CheckinResponse:
    contents = _build_contents(state)
    config = types.GenerateContentConfig(
        system_instruction=_system_instruction(state.slot),
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
