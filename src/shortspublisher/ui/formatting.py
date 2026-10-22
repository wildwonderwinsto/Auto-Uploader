"""Display-only formatting. Never round-trip these strings into approved plans."""

from datetime import datetime
from math import isfinite
from zoneinfo import ZoneInfo

from PySide6.QtCore import QDate, QLocale, QTime, QTimeZone

# The user's macOS Region, captured before the English default replaces it; supplies the clock.
_region: QLocale | None = None


def english_locale(region: QLocale) -> QLocale:
    if region.language() == QLocale.Language.English:
        return region
    # Qt falls back to en_US for territories without an English variant (China, Taiwan).
    return QLocale(QLocale.Language.English, region.territory())


def use_english_interface() -> None:
    """Qt's default locale follows the macOS Region, not the interface language: a Chinese
    Region renders month/day names and AM/PM text in Chinese. Keep wording English while
    preserving the Region's 12/24-hour convention through time_pattern()."""
    global _region
    if _region is None:
        _region = QLocale()
    QLocale.setDefault(english_locale(_region))


def system_zone() -> str:
    return bytes(QTimeZone.systemTimeZoneId().data()).decode() or "UTC"


def time_pattern(locale: QLocale | None = None, *, seconds: bool = False) -> str:
    clock = locale or _region or QLocale()
    twelve = "AP" in clock.timeFormat(QLocale.FormatType.ShortFormat).upper()
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
            time_pattern(locale, seconds=bool(local.second)),
        )
        return f"{date} · {clock} {local.tzname()}"
    except (ValueError, KeyError, OverflowError):
        return "Time unavailable"


def friendly(value: str | None, zone: str | None = None, *, empty: str = "No time set") -> str:
    """Short times for lists: "Today · 3:00 PM", "Tomorrow · 9:00 AM", "Tue, Oct 21 · 3:00 PM"."""
    if not value:
        return empty
    try:
        instant = datetime.fromisoformat(value)
        if instant.tzinfo is None:
            return "Time zone unavailable"
        tz = ZoneInfo(zone or system_zone())
        local, today = instant.astimezone(tz), datetime.now(tz).date()
        language = QLocale()
        clock = language.toString(QTime(local.hour, local.minute), time_pattern())
        days = (local.date() - today).days
        if days in (-1, 0, 1):
            day = ("Yesterday", "Today", "Tomorrow")[days + 1]
        else:
            day = language.toString(
                QDate(local.year, local.month, local.day),
                "ddd, MMM d" if local.year == today.year else "MMM d, yyyy",
            )
        return f"{day} · {clock}"
    except (ValueError, KeyError, OverflowError):
        return "Time unavailable"


def day_label(value: str | None, zone: str | None = None) -> str:
    """Agenda group names: "Today", "Tomorrow", "Wednesday, Oct 22"."""
    if not value:
        return "Waiting for the next posting time"
    try:
        tz = ZoneInfo(zone or system_zone())
        local = datetime.fromisoformat(value).astimezone(tz)
        days = (local.date() - datetime.now(tz).date()).days
    except (ValueError, KeyError, OverflowError):
        return "Time unavailable"
    if days in (-1, 0, 1):
        return ("Yesterday", "Today", "Tomorrow")[days + 1]
    if days < -1:
        return "Earlier"
    return QLocale().toString(QDate(local.year, local.month, local.day), "dddd, MMM d")


def compact(number: int) -> str:
    """1234 → 1.2K, 2500000 → 2.5M."""
    for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(number) >= limit:
            text = f"{number / limit:.1f}".rstrip("0").rstrip(".")
            return text + suffix
    return str(number)


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
