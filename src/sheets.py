import asyncio
import json
import logging
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

from .config import GSA_KEY_PATH, LOGS_DIR, SHEET_ID, SHEET_TAB

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Column definitions per tab. All tabs share local_date as the join key (col A).
COLUMNS: dict[str, list[str]] = {
    "checkins": [
        "local_date",
        "morning_logged_at",
        "evening_logged_at",
        "sleep_hours",
        "water_oz",
        "mood_score",
        "body_notes",
        "desk_hours",
        "shoulder_pain",
        "neck_spasms",
        "migraine",
        "migraine_severity",
        "exercise_type",
        "exercise_minutes",
        "steps",
        "skipped_meals",
        "alcohol",
        "morning_transcript",
        "evening_transcript",
    ],
    "screen_time": [
        "local_date",
        "laptop_screen_hours",
        "phone_screen_hours",
        "laptop_longest_focus_block_min",
        "phone_longest_focus_block_min",
        "laptop_breaks_count",
        "phone_breaks_count",
    ],
    "environment": [
        "local_date",
        "barometric_pressure_mb",
        "pressure_drop_24h_mb",
    ],
}

# Aliases kept for callers that reference the checkins layout directly.
COLUMN_ORDER = COLUMNS["checkins"]
COLUMN_INDEX = {name: i + 1 for i, name in enumerate(COLUMN_ORDER)}

_client: "gspread.Client | None" = None
_worksheets: "dict[str, gspread.Worksheet]" = {}


def _get_client():
    global _client
    if _client is None:
        creds = Credentials.from_service_account_file(GSA_KEY_PATH, scopes=SCOPES)
        _client = gspread.authorize(creds)
    return _client


def _get_worksheet(tab: str) -> gspread.Worksheet:
    if tab not in _worksheets:
        _worksheets[tab] = _get_client().open_by_key(SHEET_ID).worksheet(tab)
    return _worksheets[tab]


def _format_cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    return v


def _find_row_by_date(ws: gspread.Worksheet, local_date: str) -> int | None:
    """Return 1-based row number whose column A matches local_date, or None."""
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


def _col_index_for_tab(tab: str) -> dict[str, int]:
    col_list = COLUMNS.get(tab)
    if col_list is not None:
        return {name: i + 1 for i, name in enumerate(col_list)}
    # Unknown tab (e.g. migraine_episodes): look up from sheet header.
    ws = _get_worksheet(tab)
    headers = ws.row_values(1)
    return {name: idx + 1 for idx, name in enumerate(headers)}


def _upsert_sync(local_date: str, fields: dict, tab: str) -> None:
    ws = _get_worksheet(tab)
    col_index = _col_index_for_tab(tab)
    col_order = COLUMNS.get(tab) or list(col_index.keys())
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            row_num = _find_row_by_date(ws, local_date)
            if row_num is None:
                new_row = []
                for col in col_order:
                    if col == "local_date":
                        new_row.append(f"'{local_date}")
                    else:
                        new_row.append(_format_cell(fields.get(col)))
                ws.append_row(new_row, value_input_option="USER_ENTERED")
            else:
                updates = []
                for col_name, value in fields.items():
                    if col_name not in col_index:
                        continue
                    a1 = gspread.utils.rowcol_to_a1(row_num, col_index[col_name])
                    updates.append({"range": a1, "values": [[_format_cell(value)]]})
                if updates:
                    ws.batch_update(updates, value_input_option="USER_ENTERED")
            return
        except Exception as e:
            last_err = e
            log.warning("sheets upsert failed (attempt %d, tab=%s): %s", attempt + 1, tab, e)
    _record_failure({"local_date": local_date, "tab": tab, **fields}, str(last_err))
    raise RuntimeError(f"sheets upsert failed twice: {last_err}")


async def upsert_row(local_date: str, fields: dict, tab: str = SHEET_TAB) -> None:
    await asyncio.to_thread(_upsert_sync, local_date, fields, tab)


def _fetch_all_rows_sync(tab: str) -> list[dict]:
    ws = _get_worksheet(tab)
    values = ws.get_all_values()
    if len(values) < 2:
        return []
    headers = values[0]
    out: list[dict] = []
    for row in values[1:]:
        padded = list(row) + [""] * (len(headers) - len(row))
        out.append(dict(zip(headers, padded)))
    return out


async def fetch_all_rows(tab: str = SHEET_TAB) -> list[dict]:
    return await asyncio.to_thread(_fetch_all_rows_sync, tab)


def _update_existing_sync(local_date: str, fields: dict, tab: str) -> bool:
    ws = _get_worksheet(tab)
    col_index = _col_index_for_tab(tab)
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            row_num = _find_row_by_date(ws, local_date)
            if row_num is None:
                return False
            updates = []
            for col_name, value in fields.items():
                if col_name not in col_index:
                    continue
                a1 = gspread.utils.rowcol_to_a1(row_num, col_index[col_name])
                updates.append({"range": a1, "values": [[_format_cell(value)]]})
            if updates:
                ws.batch_update(updates, value_input_option="USER_ENTERED")
            return True
        except Exception as e:
            last_err = e
            log.warning("sheets update failed (attempt %d, tab=%s): %s", attempt + 1, tab, e)
    _record_failure({"local_date": local_date, "tab": tab, **fields}, str(last_err))
    raise RuntimeError(f"sheets update failed twice: {last_err}")


async def update_existing_row(local_date: str, fields: dict, tab: str = SHEET_TAB) -> bool:
    return await asyncio.to_thread(_update_existing_sync, local_date, fields, tab)


def _fetch_row_by_date_sync(local_date: str, tab: str) -> dict | None:
    rows = _fetch_all_rows_sync(tab)
    for r in rows:
        if r.get("local_date") == local_date:
            return r
    return None


async def fetch_row_by_date(local_date: str, tab: str = SHEET_TAB) -> dict | None:
    return await asyncio.to_thread(_fetch_row_by_date_sync, local_date, tab)
