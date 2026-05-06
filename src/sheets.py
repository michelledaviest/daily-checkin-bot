import asyncio
import json
import logging
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

from .config import GSA_KEY_PATH, LOGS_DIR, SHEET_ID, SHEET_TAB

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Header order — must match the sheet's header row exactly. One row per local_date.
COLUMN_ORDER = [
    "local_date",
    "morning_logged_at",
    "evening_logged_at",
    "sleep_hours",
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
    "morning_transcript",
    "evening_transcript",
    "morning_turns",
    "evening_turns",
]

# 1-based column indices for partial cell updates.
COLUMN_INDEX = {name: i + 1 for i, name in enumerate(COLUMN_ORDER)}

_worksheet: gspread.Worksheet | None = None


def _get_worksheet() -> gspread.Worksheet:
    global _worksheet
    if _worksheet is not None:
        return _worksheet
    creds = Credentials.from_service_account_file(GSA_KEY_PATH, scopes=SCOPES)
    client = gspread.authorize(creds)
    _worksheet = client.open_by_key(SHEET_ID).worksheet(SHEET_TAB)
    return _worksheet


def _format_cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    return v


def _find_row_by_date(ws: gspread.Worksheet, local_date: str) -> int | None:
    """Return 1-based row number whose column A matches local_date, or None.
    Skips the header row at index 1."""
    col_a = ws.col_values(1)
    for idx, val in enumerate(col_a, start=1):
        if idx == 1:
            continue  # header
        if val == local_date:
            return idx
    return None


def _failed_writes_path() -> Path:
    return LOGS_DIR / "failed_writes.jsonl"


def _record_failure(payload: dict, err: str) -> None:
    p = _failed_writes_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps({"payload": payload, "error": err}, default=str) + "\n")


def _upsert_sync(local_date: str, fields: dict) -> None:
    """Upsert today's row by date. If no row exists, append a new one with the
    given fields populated and the rest empty. If a row exists, update only the
    cells in `fields`."""
    ws = _get_worksheet()
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            row_num = _find_row_by_date(ws, local_date)
            if row_num is None:
                # Build full-width row; force date column to TEXT so col search works.
                new_row = []
                for col in COLUMN_ORDER:
                    if col == "local_date":
                        new_row.append(f"'{local_date}")  # leading apostrophe = text
                    else:
                        new_row.append(_format_cell(fields.get(col)))
                ws.append_row(new_row, value_input_option="USER_ENTERED")
            else:
                updates = []
                for col_name, value in fields.items():
                    if col_name not in COLUMN_INDEX:
                        continue
                    a1 = gspread.utils.rowcol_to_a1(row_num, COLUMN_INDEX[col_name])
                    updates.append(
                        {"range": a1, "values": [[_format_cell(value)]]}
                    )
                if updates:
                    ws.batch_update(updates, value_input_option="USER_ENTERED")
            return
        except Exception as e:
            last_err = e
            log.warning("sheets upsert failed (attempt %d): %s", attempt + 1, e)
    _record_failure({"local_date": local_date, **fields}, str(last_err))
    raise RuntimeError(f"sheets upsert failed twice: {last_err}")


async def upsert_row(local_date: str, fields: dict) -> None:
    await asyncio.to_thread(_upsert_sync, local_date, fields)
