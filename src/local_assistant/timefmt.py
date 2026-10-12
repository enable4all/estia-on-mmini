"""Time helpers: UTC ISO strings for the database, local stamps for Gemma and the user."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def iso_to_epoch(iso_utc: str) -> float:
    return datetime.fromisoformat(iso_utc).timestamp()


def local_stamp(epoch_ms: int, tz: ZoneInfo, *, year: bool = False) -> str:
    """'[Thu 8 Oct 09:41]' for a Signal timestamp in milliseconds; with `year`, '[Thu 8 Oct 2026 09:41]'."""
    moment = datetime.fromtimestamp(epoch_ms / 1000, tz)
    day = f"{moment:%a} {moment.day} {moment:%b}" + (f" {moment.year}" if year else "")
    return f"[{day} {moment:%H:%M}]"


def local_hhmm(epoch_s: float, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(epoch_s, tz).strftime("%H:%M")


def local_when(epoch_s: float, now_s: float, tz: ZoneInfo) -> str:
    """'14:05' today, '7 Oct 14:05' on another day this year, '3 Sep 2025 14:05' in another year."""
    moment, today = datetime.fromtimestamp(epoch_s, tz), datetime.fromtimestamp(now_s, tz)
    if moment.date() == today.date():
        return f"{moment:%H:%M}"
    if moment.year == today.year:
        return f"{moment.day} {moment:%b %H:%M}"
    return f"{moment.day} {moment:%b %Y %H:%M}"


def local_date(iso_utc: str, tz: ZoneInfo) -> str:
    """'8 Oct 2026' for a UTC ISO timestamp from the database."""
    moment = datetime.fromisoformat(iso_utc).astimezone(tz)
    return f"{moment.day} {moment:%b %Y}"


def local_datetime(iso_utc: str, tz: ZoneInfo) -> str:
    """'8 Oct 2026 09:32' for a UTC ISO timestamp from the database."""
    moment = datetime.fromisoformat(iso_utc).astimezone(tz)
    return f"{moment.day} {moment:%b %Y %H:%M}"
