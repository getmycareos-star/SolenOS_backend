from datetime import datetime, timezone

from app.api.care_continuity import _current_care_projection_sort_key


def test_current_care_projection_orders_by_event_time_not_backfill_time():
    recent_event_backfilled_earlier = {
        "id": "recent-event",
        "priority": 6,
        "occurred_at": datetime(2026, 11, 20, 9, tzinfo=timezone.utc),
        "recorded_at": datetime(2026, 11, 26, 12, tzinfo=timezone.utc),
    }
    older_event_saved_earlier = {
        "id": "older-event",
        "priority": 6,
        "occurred_at": datetime(2026, 11, 18, 9, tzinfo=timezone.utc),
        "recorded_at": datetime(2026, 11, 18, 10, tzinfo=timezone.utc),
    }

    ordered = sorted(
        [older_event_saved_earlier, recent_event_backfilled_earlier],
        key=_current_care_projection_sort_key,
    )

    assert [item["id"] for item in ordered] == ["recent-event", "older-event"]


def test_current_care_projection_uses_record_time_only_when_event_time_is_missing():
    known_event_time = {
        "id": "known-event-time",
        "priority": 4,
        "occurred_at": "2026-11-20T09:00:00Z",
        "recorded_at": "2026-11-26T12:00:00Z",
    }
    no_event_time = {
        "id": "missing-event-time",
        "priority": 4,
        "occurred_at": None,
        "recorded_at": "2026-11-21T12:00:00+00:00",
    }

    ordered = sorted(
        [known_event_time, no_event_time],
        key=_current_care_projection_sort_key,
    )

    assert [item["id"] for item in ordered] == ["no-event-time", "known-event-time"]


def test_current_care_projection_keeps_priority_ahead_of_recency_and_breaks_ties_stably():
    items = [
        {"id": "b", "priority": 6, "occurred_at": "2026-11-26T10:00:00Z"},
        {"id": "a", "priority": 6, "occurred_at": "2026-11-26T10:00:00Z"},
        {"id": "urgent", "priority": 0, "occurred_at": "2026-11-01T10:00:00Z"},
    ]

    ordered = sorted(items, key=_current_care_projection_sort_key)

    assert [item["id"] for item in ordered] == ["urgent", "a", "b"]
