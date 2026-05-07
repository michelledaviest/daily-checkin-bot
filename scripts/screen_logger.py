#!/usr/bin/env python3
"""Hourly logger that pulls ActivityWatch data and writes to the bot's Google Sheet.

Runs on the LAPTOP. Independent of the bot — uses the same service account and
the same upsert-by-local_date pattern. Looks up sheet columns by header name so
column-order drift between this script and src/sheets.py can't silently corrupt
data.

Config via env vars (read from ~/.config/checkin-bot/.env if present):
- SHEET_ID                 (required)
- SHEET_TAB                (default: checkins)
- GSA_KEY_PATH             (default: ~/.config/checkin-bot/gsa-key.json)
- TIMEZONE                 (default: America/New_York)
- AW_SERVER                (default: http://localhost:5600)
- BREAK_MIN_SECONDS        (default: 300 — 5 minutes)
- PHONE_BUCKET             (optional; blank = laptop only. v1B sets this.)
- LAPTOP_BUCKET            (optional; default = aw-watcher-afk_<hostname>)
"""
from __future__ import annotations

import os
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import gspread
import requests
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials


# ---- config ---------------------------------------------------------------

CONFIG_DIR = Path.home() / ".config" / "checkin-bot"
load_dotenv(CONFIG_DIR / ".env")

SHEET_ID = os.environ.get("SHEET_ID", "").strip()
if not SHEET_ID:
    print("ERROR: SHEET_ID env var not set (expected in ~/.config/checkin-bot/.env)",
          file=sys.stderr)
    sys.exit(1)

SCREEN_TIME_TAB = os.environ.get("SCREEN_TIME_TAB", "screen_time")
GSA_KEY_PATH = Path(os.environ.get("GSA_KEY_PATH", str(CONFIG_DIR / "gsa-key.json")))
AW_SERVER = os.environ.get("AW_SERVER", "http://localhost:5600").rstrip("/")
TIMEZONE = os.environ.get("TIMEZONE", "America/New_York")
BREAK_MIN_SECONDS = int(os.environ.get("BREAK_MIN_SECONDS", "300"))
PHONE_BUCKET = os.environ.get("PHONE_BUCKET", "").strip() or None
LAPTOP_BUCKET = (
    os.environ.get("LAPTOP_BUCKET", "").strip()
    or f"aw-watcher-afk_{socket.gethostname()}"
)

LOCAL_TZ = ZoneInfo(TIMEZONE)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


# ---- ActivityWatch helpers ------------------------------------------------

