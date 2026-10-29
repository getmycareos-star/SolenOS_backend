"""Messy, synthetic real-world care-coordination scenarios for Open Loop Engine.

These cases intentionally include conflicting accounts, stale commitments, partial
information, cancelled plans, delayed responses, recurrence, and dependency chains.
They test workflow safety and evidence handling, not medical judgment or clinical outcomes.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from test_open_loops import (
    create_loop,
    session,
    setup_function as setup_open_loop_db,
    teardown_function as teardown_open_loop_db,
)
from app.main import app


@pytest.fixture(autouse=True)
def isolated_database():
    setup_open_loop_db()
    yield
    teardown_open_loop_db()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def care_case(client):
    _, headers = session(client, "Messy-care scenario recipient")
    return client, headers


def make(client, headers, kind, title, description, **extra):
    return create_loop(client, headers, kind, title=title, description=description, **extra)


def test_mw01_conflicting_family_and_clinic_dates_remain_explicitly_unresolved(care_case):
    client, headers = care_case
    loop = make(client, headers, "contradiction", "Appointment date conflict",
                "Daughter says Tuesday; clinic message appears to say Thursday.",
                evidence=[{"kind": "family_report", "text": "Daughter says Tuesday"},
                          {"kind": "clinic_message", "text": "Message appears to say Thursday"}])
    assert loop["status"] == "open"
    assert loop["outcome_status"] != "outcome_verified"
    closed = client.post(f"/api/v1/open-loops/{loop['id']}/close", headers=headers,
                         json={"text": "Looks settled", "evidence": [{"kind": "note"}]})
    assert closed.status_code == 409


def test_mw02_partial_information_cannot_be_closed_as_complete(care_case):
    client, headers = care_case
    loop = make(client, headers, "missing_information", "Discharge instructions incomplete",
                "Family has page one; page two and the follow-up date are missing.",
                missing_information=["page two", "follow-up date"])
    response = client.post(f"/api/v1/open-loops/{loop['id']}/close", headers=headers,
                           json={"text": "We found one page", "evidence": [{"kind": "document"}]})
    assert response.status_code == 409


def test_mw03_action_taken_is_not_proof_of_intended_outcome(care_case):
    client, headers = care_case
    loop = make(client, headers, "pending_action", "Call transport service",
                "Caregiver called; pickup confirmation has not arrived.")
    action = client.post(f"/api/v1/open-loops/{loop['id']}/actions", headers=headers,
                         json={"text": "Called transport; voicemail left",
                               "evidence": [{"kind": "call_log", "text": "Voicemail left"}]})
    assert action.status_code == 200
    assert action.json()["outcome_status"] == "outcome_unknown"
    closed = client.post(f"/api/v1/open-loops/{loop['id']}/close", headers=headers,
                         json={"text": "Call completed", "evidence": [{"kind": "call_log"}]})
    assert closed.status_code == 409


def test_mw04_unanswered_clinic_callback_uses_recorded_deadline(care_case):
    client, headers = care_case
    past = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    loop = make(client, headers, "awaiting_response", "Clinic callback",
                "Reception said someone would call back; no response recorded.",
                deadline_at=past, deadline_source="recorded_commitment",
                recipient_label="Clinic reception", request_text="Return call requested")
    assert loop["status"] == "waiting"
    assert loop["overdue"] is True
    result = client.get("/api/v1/open-loops?overdue=true", headers=headers)
    assert any(item["id"] == loop["id"] for item in result.json()["items"])


def test_mw05_future_estimate_without_confirmed_deadline_is_not_overdue(care_case):
    client, headers = care_case
    loop = make(client, headers, "follow_up", "Possible home visit",
                "A relative thinks the visit may be next week; exact date not confirmed.")
    assert loop["deadline_at"] is None
    assert loop["overdue"] is False
    overdue = client.get("/api/v1/open-loops?overdue=true", headers=headers).json()["items"]
    assert all(item["id"] != loop["id"] for item in overdue)


def test_mw06_deferred_decision_requires_reason_and_review_time_is_persisted(care_case):
    client, headers = care_case
    no_reason = client.post("/api/v1/open-loops", headers=headers, json={
        "loop_type": "deferred_decision", "title": "Discuss care schedule",
        "description": "Family postponed the discussion.",
    })
    assert no_reason.status_code == 422
    review_at = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    loop = make(client, headers, "deferred_decision", "Discuss care schedule",
                "Family postponed the discussion pending a sibling's availability.",
                deferred_reason="Sibling unavailable; revisit after their return.",
                review_at=review_at)
    assert loop["status"] == "deferred"
    assert loop["review_at"] is not None
    assert loop["deferred_reason"]


def test_mw07_abandoned_follow_up_is_review_state_not_resolution(care_case):
    client, headers = care_case
    loop = make(client, headers, "follow_up", "Ask about referral",
                "No one has followed up and ownership is unclear.")
    patch = client.patch(f"/api/v1/open-loops/{loop['id']}", headers=headers,
                         json={"status": "review_abandoned",
                               "reason": "No activity recorded; determine whether this is still needed."})
    assert patch.status_code == 200
    assert patch.json()["status"] == "review_abandoned"
    assert patch.json()["outcome_status"] != "outcome_verified"


def test_mw08_cancelled_prerequisite_does_not_unblock_dependent(care_case):
    client, headers = care_case
    prerequisite = make(client, headers, "pending_action", "Confirm accessible transport",
                        "Transport provider has not confirmed availability.")
    dependent = make(client, headers, "pending_decision", "Confirm appointment attendance",
                     "Family decision depends on accessible transport availability.")
    edge = client.post(f"/api/v1/open-loops/{dependent['id']}/dependencies", headers=headers,
                       json={"prerequisite_id": prerequisite["id"], "mandatory": True})
    assert edge.status_code == 200
    cancelled = client.post(f"/api/v1/open-loops/{prerequisite['id']}/cancel", headers=headers,
                            json={"text": "Provider cannot take the booking",
                                  "evidence": [{"kind": "provider_message"}]})
    assert cancelled.status_code == 200
    fetched = client.get(f"/api/v1/open-loops/{dependent['id']}", headers=headers).json()
    assert fetched["loop"]["status"] != "closed"
    assert any(dep["prerequisite_id"] == prerequisite["id"] for dep in fetched["dependencies"])


def test_mw09_reopened_loop_retains_reason_and_audit_history(care_case):
    client, headers = care_case
    loop = make(client, headers, "awaiting_response", "Confirm pharmacy delivery",
                "Delivery was queried; response has not yet arrived.")
    response = client.post(f"/api/v1/open-loops/{loop['id']}/responses", headers=headers,
                           json={"text": "Delivery desk says delivered",
                                 "evidence": [{"kind": "delivery_message", "text": "Desk report"}]})
    assert response.status_code == 200
    closed = client.post(f"/api/v1/open-loops/{loop['id']}/close", headers=headers,
                         json={"text": "Reviewed response", "evidence": [{"kind": "review"}]})
    assert closed.status_code == 200
    reopened = client.post(f"/api/v1/open-loops/{loop['id']}/reopen", headers=headers,
                           json={"text": "Recipient says parcel was not found; recheck",
                                 "evidence": [{"kind": "caregiver_report"}]})
    assert reopened.status_code == 200
    history = client.get(f"/api/v1/open-loops/{loop['id']}/history", headers=headers).json()
    assert any(event["event_type"] == "reopened" for event in history)


def test_mw10_professional_input_without_source_document_cannot_be_closed(care_case):
    client, headers = care_case
    loop = make(client, headers, "professional_input", "Confirm appointment instructions",
                "Family recalls a phone conversation but has no note or message.")
    response = client.post(f"/api/v1/open-loops/{loop['id']}/responses", headers=headers,
                           json={"text": "I think the nurse said Friday", "evidence": []})
    assert response.status_code == 422
    closed = client.post(f"/api/v1/open-loops/{loop['id']}/close", headers=headers,
                         json={"text": "Family memory is enough", "evidence": [{"kind": "family_memory"}]})
    assert closed.status_code == 409


def test_mw11_two_distinct_concerns_on_same_day_are_not_collapsed(care_case):
    client, headers = care_case
    hydration = make(client, headers, "unresolved_concern", "Fluid intake concern",
                     "Caregiver reports less drinking today; reason unknown.")
    transport = make(client, headers, "pending_action", "Transport confirmation",
                     "Ride booking remains unconfirmed.")
    assert hydration["id"] != transport["id"]
    listed = client.get("/api/v1/open-loops", headers=headers).json()["items"]
    assert {hydration["id"], transport["id"]}.issubset({item["id"] for item in listed})


def test_mw12_repeated_reports_preserve_distinct_occurrences(care_case):
    client, headers = care_case
    first = make(client, headers, "unresolved_concern", "Repeated nighttime waking",
                 "Caregiver reports waking repeatedly; cause not known.",
                 evidence=[{"kind": "caregiver_report", "text": "First night"}])
    second = client.post("/api/v1/open-loops", headers=headers, json={
        "loop_type": "unresolved_concern",
        "title": "Repeated nighttime waking",
        "description": "Caregiver reports waking repeatedly; cause not known.",
        "evidence": [{"kind": "caregiver_report", "text": "Second night"}],
    })
    assert second.status_code == 201
    assert second.json()["id"] == first["id"]
    occurrences = client.get(f"/api/v1/open-loops/{first['id']}/occurrences", headers=headers)
    assert occurrences.status_code == 200
    assert len(occurrences.json()) == 2


def test_mw13_response_recorded_does_not_automatically_verify_action_outcome(care_case):
    client, headers = care_case
    loop = make(client, headers, "follow_up", "Check referral receipt",
                "Referral was sent according to family; receiving office has not confirmed processing.")
    response = client.post(f"/api/v1/open-loops/{loop['id']}/responses", headers=headers,
                           json={"text": "Office confirms it received a fax, not that it was processed.",
                                 "evidence": [{"kind": "office_message"}]})
    assert response.status_code == 200
    assert response.json()["outcome_status"] == "outcome_unknown"
    assert response.json()["status"] != "closed"


def test_mw14_dependency_chain_requires_each_prerequisite_to_close(care_case):
    client, headers = care_case
    a = make(client, headers, "pending_action", "Get accessible transport quote",
             "Quote requested; not yet received.")
    b = make(client, headers, "pending_decision", "Choose transport option",
             "Decision waits on quote.")
    c = make(client, headers, "pending_action", "Confirm appointment ride",
             "Booking waits on the selected option.")
    edge_b = client.post(f"/api/v1/open-loops/{b['id']}/dependencies", headers=headers,
                         json={"prerequisite_id": a["id"], "mandatory": True})
    edge_c = client.post(f"/api/v1/open-loops/{c['id']}/dependencies", headers=headers,
                         json={"prerequisite_id": b["id"], "mandatory": True})
    assert edge_b.status_code == 200 and edge_c.status_code == 200
    assert client.get(f"/api/v1/open-loops/{c['id']}", headers=headers).json()["loop"]["status"] != "closed"
    cycle = client.post(f"/api/v1/open-loops/{a['id']}/dependencies", headers=headers,
                        json={"prerequisite_id": c["id"], "mandatory": True})
    assert cycle.status_code == 409


def test_mw15_incomplete_evidence_cannot_be_replaced_by_confident_wording(care_case):
    client, headers = care_case
    loop = make(client, headers, "unconfirmed_outcome", "Confirm new care arrangement",
                "One relative says the arrangement was agreed; another has not confirmed.")
    reported = client.post(f"/api/v1/open-loops/{loop['id']}/outcomes", headers=headers,
                           json={"text": "Everyone has agreed", "evidence": []})
    assert reported.status_code == 200
    assert reported.json()["outcome_status"] == "outcome_reported"
    closed = client.post(f"/api/v1/open-loops/{loop['id']}/close", headers=headers,
                         json={"text": "Definitely complete", "evidence": [{"kind": "assertion"}]})
    assert closed.status_code == 409


def test_mw16_stale_or_duplicate_callback_does_not_create_a_second_response_loop(care_case):
    client, headers = care_case
    payload = {"loop_type": "awaiting_response", "title": "Callback from clinic",
               "description": "Callback is outstanding; no answer has been recorded.",
               "idempotency_key": "messy-callback-request-001"}
    first = client.post("/api/v1/open-loops", headers=headers, json=payload)
    retry = client.post("/api/v1/open-loops", headers=headers, json=payload)
    assert first.status_code == 201 and retry.status_code == 201
    assert first.json()["id"] == retry.json()["id"]
    listed = client.get("/api/v1/open-loops?loop_type=awaiting_response", headers=headers).json()["items"]
    assert len([item for item in listed if item["title"] == "Callback from clinic"]) == 1
