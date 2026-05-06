import logging

import requests

from .config import HC_DAILY_URL, HC_HEARTBEAT_URL

log = logging.getLogger(__name__)

_TIMEOUT = 10


def _ping(url: str, status: str = "success") -> None:
    if not url:
        return
    target = url if status == "success" else f"{url.rstrip('/')}/fail"
    try:
        requests.get(target, timeout=_TIMEOUT)
    except requests.RequestException as e:
        log.warning("healthcheck ping failed: %s", e)


def heartbeat(success: bool = True) -> None:
    _ping(HC_HEARTBEAT_URL, "success" if success else "fail")


def daily(success: bool = True) -> None:
    _ping(HC_DAILY_URL, "success" if success else "fail")