def today_local_date() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def day_bounds_utc(local_date_str: str) -> tuple[datetime, datetime]:
    """Return UTC start/end for a local date (midnight-to-midnight)."""
    local_start = datetime.strptime(local_date_str, "%Y-%m-%d").replace(tzinfo=LOCAL_TZ)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def fetch_events(bucket: str, start: datetime, end: datetime) -> list[dict]:
    url = f"{AW_SERVER}/api/0/buckets/{bucket}/events"
    params = {
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "limit": -1,
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


# ---- metric computation ---------------------------------------------------

def compute_laptop_metrics(events: list[dict]) -> dict:
    """AFK events have data.status in {'afk','not-afk'} and a duration in seconds."""
    events_sorted = sorted(events, key=lambda e: e["timestamp"])
    total_active = sum(
        e["duration"] for e in events_sorted if e["data"].get("status") == "not-afk"
    )
    breaks = sum(
        1 for e in events_sorted
        if e["data"].get("status") == "afk" and e["duration"] >= BREAK_MIN_SECONDS
    )
    longest_s = 0
    cur_s = 0
    for e in events_sorted:
        status = e["data"].get("status")
        dur = e["duration"]
        if status == "not-afk":
            cur_s += dur
            longest_s = max(longest_s, cur_s)
        elif dur >= BREAK_MIN_SECONDS:
            cur_s = 0
        # else: short afk gap, doesn't reset the block
    return {
        "laptop_screen_hours": round(total_active / 3600, 2),
        "laptop_breaks_count": breaks,
        "laptop_longest_focus_block_min": int(longest_s // 60),
    }


def compute_phone_metrics(events: list[dict]) -> dict:
    """Each aw-watcher-android event = a screen-on session. Gaps between events
    are inactive intervals."""
    events_sorted = sorted(events, key=lambda e: e["timestamp"])
    total_active = sum(e["duration"] for e in events_sorted)

    breaks = 0
    longest_s = 0
    cur_s = 0
    prev_end: datetime | None = None
    for e in events_sorted:
        start = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
        end = start + timedelta(seconds=e["duration"])
        if prev_end is not None:
            gap_s = (start - prev_end).total_seconds()
            if gap_s >= BREAK_MIN_SECONDS:
                breaks += 1
                cur_s = 0
        cur_s += e["duration"]
        longest_s = max(longest_s, cur_s)
        prev_end = end

    return {
        "phone_screen_hours": round(total_active / 3600, 2),
        "phone_breaks_count": breaks,
        "phone_longest_focus_block_min": int(longest_s // 60),
    }


# ---- Sheet upsert ---------------------------------------------------------

def get_worksheet() -> gspread.Worksheet:
    creds = Credentials.from_service_account_file(str(GSA_KEY_PATH), scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID).worksheet(SCREEN_TIME_TAB)


def upsert_row(ws: gspread.Worksheet, local_date_str: str, fields: dict) -> None:
    """Find row whose column A == local_date_str. If none, append. If found,
    patch only the cells named in `fields`. Looks up column positions from the
    sheet's actual header row (no hardcoded offsets)."""
    headers = ws.row_values(1)
    col_map = {name: idx + 1 for idx, name in enumerate(headers)}

    col_a = ws.col_values(1)
    row_num: int | None = None
    for idx, val in enumerate(col_a, start=1):
        if idx == 1:
            continue  # header
        if val == local_date_str:
            row_num = idx
            break

    if row_num is None:
        new_row = [""] * len(headers)
        for col_name, col_idx in col_map.items():
            if col_name == "local_date":
                # leading apostrophe forces TEXT type so col_values(1) matching works
                new_row[col_idx - 1] = f"'{local_date_str}"
            elif col_name in fields and fields[col_name] is not None:
                new_row[col_idx - 1] = fields[col_name]
        ws.append_row(new_row, value_input_option="USER_ENTERED")
        return

    updates: list[dict] = []
    for col_name, value in fields.items():
        if col_name not in col_map or value is None:
            continue
        a1 = gspread.utils.rowcol_to_a1(row_num, col_map[col_name])
        updates.append({"range": a1, "values": [[value]]})
    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")


# ---- main -----------------------------------------------------------------

def main() -> None:
    today = today_local_date()
    start_utc, end_utc = day_bounds_utc(today)

    fields: dict = {}

    # Laptop bucket — required.
    try:
        laptop_events = fetch_events(LAPTOP_BUCKET, start_utc, end_utc)
        if laptop_events:
            fields.update(compute_laptop_metrics(laptop_events))
    except requests.RequestException as e:
        print(f"ERROR: failed to fetch laptop bucket {LAPTOP_BUCKET}: {e}",
              file=sys.stderr)
        sys.exit(1)

    # Phone bucket — optional (v1B only).
    if PHONE_BUCKET:
        try:
            phone_events = fetch_events(PHONE_BUCKET, start_utc, end_utc)
            if phone_events:
                fields.update(compute_phone_metrics(phone_events))
        except requests.RequestException as e:
            print(f"WARN: failed to fetch phone bucket {PHONE_BUCKET}: {e}",
                  file=sys.stderr)

    if not fields:
        print(f"[{today}] no events yet — skipping write.")
        return

    ws = get_worksheet()
    upsert_row(ws, today, fields)
    summary = ", ".join(f"{k}={v}" for k, v in sorted(fields.items()))
    print(f"[{today}] wrote: {summary}")


if __name__ == "__main__":
    main()
