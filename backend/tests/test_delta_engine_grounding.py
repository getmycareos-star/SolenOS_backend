from types import SimpleNamespace

from app.services.delta_engine import _match_source_references


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
