from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .config import TIMEZONE

LOCAL_TZ = ZoneInfo(TIMEZONE)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_local() -> datetime:
    return datetime.now(LOCAL_TZ)


def local_date_str(dt: datetime | None = None) -> str:
    dt = dt or now_local()
    return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d")


def iso_utc(dt: datetime | None = None) -> str:
    dt = dt or now_utc()
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
