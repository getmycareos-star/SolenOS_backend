"""Tests for evidence links carried by selected Companion context records."""

import json

from app.api.collaboration import _context_linked_evidence_ids
from app.services.lcr_context import format_lcr_context_for_prompt


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


def test_companion_prompt_metadata_preserves_assertion_conflicts():
    prompt = format_lcr_context_for_prompt({
        "schema_status": "available",
        "sources": {},
        "entity_resolution": {
            "assertion_conflicts": [{
                "conflict_type": "assertion_value_disagreement",
                "subject_type": "medication",
                "subject_id": "medication-1",
                "field": "dose",
                "assertion_kind": "current_state",
                "values": [
                    {
                        "value": "5 mg",
                        "assertions": [{
                            "assertion_id": "assertion-1",
                            "evidence_ids": ["evidence-1"],
                            "event_time_text": "Monday",
                        }],
                    },
                    {
                        "value": "10 mg",
                        "assertions": [{
                            "assertion_id": "assertion-2",
                            "evidence_ids": ["evidence-2"],
                            "event_time_text": "Tuesday",
                        }],
                    },
                ],
                "interpretation": "Saved reports disagree; do not choose a value.",
            }],
        },
    })

    assert '"assertion_conflicts"' in prompt
    assert '"field":"dose"' in prompt
    assert '"value":"5 mg"' in prompt
    assert '"value":"10 mg"' in prompt
    assert '"evidence-1"' in prompt
    assert '"event_time_text":"Tuesday"' in prompt
