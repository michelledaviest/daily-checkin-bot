import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import AUDIO_DIR, STATE_DIR, STATE_TTL_HOURS
from .time_util import now_utc


@dataclass
class Turn:
    role: str  # "user" | "model"
    # User turns set EITHER audio_path (voice note) OR text (typed reply).
    # Model turns set text only.
    audio_path: str | None
    text: str | None
    timestamp: str  # ISO UTC


@dataclass
class ConversationState:
    chat_id: int
    slot: str  # "morning" | "evening" | "update"
    started_at: str  # ISO UTC
    turns: list[Turn] = field(default_factory=list)
    partial_fields: dict = field(default_factory=dict)
    raw_transcript: str = ""
    mode: str = "checkin"  # "checkin" | "update"
    target_date: str | None = None  # ISO date (YYYY-MM-DD) for update mode
    awaiting_confirmation: bool = False
    propose_message_id: int | None = None  # the bot message that has the Yes/No buttons

    def is_expired(self) -> bool:
        started = datetime.fromisoformat(self.started_at)
        return now_utc() - started > timedelta(hours=STATE_TTL_HOURS)

    def turn_count(self) -> int:
        return sum(1 for t in self.turns if t.role == "user")

    def duration_seconds(self) -> int:
        if not self.turns:
            return 0
        last = datetime.fromisoformat(self.turns[-1].timestamp)
        first = datetime.fromisoformat(self.started_at)
        return int((last - first).total_seconds())


def _state_path(chat_id: int) -> Path:
    return STATE_DIR / f"{chat_id}.json"


def _audio_dir(chat_id: int) -> Path:
    d = AUDIO_DIR / str(chat_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def load(chat_id: int) -> ConversationState | None:
    p = _state_path(chat_id)
    if not p.exists():
        return None
    raw = json.loads(p.read_text())
    state = ConversationState(
        chat_id=raw["chat_id"],
        slot=raw["slot"],
        started_at=raw["started_at"],
        turns=[Turn(**t) for t in raw["turns"]],
        partial_fields=raw.get("partial_fields", {}),
        raw_transcript=raw.get("raw_transcript", ""),
        mode=raw.get("mode", "checkin"),
        target_date=raw.get("target_date"),
        awaiting_confirmation=raw.get("awaiting_confirmation", False),
        propose_message_id=raw.get("propose_message_id"),
    )
    if state.is_expired():
        clear(chat_id)
        return None
    return state


def save(state: ConversationState) -> None:
    p = _state_path(state.chat_id)
    p.write_text(json.dumps(asdict(state), indent=2))


def clear(chat_id: int) -> None:
    p = _state_path(chat_id)
    if p.exists():
        p.unlink()
    audio_dir = AUDIO_DIR / str(chat_id)
    if audio_dir.exists():
        for f in audio_dir.iterdir():
            f.unlink()


def start(chat_id: int, slot: str, prefilled: dict | None = None) -> ConversationState:
    clear(chat_id)
    s = ConversationState(
        chat_id=chat_id,
        slot=slot,
        started_at=now_utc().isoformat(timespec="seconds"),
        mode="checkin",
    )
    if prefilled:
        for k, v in prefilled.items():
            if v is not None:
                s.partial_fields[k] = v
    save(s)
    return s


def start_update(chat_id: int) -> ConversationState:
    clear(chat_id)
    state = ConversationState(
        chat_id=chat_id,
        slot="update",
        started_at=now_utc().isoformat(timespec="seconds"),
        mode="update",
    )
    save(state)
    return state


def append_user_audio(state: ConversationState, audio_bytes: bytes) -> Turn:
    turn_idx = state.turn_count() + 1
    audio_path = _audio_dir(state.chat_id) / f"turn_{turn_idx:02d}.ogg"
    audio_path.write_bytes(audio_bytes)
    turn = Turn(
        role="user",
        audio_path=str(audio_path),
        text=None,
        timestamp=now_utc().isoformat(timespec="seconds"),
    )
    state.turns.append(turn)
    return turn


def append_user_text(state: ConversationState, text: str) -> Turn:
    turn = Turn(
        role="user",
        audio_path=None,
        text=text,
        timestamp=now_utc().isoformat(timespec="seconds"),
    )
    state.turns.append(turn)
    return turn


def append_model_reply(state: ConversationState, text: str) -> Turn:
    turn = Turn(
        role="model",
        audio_path=None,
        text=text,
        timestamp=now_utc().isoformat(timespec="seconds"),
    )
    state.turns.append(turn)
    return turn


def merge_fields(state: ConversationState, new_fields: dict) -> None:
    """Update partial_fields with non-null values from this turn, append transcript delta."""
    delta = new_fields.pop("raw_transcript_delta", "") or ""
    for k, v in new_fields.items():
        if v is not None:
            state.partial_fields[k] = v
    if delta:
        if state.raw_transcript:
            state.raw_transcript += " " + delta.strip()
        else:
            state.raw_transcript = delta.strip()
