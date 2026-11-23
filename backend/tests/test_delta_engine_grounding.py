from types import SimpleNamespace
import hashlib
import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.services import delta_engine
from app.services.delta_engine import (
    DELTA_JSON_CONTRACT,
    _care_state_revision_snapshot,
    _match_source_references,
    _parse_model_json,
)


def test_new_event_excerpt_is_linked_only_to_the_persisted_event_id():
    excerpt = "Caregiver reported two episodes of unsteadiness this week."
    refs = _match_source_references(
        excerpt,
        new_content=f"Daily capture: {excerpt} Follow-up is not yet confirmed.",
        source_event_id="event-current",
        active_state=[],
        prior_events=[],
        evidence_rows=[],
    )
    assert refs == [{
        "record_type": "care_event",
        "record_id": "event-current",
        "match_basis": "exact_normalized_excerpt",
    }]


def test_follow_up_excerpt_links_only_matching_evidence_not_every_run_evidence_id():
    excerpt = "The discharge note says the follow-up appointment is next Tuesday."
    evidence_rows = [
        SimpleNamespace(id="evidence-supporting", source_text=f"Extracted passage: {excerpt}"),
        SimpleNamespace(id="evidence-unrelated", source_text="The person slept poorly on Monday night."),
    ]
    refs = _match_source_references(
        excerpt,
        new_content="New event contains no matching excerpt.",
        source_event_id="event-current",
        active_state=[],
        prior_events=[],
        evidence_rows=evidence_rows,
    )
    assert refs == [{
        "record_type": "evidence",
        "record_id": "evidence-supporting",
        "match_basis": "exact_normalized_excerpt",
    }]


def test_source_content_binding_accepts_exact_saved_event_text():
    event = SimpleNamespace(
        id="event-exact",
        title="Saved sleep note",
        description="Caregiver reported interrupted sleep.",
    )
    assert delta_engine._source_content_matches_event(
        "Caregiver reported interrupted sleep.", event
    )


def test_source_content_binding_accepts_only_unchanged_structured_capture_projection():
    saved = {
        "notes": "Mum woke three times overnight.",
        "medications": [{"name": "Example medicine", "dose": "5 mg"}],
        "capture_idempotency_key": "capture-12345678",
    }
    event = SimpleNamespace(
        id="event-structured",
        title="Daily care capture",
        description=json.dumps(saved),
    )
    projection = json.dumps({"notes": saved["notes"], "medications": saved["medications"]})
    assert delta_engine._source_content_matches_event(projection, event)


def test_source_content_binding_rejects_changed_or_unrelated_content():
    event = SimpleNamespace(
        id="event-mismatch",
        title="Saved sleep note",
        description=json.dumps({"notes": "Mum woke three times overnight."}),
    )
    assert not delta_engine._source_content_matches_event(
        "Caregiver reported that Mum slept peacefully.", event
    )
    assert not delta_engine._source_content_matches_event(
        json.dumps({"notes": "Mum slept peacefully."}), event
    )
    assert not delta_engine._source_content_matches_event("{}", event)


def test_reconcile_rejects_content_not_bound_to_the_supplied_source_event():
    from unittest.mock import Mock

    event = SimpleNamespace(
        id="event-bound",
        person_id="person-bound",
        title="Saved sleep note",
        description="Caregiver reported interrupted sleep.",
    )
    db = Mock()
    db.query.return_value.filter.return_value.first.return_value = event

    with pytest.raises(HTTPException) as exc:
        delta_engine.reconcile_event(
            db,
            person_id="person-bound",
            event_date=None,
            source="caregiver report",
            content="Caregiver reported that Mum slept peacefully.",
            source_event_id="event-bound",
            persist=False,
        )

    assert exc.value.status_code == 422
    assert "does not match the saved care update" in exc.value.detail
    assert db.query.call_count == 1


def test_generic_excerpt_cannot_create_a_source_link():
    refs = _match_source_references(
        "Yes",
        new_content="Yes",
        source_event_id="event-current",
        active_state=[],
        prior_events=[],
        evidence_rows=[SimpleNamespace(id="evidence-1", source_text="Yes")],
    )
    assert refs == []


