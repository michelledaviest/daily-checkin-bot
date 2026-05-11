"""Pull barometric pressure from Open-Meteo and write to the sheet.

Called once at the morning slot. Adds two columns:
  barometric_pressure_mb  — today's mean sea-level pressure (hPa / mb)
  pressure_drop_24h_mb    — yesterday's mean minus today's mean (positive = falling)

No API key required. Config via LATITUDE / LONGITUDE / TIMEZONE env vars.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import requests

from . import sheets
from .config import ENVIRONMENT_TAB, LATITUDE, LONGITUDE, TIMEZONE
from .time_util import now_local

log = logging.getLogger(__name__)

_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def _fetch_pressure_sync() -> tuple[float | None, float | None]:
    """Return (today_mean_mb, drop_24h_mb). Either value may be None on failure."""
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "hourly": "pressure_msl",
        "timezone": TIMEZONE,
        "past_days": 1,
        "forecast_days": 1,
    }
    r = requests.get(_OPEN_METEO_URL, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()

    hourly = data.get("hourly", {})
    times: list[str] = hourly.get("time", [])
    pressures: list[float | None] = hourly.get("pressure_msl", [])

    if not times or not pressures:
        return None, None

    now = now_local()
    today_str = now.strftime("%Y-%m-%d")
    yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    today_vals = [p for t, p in zip(times, pressures) if t.startswith(today_str) and p is not None]
    yest_vals = [p for t, p in zip(times, pressures) if t.startswith(yesterday_str) and p is not None]

    if not today_vals:
        return None, None

    today_mean = round(sum(today_vals) / len(today_vals), 1)

    if not yest_vals:
        return today_mean, None

    yest_mean = sum(yest_vals) / len(yest_vals)
    drop = round(yest_mean - today_mean, 1)
    return today_mean, drop


async def fetch_and_write(local_date: str) -> None:
    """Fetch pressure data and upsert into the sheet row for local_date."""
    try:
        today_mb, drop_mb = await asyncio.to_thread(_fetch_pressure_sync)
    except Exception:
        log.exception("weather: Open-Meteo fetch failed")
        return

    fields: dict = {}
    if today_mb is not None:
        fields["barometric_pressure_mb"] = today_mb
    if drop_mb is not None:
        fields["pressure_drop_24h_mb"] = drop_mb

    if not fields:
        log.warning("weather: no pressure data returned for %s", local_date)
        return

    try:
        await sheets.upsert_row(local_date, fields, tab=ENVIRONMENT_TAB)
        log.info("weather: wrote %s for %s", fields, local_date)
    except Exception:
        log.exception("weather: sheet write failed")
