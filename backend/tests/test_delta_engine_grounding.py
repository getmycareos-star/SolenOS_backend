from types import SimpleNamespace
import json

import pytest
from fastapi import HTTPException

from app.services.delta_engine import DELTA_JSON_CONTRACT, _match_source_references, _parse_model_json


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


def test_delta_json_parser_rejects_truncated_output_without_persisting():
    with pytest.raises(HTTPException) as exc:
        _parse_model_json('{"event_summary":"unfinished"')
    assert exc.value.status_code == 502
    assert exc.value.detail["code"] == "delta_invalid_json"



def test_delta_json_contract_bounds_each_finding_list_for_compact_inference():
    payload = {
        "event_summary": "Caregiver observation recorded.",
        "medications": [
            {"change_type": "uncertain", "name": "Medicine A", "previous": None, "current": None, "evidence_excerpt": "Mention A", "certainty": "uncertain"},
            {"change_type": "uncertain", "name": "Medicine B", "previous": None, "current": None, "evidence_excerpt": "Mention B", "certainty": "uncertain"},
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
    assert DELTA_JSON_CONTRACT["properties"]["medications"]["maxItems"] == 1
