"""Central runtime settings shared by temporal features."""

from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parent / ".env", override=False)


DEFAULT_TIMEZONE_NAME = "Asia/Shanghai"
TIMEZONE_NAME = os.getenv("APP_TIMEZONE", DEFAULT_TIMEZONE_NAME).strip() or DEFAULT_TIMEZONE_NAME

try:
    APP_TIMEZONE = ZoneInfo(TIMEZONE_NAME)
except ZoneInfoNotFoundError as exc:
    if TIMEZONE_NAME == DEFAULT_TIMEZONE_NAME:
        # Shanghai has used UTC+08:00 without daylight-saving transitions
        # since 1991. This explicit fallback supports minimal Windows Python
        # installs that do not bundle the optional IANA tzdata package.
        APP_TIMEZONE = timezone(timedelta(hours=8), name=TIMEZONE_NAME)
    else:
        raise RuntimeError(f"Unknown APP_TIMEZONE: {TIMEZONE_NAME}") from exc