def test_historical_event_excerpt_links_to_the_exact_prior_event():
    excerpt = "Caregiver recorded that the person needed assistance transferring from bed."
    prior_events = [
        SimpleNamespace(
            id="event-matching",
            title="Transfer assistance",
            description=excerpt,
        ),
        SimpleNamespace(
            id="event-unrelated",
            title="Sleep",
            description="Caregiver reported poor sleep overnight.",
        ),
    ]
    refs = _match_source_references(
        excerpt,
        new_content="A different new care event was recorded.",
        source_event_id="event-current",
        active_state=[],
        prior_events=prior_events,
        evidence_rows=[],
    )
    assert refs == [{
        "record_type": "care_event",
        "record_id": "event-matching",
        "match_basis": "exact_normalized_excerpt",
    }]


def test_delta_json_parser_accepts_one_valid_object_after_model_preamble():
    payload = {
        "event_summary": "Caregiver observation recorded.",
        "medications": [],
        "conditions": [],
        "symptoms": [],
        "tests": [],
        "follow_ups": [],
        "contradictions": [],
        "clinical_gaps": [],
        "caregiver_summary": "Review the recorded observation.",
        "clinician_summary": "Caregiver-reported observation; not independently verified.",
    }
    parsed = _parse_model_json("Structured result:\\n" + json.dumps(payload))
    assert parsed == payload


def test_delta_generation_retries_once_after_incomplete_json(monkeypatch):
    payload = {
        "event_summary": "Caregiver observation recorded.",
        "medications": [], "conditions": [], "symptoms": [], "tests": [],
        "follow_ups": [], "contradictions": [], "clinical_gaps": [],
        "caregiver_summary": "Review the recorded observation.",
        "clinician_summary": "Caregiver-reported observation; not independently verified.",
    }
    calls = []
    def fake_qwen(messages, **kwargs):
        calls.append({"messages": messages, **kwargs})
        return '{"event_summary":"unfinished"' if len(calls) == 1 else json.dumps(payload)
    monkeypatch.setattr(delta_engine, "qwen_chat", fake_qwen)
    result = delta_engine._request_delta_json(
        [{"role": "system", "content": "Return the Delta object."}], timeout=180
    )
    assert result == payload
    assert len(calls) == 2
    assert [call["timeout"] for call in calls] == [120, 30]
    assert [call["options"]["num_predict"] for call in calls] == [384, 512]
    assert "previous response was incomplete" in calls[1]["messages"][-1]["content"]

def test_delta_json_parser_rejects_truncated_output_without_persisting():
    with pytest.raises(HTTPException) as exc:
        _parse_model_json('{"event_summary":"unfinished"')
    assert exc.value.status_code == 502
    assert exc.value.detail["code"] == "delta_invalid_json"



def test_delta_json_contract_bounds_each_finding_list_for_compact_inference():
    payload = {
        "event_summary": "Caregiver observation recorded.",
        "medications": [
            {"change_type": "uncertain", "name": f"Medicine {letter}", "previous": None, "current": None, "evidence_excerpt": f"Mention {letter}", "certainty": "uncertain"}
            for letter in "ABCDEF"
        ],
        "conditions": [],
        "symptoms": [],
        "tests": [],
        "follow_ups": [],
        "contradictions": [],
        "clinical_gaps": [],
        "caregiver_summary": "Review the recorded observation.",
        "clinician_summary": "Caregiver-reported observation.",
    }
    with pytest.raises(HTTPException) as exc:
        _parse_model_json(json.dumps(payload))
    assert exc.value.status_code == 502
    assert "item limit" in str(exc.value.detail)
    for finding_list in ("medications", "conditions", "symptoms", "tests", "follow_ups", "contradictions", "clinical_gaps"):
        assert DELTA_JSON_CONTRACT["properties"][finding_list]["maxItems"] == 5


def test_care_state_revision_snapshot_detects_new_rows_with_identical_timestamps():
    from datetime import datetime, timezone
    from unittest.mock import Mock

    db = Mock()
    same_timestamp = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    db.query.return_value.filter.return_value.scalar.side_effect = [
        same_timestamp, 4,
        same_timestamp, 5,
    ]

    before = _care_state_revision_snapshot(db, "person-revision-snapshot")
    after = _care_state_revision_snapshot(db, "person-revision-snapshot")

    assert before == "4:2026-11-15T12:00:00+00:00"
    assert after == "5:2026-11-15T12:00:00+00:00"
    assert before != after



