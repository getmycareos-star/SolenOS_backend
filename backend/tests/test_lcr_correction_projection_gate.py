from app.api.care_continuity import _verified_event_correction_statuses


def test_only_person_scoped_checked_correction_links_can_replace_events():
    statuses = _verified_event_correction_statuses([
        {
            "id": "event-checked",
            "correction_lineage_status": "checked",
            "superseded_by_corrections": [
                {"id": "correction-completed", "processing_status": "completed"},
                {"id": "correction-pending", "processing_status": "processing"},
            ],
        },
        {
            "id": "event-unverified",
            "correction_lineage_status": "failed",
            "superseded_by_corrections": [
                {"id": "correction-unverified", "processing_status": "completed"},
            ],
        },
        {
            "id": "event-no-links",
            "correction_lineage_status": "checked",
            "superseded_by_corrections": None,
        },
    ])
    assert statuses == {
        "correction-completed": "completed",
        "correction-pending": "processing",
    }


def test_malformed_correction_relationships_are_not_treated_as_verified():
    statuses = _verified_event_correction_statuses([
        None,
        {"correction_lineage_status": "checked", "superseded_by_corrections": "not-a-list"},
        {"correction_lineage_status": "failed", "superseded_by_corrections": [{"id": "x", "processing_status": "completed"}]},
    ])
    assert statuses == {}
