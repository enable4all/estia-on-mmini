from zoneinfo import ZoneInfo

from local_assistant.timefmt import (
    iso_to_epoch,
    local_date,
    local_datetime,
    local_hhmm,
    local_stamp,
    local_when,
    utc_now,
)
from tests.conftest import T0, TZ


def test_local_stamp_matches_signal_timestamp() -> None:
    assert local_stamp(T0, TZ) == "[Thu 8 Oct 09:32]"


def test_winter_time_uses_plus_one() -> None:
    january = 1767261600000  # 2026-01-01 10:00 UTC
    assert local_stamp(january, TZ) == "[Thu 1 Jan 11:00]"


def test_hhmm_and_dates() -> None:
    assert local_hhmm(T0 / 1000, TZ) == "09:32"
    assert local_date("2026-10-08T07:32:23+00:00", TZ) == "8 Oct 2026"
    assert local_datetime("2026-10-08T07:32:23+00:00", TZ) == "8 Oct 2026 09:32"


def test_utc_now_round_trips() -> None:
    now = utc_now()
    assert now.endswith("+00:00")
    assert iso_to_epoch(now) > 1_700_000_000


def test_when_shows_the_date_only_when_not_today() -> None:
    now = T0 / 1000  # Thu 8 Oct 2026 09:32 in Amsterdam
    assert local_when(now - 3600, now, TZ) == "08:32"
    assert local_when(now - 86_400, now, TZ) == "7 Oct 09:32"
    assert local_when(now - 400 * 86_400, now, TZ) == "3 Sep 2025 09:32"


def test_when_follows_the_configured_time_zone() -> None:
    late = 1_791_500_400.0  # 2026-10-08 23:00 UTC: already 9 Oct in Amsterdam
    assert local_when(late - 1800, late, TZ) == "00:30"
    assert local_when(late - 1800, late, ZoneInfo("UTC")) == "22:30"
    assert local_when(late - 3 * 3600, late, TZ) == "8 Oct 22:00"
