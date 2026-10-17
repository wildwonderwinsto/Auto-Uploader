"""Display-only formatting. Never round-trip these strings into approved plans."""

from datetime import datetime
from math import isfinite
from zoneinfo import ZoneInfo

from PySide6.QtCore import QDate, QLocale, QTime, QTimeZone


def system_zone() -> str:
    return bytes(QTimeZone.systemTimeZoneId().data()).decode() or "UTC"


def time_pattern(locale: QLocale | None = None, *, seconds: bool = False) -> str:
    locale = locale or QLocale()
    twelve = "AP" in locale.timeFormat(QLocale.FormatType.ShortFormat).upper()
    return ("h:mm" if twelve else "HH:mm") + (":ss" if seconds else "") + (" AP" if twelve else "")


def timestamp(
    value: str | datetime | None,
    zone: str | None = None,
    *,
    locale: QLocale | None = None,
    weekday: bool = False,
    empty: str = "Not available",
) -> str:
    if not value:
        return empty
    try:
        instant = datetime.fromisoformat(value) if isinstance(value, str) else value
        if instant.tzinfo is None:
            return "Time zone unavailable"
        local = instant.astimezone(ZoneInfo(zone or system_zone()))
        language = locale or QLocale()
        date = language.toString(
            QDate(local.year, local.month, local.day),
            "ddd, MMM d, yyyy" if weekday else "MMM d, yyyy",
        )
        clock = language.toString(
            QTime(local.hour, local.minute, local.second),
            time_pattern(language, seconds=bool(local.second)),
        )
        return f"{date} · {clock} {local.tzname()}"
    except (ValueError, KeyError, OverflowError):
        return "Time unavailable"


def duration(seconds: float) -> str:
    if not isfinite(seconds) or seconds < 0:
        return "Duration unknown"
    total = round(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, sec = divmod(remainder, 60)
    return (
        f"{hours}h {minutes}m {sec}s" if hours else f"{minutes}m {sec}s" if minutes else f"{sec}s"
    )


def file_size(size: int) -> str:
    for suffix, divisor in (("GiB", 1024**3), ("MiB", 1024**2), ("KiB", 1024)):
        if size >= divisor:
            return f"{size / divisor:.1f} {suffix}"
    return f"{size} bytes"


def upload_status(state: str, processing: str = "") -> str:
    if state in ("Public Verified", "Uploaded Private") and processing in (
        "processed",
        "succeeded",
    ):
        return "Video processed · " + (
            "Public verified" if state == "Public Verified" else "Private upload"
        )
    return {
        "Needs Reconciliation": "Check upload result",
        "Awaiting Confirmation": "Waiting for your confirmation",
        "Preparing": "Preparing upload",
        "Uploaded Private": "Uploaded privately · Check processing",
        "Public Verified": "Public visibility verified · Check processing",
        "Interrupted": "Upload interrupted · Check session",
        "Paused": "Upload paused",
    }.get(state, state)
