"""Tests for evidence links carried by selected Companion context records."""

import json

from app.api.collaboration import _context_linked_evidence_ids


def test_selected_context_preserves_linked_evidence_ids_across_record_types():
    selected_lines = [
        "LCR event evidence: " + json.dumps({
            "event_id": "event-1",
            "evidence_ids": ["evidence-1", "evidence-2"],
        }),
        "Caregiver-reviewed assertion: " + json.dumps({
            "source_type": "assertions",
            "record": {
                "id": "assertion-1",
                "evidence_ids": ["evidence-2", "evidence-3"],
            },
        }),
        "Verified document extraction: " + json.dumps({
            "source_type": "document_extractions",
            "record": {"source_evidence_id": "evidence-4"},
        }),
        "Legacy event evidence: " + json.dumps({
            "event_id": "event-2",
            "evidence_ids": '["evidence-5"]',
        }),
    ]

    assert _context_linked_evidence_ids(selected_lines) == [
        "evidence-1",
        "evidence-2",
        "evidence-3",
        "evidence-4",
        "evidence-5",
    ]


def test_selected_context_ignores_malformed_or_unlinked_records():
    selected_lines = [
        "Not a JSON context line",
        "Malformed context: {not-json}",
        "Care event: " + json.dumps({"event_id": "event-3", "evidence_ids": []}),
        "Care assertion: " + json.dumps({"source_type": "assertions", "record": {"id": "assertion-2"}}),
    ]

    assert _context_linked_evidence_ids(selected_lines) == []
