from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.reminder_delivery import is_quiet_hours, quiet_hours_release_at


def preference(start="21:00", end="08:00", timezone_name="America/New_York"):
    return SimpleNamespace(
        quiet_hours_enabled=True,
        quiet_hours_start=start,
        quiet_hours_end=end,
        timezone_name=timezone_name,
    )


def test_quiet_hours_release_uses_caregiver_timezone():
    settings = preference(start="21:00", end="08:00")
    now = datetime(2026, 1, 15, 7, 0, tzinfo=timezone.utc)  # 02:00 EST
    assert is_quiet_hours(settings, now)
    assert quiet_hours_release_at(settings, now) == datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc)


def test_quiet_hours_spring_forward_nonexistent_end_releases_at_first_valid_time():
    settings = preference(start="21:00", end="02:30")
    before_jump = datetime(2026, 3, 8, 6, 45, tzinfo=timezone.utc)  # 01:45 EST
    after_jump_before_release = datetime(2026, 3, 8, 7, 15, tzinfo=timezone.utc)  # 03:15 EDT
    after_release = datetime(2026, 3, 8, 7, 31, tzinfo=timezone.utc)  # 03:31 EDT

    expected_release = datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)
    assert is_quiet_hours(settings, before_jump)
    assert quiet_hours_release_at(settings, before_jump) == expected_release
    assert is_quiet_hours(settings, after_jump_before_release)
    assert quiet_hours_release_at(settings, after_jump_before_release) == expected_release
    assert not is_quiet_hours(settings, after_release)


def test_quiet_hours_fall_back_repeated_end_hour_uses_later_occurrence():
    settings = preference(start="21:00", end="01:30")
    first_occurrence_after_wall_end = datetime(2026, 11, 1, 5, 45, tzinfo=timezone.utc)  # 01:45 EDT
    after_second_occurrence = datetime(2026, 11, 1, 6, 31, tzinfo=timezone.utc)  # 01:31 EST

    expected_release = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)
    assert is_quiet_hours(settings, first_occurrence_after_wall_end)
    assert quiet_hours_release_at(settings, first_occurrence_after_wall_end) == expected_release
    assert not is_quiet_hours(settings, after_second_occurrence)


def test_disabled_quiet_hours_never_defers_delivery():
    settings = preference()
    settings.quiet_hours_enabled = False
    now = datetime(2026, 1, 15, 7, 0, tzinfo=timezone.utc)
    assert not is_quiet_hours(settings, now)
    assert quiet_hours_release_at(settings, now) is None