def test_delta_run_replay_requires_exact_content_and_event_time():
    event_date = datetime(2026, 11, 19, 14, 0, tzinfo=timezone.utc)
    content = "Caregiver reported two interrupted nights."
    content_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    provenance = {
        "engine_version": delta_engine.DELTA_ENGINE_VERSION,
        "source_event_snapshot_sha256": None,
        "care_state_revision_snapshot": "7:2026-11-19T13:00:00+00:00",
        "evidence_ids": [],
        "input_content_sha256": content_digest,
        "event_date": event_date.isoformat(),
    }
    current = {
        "source_event_snapshot_sha256": None,
        "care_state_revision_snapshot": "7:2026-11-19T13:00:00+00:00",
        "evidence_ids": [],
        "input_content_sha256": content_digest,
        "event_date": event_date,
    }

    assert delta_engine._delta_run_matches_current_input(
        provenance, **current
    )
    assert not delta_engine._delta_run_matches_current_input(
        provenance,
        **{**current, "input_content_sha256": hashlib.sha256(
            b"A different caregiver observation."
        ).hexdigest()},
    )
    assert not delta_engine._delta_run_matches_current_input(
        provenance,
        **{**current, "event_date": datetime(2026, 11, 18, 14, 0, tzinfo=timezone.utc)},
    )

    # Results written before the content digest existed must be recomputed,
    # never treated as proof that the current content was already reconciled.
    legacy_provenance = {key: value for key, value in provenance.items()
                         if key != "input_content_sha256"}
    assert not delta_engine._delta_run_matches_current_input(
        legacy_provenance, **current
    )


def test_delta_context_preserves_document_qualifiers_without_copying_raw_source_text():
    context = delta_engine._safe_assertion_source_context({
        "kind": "caregiver_reviewed_document_candidate",
        "authority": "caregiver_reviewed_report_not_independently_verified",
        "review_status": "accepted_by_caregiver",
        "private_raw_quote": "This exact medical quotation must not be copied into JSON context.",
        "document_candidate": {
            "source_attribution": "professional_documented",
            "negation_status": "explicitly_negated",
            "modality": "tentative",
            "conditionality": "conditional",
            "temporal_context": "historical",
            "statement_kind": "recommendation",
            "field_type": "symptom",
            "page_number": 4,
            "event_date": "2026-11-10",
            "source_excerpt": "Raw page quotation must not be copied.",
            "source_span_start": 40,
        },
    })
    assert context["authority"] == "caregiver_reviewed_report_not_independently_verified"
    assert context["document_candidate"]["negation_status"] == "explicitly_negated"
    assert context["document_candidate"]["modality"] == "tentative"
    assert context["document_candidate"]["conditionality"] == "conditional"
    assert context["document_candidate"]["temporal_context"] == "historical"
    assert context["document_candidate"]["statement_kind"] == "recommendation"
    assert context["document_candidate"]["page_number"] == 4
    assert "private_raw_quote" not in context
    assert "source_excerpt" not in context["document_candidate"]
    assert "source_span_start" not in context["document_candidate"]


def test_delta_context_ignores_untrusted_or_malformed_document_context():
    assert delta_engine._safe_assertion_source_context("clinically_verified") == {}
    assert delta_engine._safe_assertion_source_context({
        "authority": {"untrusted": "value"},
        "document_candidate": ["not", "a", "mapping"],
    }) == {}


def test_delta_reconciliation_uses_saved_event_time_when_request_omits_event_date():
    persisted_event_time = datetime(2026, 10, 12, 8, 30, tzinfo=timezone.utc)
    source_event = SimpleNamespace(occurred_at=persisted_event_time)

    assert delta_engine._event_time_for_reconciliation(None, source_event) == persisted_event_time


def test_explicit_event_time_takes_precedence_over_saved_event_time():
    explicit_event_time = datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)
    persisted_event_time = datetime(2026, 10, 12, 8, 30, tzinfo=timezone.utc)
    source_event = SimpleNamespace(occurred_at=persisted_event_time)

    assert delta_engine._event_time_for_reconciliation(explicit_event_time, source_event) == explicit_event_time


def test_server_recorded_timestamp_is_not_promoted_to_an_event_time():
    source_event = SimpleNamespace(
        occurred_at=datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc),
        time_provenance="server_recorded_at",
    )

    assert delta_engine._event_time_for_reconciliation(None, source_event) is None


def test_legacy_saved_event_time_remains_available_when_provenance_is_not_server_recorded():
    persisted_event_time = datetime(2026, 10, 12, 8, 30, tzinfo=timezone.utc)
    source_event = SimpleNamespace(
        occurred_at=persisted_event_time,
        time_provenance="caregiver_reported_at",
    )

    assert delta_engine._event_time_for_reconciliation(None, source_event) == persisted_event_time
