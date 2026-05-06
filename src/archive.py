import json
import logging
from pathlib import Path

from .config import LOGS_DIR
from .time_util import local_date_str

log = logging.getLogger(__name__)


def _path_for(date_str: str) -> Path:
    return LOGS_DIR / f"{date_str}.jsonl"


def append(row: dict) -> None:
    """Append a completed check-in row to today's JSONL file (local date)."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    path = _path_for(local_date_str())
    with path.open("a") as f:
        f.write(json.dumps(row, default=str) + "\n")


def has_entries_today() -> bool:
    p = _path_for(local_date_str())
    return p.exists() and p.stat().st_size > 0
