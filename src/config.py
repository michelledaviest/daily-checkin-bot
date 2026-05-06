import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


TELEGRAM_BOT_TOKEN = _required("TELEGRAM_BOT_TOKEN")
TELEGRAM_ALLOWED_CHAT_ID = int(_required("TELEGRAM_ALLOWED_CHAT_ID"))

GEMINI_API_KEY = _required("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

GSA_KEY_PATH = _required("GSA_KEY_PATH")
SHEET_ID = _required("SHEET_ID")
SHEET_TAB = os.environ.get("SHEET_TAB", "checkins")

HC_HEARTBEAT_URL = os.environ.get("HC_HEARTBEAT_URL", "")
HC_DAILY_URL = os.environ.get("HC_DAILY_URL", "")

BOT_NAME = os.environ.get("BOT_NAME", "your check-in buddy")

MORNING_HOUR = _int("MORNING_HOUR", 9)
EVENING_HOUR = _int("EVENING_HOUR", 19)
TIMEZONE = os.environ.get("TIMEZONE", "America/New_York")

MAX_TURNS = _int("MAX_TURNS", 8)

STATE_DIR = Path(os.environ.get("STATE_DIR", "./state")).resolve()
LOGS_DIR = Path(os.environ.get("LOGS_DIR", "./logs")).resolve()
AUDIO_DIR = STATE_DIR / "audio"

STATE_TTL_HOURS = 6

EXERCISE_TYPES = [
    "none",
    "strength",
    "swim",
    "spin",
    "hike",
    "run",
    "yoga",
    "other",
]


def ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
