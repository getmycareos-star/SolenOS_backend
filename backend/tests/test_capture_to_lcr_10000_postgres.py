"""10,000 complete API-backed caregiver capture-to-LCR journeys.

This is synthetic systems qualification, not clinical-accuracy or real-family
validation. Every journey creates its own server-issued caregiver identity,
submits delayed, attributed source evidence, records a care event, preserves a
contradiction and correction, and retrieves the resulting LCR projection in
subsequent HTTP requests. A separate caregiver identity is used for adversarial
cross-person reads.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import get_db
from app.main import app
import app.main as main_module

DATABASE_URL = (
    os.getenv("SOLENOS_BENCHMARK_DATABASE_URL")
    or os.getenv("DATABASE_URL", "")
)
if DATABASE_URL.startswith("postgresql+psycopg://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql+psycopg://", "postgresql+psycopg2://", 1
    )

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")),
    reason="requires disposable PostgreSQL benchmark database",
)

JOURNEY_COUNT = 10_000
UTC = timezone.utc


def _anonymous_session(client: TestClient, label: str) -> dict:
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": f"Synthetic care recipient {label}",
              "caregiver_name": f"Synthetic caregiver {label}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_10000_complete_caregiver_capture_to_lcr_journeys():
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    original_session_local = main_module.SessionLocal
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = Session
    run_id = uuid.uuid4().hex[:12]
    counts = {
        "sessions": 0,
        "evidence": 0,
        "observations": 0,
        "events": 0,
        "contradictions": 0,
        "corrections": 0,
        "historical_reconstructions": 0,
        "lcr_reads": 0,
        "foreign_denials": 0,
    }

    try:
        with TestClient(app) as client:
            # A distinct unrelated identity probes every target-person boundary.
            attacker = _anonymous_session(client, f"attacker-{run_id}")
            attacker_headers = {
                "Authorization": f"Bearer {attacker['access_token']}"
            }

            for index in range(JOURNEY_COUNT):
                label = f"{run_id}-{index:05d}"
                session = _anonymous_session(client, label)
                person_id = session["person"]["id"]
                caregiver_id = session["caregiver"]["id"]
                headers = {"Authorization": f"Bearer {session['access_token']}"}
                occurred_at = datetime(2026, 10, 1, 8, 0, tzinfo=UTC) + timedelta(
                    minutes=index % 1440
                )
                reported_at = occurred_at + timedelta(days=7)
                source_text = (
                    f"Caregiver {label} reports that the person needed "
                    f"walking support on the earlier visit."
                )

                evidence_response = client.post(
                    "/api/v1/evidence",
                    headers=headers,
                    json={
                        "person_id": person_id,
                        "type": "caregiver_report",
                        "evidence_kind": "caregiver_report",
                        "epistemic_status": "unverified",
                        "source_text": source_text,
                        "uploaded_by_caregiver_id": caregiver_id,
                        "reported_by_caregiver_id": caregiver_id,
                        "reported_by_label": f"Family caregiver {index % 7}",
                        "original_author": f"Family caregiver {index % 7}",
                        "collected_by_caregiver_id": caregiver_id,
                        "occurred_at": occurred_at.isoformat(),
                        "reported_at": reported_at.isoformat(),
                        "collected_at": reported_at.isoformat(),
                        "evidence_status": "reported",
                        "source_type": "caregiver_report",
                        "time_provenance": "caregiver_recollection",
                        "ingestion_key": f"journey:{label}:source",
                    },
                )
                assert evidence_response.status_code == 200, (
                    f"journey={index} evidence: {evidence_response.status_code} "
                    f"{evidence_response.text}"
                )
                evidence = evidence_response.json()
                evidence_id = evidence["id"]
                assert evidence["uploaded_by_caregiver_id"] == caregiver_id
                assert evidence["reported_by_caregiver_id"] == caregiver_id
                assert evidence["reported_by_label"] == f"Family caregiver {index % 7}"
                counts["evidence"] += 1

                # A second, separately attributed account supplies an actual
                # competing source. Its reporter identity is deliberately
                # unverified; the benchmark must not invent a linked caregiver.
                counter_text = (
                    f"Second family account {label} reports that walking "
                    "support was not needed during the same earlier visit."
                )
                counter_response = client.post(
                    "/api/v1/evidence",
                    headers=headers,
                    json={
                        "person_id": person_id,
                        "type": "caregiver_report",
                        "evidence_kind": "caregiver_report",
                        "epistemic_status": "disputed",
                        "source_text": counter_text,
                        "uploaded_by_caregiver_id": caregiver_id,
                        "reported_by_label": "Second family account; identity unverified",
                        "original_author": "Second family account; identity unverified",
                        "collected_by_caregiver_id": caregiver_id,
                        "occurred_at": occurred_at.isoformat(),
                        "reported_at": reported_at.isoformat(),
                        "collected_at": reported_at.isoformat(),
                        "evidence_status": "reported",
                        "source_type": "caregiver_report",
                        "time_provenance": "caregiver_recollection",
                        "ingestion_key": f"journey:{label}:counter-source",
                    },
                )
                assert counter_response.status_code == 200, (
                    f"journey={index} counter-evidence: "
                    f"{counter_response.status_code} {counter_response.text}"
                )
                counter_evidence = counter_response.json()
                counter_evidence_id = counter_evidence["id"]
                assert counter_evidence["reported_by_caregiver_id"] is None
                assert counter_evidence["reported_by_label"] == (
                    "Second family account; identity unverified"
                )
                counts["evidence"] += 1

                # Create source-span-grounded observations. The subject
                # (care recipient), observer/reporter label, and uploader are
                # separate fields; an unknown reporter identity stays unknown.
                observation_ids = []
                for source_id, passage, observer_label, value_text in (
                    (evidence_id, source_text, f"Family caregiver {index % 7}",
                     "Walking support was reported as needed."),
                    (counter_evidence_id, counter_text,
                     "Second family account; identity unverified",
                     "Walking support was reported as not needed."),
                ):
                    start = passage.index("walking")
                    observation_response = client.post(
                        f"/api/v1/care-continuity/evidence/{source_id}/observations",
                        headers=headers,
                        json={
                            "observation_kind": "caregiver_report",
                            "epistemic_status": "reported",
                            "subject_label": "Care recipient",
                            "attribute": "mobility_support",
                            "value_text": value_text,
                            "context": "Source-attributed account; not a verified clinical fact.",
                            "observer_label": observer_label,
                            "source_passage": passage[start:],
                            "source_span_start": start,
                            "source_span_end": len(passage),
                            "extraction_method": "caregiver_entered",
                            "extraction_version": "1",
                            "reported_at": reported_at.isoformat(),
                        },
                    )
                    assert observation_response.status_code == 201, (
                        f"journey={index} observation: "
                        f"{observation_response.status_code} {observation_response.text}"
                    )
                    observation = observation_response.json()
                    assert observation["subject_label"] == "Care recipient"
                    assert observation["observer_label"] == observer_label
                    observation_ids.append(observation["id"])
                    counts["observations"] += 1

                event_response = client.post(
                    "/api/v1/events",
                    headers=headers,
                    json={
                        "person_id": person_id,
                        "event_type": "mobility",
                        "status": "recorded",
                        "occurred_at": occurred_at.isoformat(),
                        "title": f"Mobility support reported {label}",
                        "description": source_text,
                        "evidence_ids": [evidence_id, counter_evidence_id],
                        "time_provenance": "source_observed_at",
                        "created_by_caregiver_id": caregiver_id,
                        "tags": ["synthetic_benchmark", "delayed_evidence"],
                    },
                )
                assert event_response.status_code == 200, (
                    f"journey={index} event: {event_response.status_code} "
                    f"{event_response.text}"
                )
                event = event_response.json()
                event_id = event["id"]
                assert event["occurred_at"].startswith(occurred_at.isoformat()[:19])
                assert event["recorded_at"] is not None
                counts["events"] += 1

                conflict_response = client.post(
                    "/api/v1/uncertainty/contradictions",
                    headers=headers,
                    json={
                        "person_id": person_id,
                        "subject_type": "mobility",
                        "subject_id": "walking_support",
                        "field": "support_needed",
                        "description": (
                            "One report says support was needed; another account "
                            "disagrees. Preserve both pending review."
                        ),
                        "event_id": event_id,
                        "context_key": f"journey:{label}:mobility",
                        "effective_from": occurred_at.isoformat(),
                        "matching_candidate_ids": observation_ids,
                        "resolution_status": "unresolved",
                        "evidence_ids": [evidence_id, counter_evidence_id],
                    },
                )
                assert conflict_response.status_code == 200, (
                    f"journey={index} contradiction: "
                    f"{conflict_response.status_code} {conflict_response.text}"
                )
                conflict = conflict_response.json()
                assert conflict["resolution_status"] == "unresolved"
                counts["contradictions"] += 1

                correction_response = client.post(
                    "/api/v1/corrections",
                    headers=headers,
                    json={
                        "person_id": person_id,
                        "target_type": "evidence",
                        "target_id": evidence_id,
                        "original_text": source_text,
                        "corrected_text": (
                            f"Caregiver {label} clarifies that the earlier "
                            "report described observed support, not a confirmed "
                            "change in mobility."
                        ),
                        "reason": "Caregiver clarification; retain the original source.",
                        "caregiver_id": caregiver_id,
                    },
                )
                assert correction_response.status_code == 200, (
                    f"journey={index} correction: "
                    f"{correction_response.status_code} {correction_response.text}"
                )
                correction = correction_response.json()
                assert correction["target_id"] == evidence_id
                assert correction["original_text"] == source_text
                counts["corrections"] += 1

                # The public change-set workflow must append both the
                # per-target revision and the shared CareStateRevision envelope
                # consumed by historical as-of reconstruction.
                change_set_response = client.post(
                    "/api/v1/care-continuity/record-change-sets",
                    headers=headers,
                    json={
                        "operation": "caregiver_correction",
                        "reason": "Record a versioned correction to the event narrative.",
                    },
                )
                assert change_set_response.status_code == 201, (
                    f"journey={index} change set: "
                    f"{change_set_response.status_code} {change_set_response.text}"
                )
                change_set_id = change_set_response.json()["id"]
                before_state = {
                    "title": event["title"],
                    "description": source_text,
                    "occurred_at": occurred_at.isoformat(),
                    "evidence_ids": [evidence_id, counter_evidence_id],
                }
                after_state = {
                    "title": event["title"],
                    "description": (
                        "Caregiver clarification: the two accounts conflict; "
                        "retain both reports without resolving the disagreement."
                    ),
                    "occurred_at": occurred_at.isoformat(),
                    "evidence_ids": [evidence_id, counter_evidence_id],
                }
                change_item_response = client.post(
                    "/api/v1/care-continuity/record-change-items",
                    headers=headers,
                    json={
                        "change_set_id": change_set_id,
                        "target_type": "care_event",
                        "target_id": event_id,
                        "operation": "update",
                        "before_value": before_state,
                        "after_value": after_state,
                        "evidence_ids": [evidence_id, counter_evidence_id],
                    },
                )
                assert change_item_response.status_code == 201, (
                    f"journey={index} change item: "
                    f"{change_item_response.status_code} {change_item_response.text}"
                )
                as_of = datetime.now(UTC) + timedelta(minutes=1)
                reconstructed_response = client.get(
                    f"/api/v1/care-continuity/state-revisions/care_event/{event_id}/as-of",
                    headers=headers,
                    params={"as_of": as_of.isoformat()},
                )
                assert reconstructed_response.status_code == 200, (
                    f"journey={index} as-of reconstruction: "
                    f"{reconstructed_response.status_code} {reconstructed_response.text}"
                )
                reconstructed = reconstructed_response.json()
                assert reconstructed["target_type"] == "care_event"
                assert reconstructed["target_id"] == event_id
                assert reconstructed["historical_state_source"] == "persisted_care_state_revision"
                assert reconstructed["state"]["description"] == after_state["description"]
                assert set(reconstructed["evidence_ids"]) == {
                    evidence_id, counter_evidence_id
                }
                counts["historical_reconstructions"] += 1

                # Each read is a new HTTP request and therefore a new DB session.
                evidence_read = client.get(
                    f"/api/v1/evidence/{person_id}", headers=headers
                )
                assert evidence_read.status_code == 200, evidence_read.text
                matching_evidence = [
                    row for row in evidence_read.json() if row["id"] == evidence_id
                ]
                assert len(matching_evidence) == 1
                assert matching_evidence[0]["source_text"] == source_text
                assert matching_evidence[0]["reported_by_caregiver_id"] == caregiver_id
                assert any(row["id"] == counter_evidence_id for row in evidence_read.json())

                for source_id, expected_observer in (
                    (evidence_id, f"Family caregiver {index % 7}"),
                    (counter_evidence_id, "Second family account; identity unverified"),
                ):
                    observations_read = client.get(
                        f"/api/v1/care-continuity/evidence/{source_id}/observations",
                        headers=headers,
                    )
                    assert observations_read.status_code == 200, observations_read.text
                    assert any(
                        row["observer_label"] == expected_observer
                        and row["subject_label"] == "Care recipient"
                        for row in observations_read.json()
                    )

                events_read = client.get(
                    f"/api/v1/events/{person_id}", headers=headers
                )
                assert events_read.status_code == 200, events_read.text
                matching_events = [
                    row for row in events_read.json() if row["id"] == event_id
                ]
                assert len(matching_events) == 1
                assert matching_events[0]["occurred_at"].startswith(
                    occurred_at.isoformat()[:19]
                )
                assert matching_events[0]["recorded_at"] is not None

                conflicts_read = client.get(
                    f"/api/v1/uncertainty/contradictions/{person_id}",
                    headers=headers,
                )
                assert conflicts_read.status_code == 200, conflicts_read.text
                matching_conflicts = [
                    row for row in conflicts_read.json()
                    if row["id"] == conflict["id"]
                ]
                assert len(matching_conflicts) == 1
                assert matching_conflicts[0]["resolution_status"] == "unresolved"

                corrections_read = client.get(
                    f"/api/v1/corrections/{person_id}", headers=headers
                )
                assert corrections_read.status_code == 200, corrections_read.text
                matching_corrections = [
                    row for row in corrections_read.json()
                    if row["id"] == correction["id"]
                ]
                assert len(matching_corrections) == 1
                assert matching_corrections[0]["original_text"] == source_text

                lcr_response = client.get(
                    "/api/v1/care-continuity/context?limit_per_source=50",
                    headers=headers,
                )
                assert lcr_response.status_code == 200, lcr_response.text
                lcr = lcr_response.json()
                assert lcr["person_id"] == person_id
                assert lcr["read_only"] is True
                source_sets = lcr.get("sources") or {}
                lcr_evidence = source_sets.get("evidence", {}).get("items", [])
                lcr_events = source_sets.get("events", {}).get("items", [])
                assert any(row.get("id") == evidence_id for row in lcr_evidence)
                assert any(row.get("id") == counter_evidence_id for row in lcr_evidence)
                assert any(row.get("id") == event_id for row in lcr_events)
                counts["lcr_reads"] += 1

                # The unrelated caregiver cannot retrieve any of this journey.
                for path in (
                    f"/api/v1/evidence/{person_id}",
                    f"/api/v1/events/{person_id}",
                    f"/api/v1/uncertainty/contradictions/{person_id}",
                    f"/api/v1/corrections/{person_id}",
                ):
                    denied = client.get(path, headers=attacker_headers)
                    assert denied.status_code == 403, (
                        f"journey={index} foreign read {path}: "
                        f"{denied.status_code} {denied.text}"
                    )
                historical_denied = client.get(
                    f"/api/v1/care-continuity/state-revisions/care_event/{event_id}/as-of",
                    headers=attacker_headers,
                    params={"as_of": (datetime.now(UTC) + timedelta(minutes=1)).isoformat()},
                )
                assert historical_denied.status_code == 404, historical_denied.text
                counts["foreign_denials"] += 5
                counts["sessions"] += 1

        expected = JOURNEY_COUNT
        assert counts == {
            "sessions": expected,
            "evidence": expected * 2,
            "observations": expected * 2,
            "events": expected,
            "contradictions": expected,
            "corrections": expected,
            "historical_reconstructions": expected,
            "lcr_reads": expected,
            "foreign_denials": expected * 5,
        }, counts

        for key, value in counts.items():
            print(f"CAPTURE_TO_LCR_{key.upper()}={value}")
        print(f"CAPTURE_TO_LCR_JOURNEYS_EXECUTED={JOURNEY_COUNT}")
        print(f"CAPTURE_TO_LCR_FRESH_HTTP_READS={JOURNEY_COUNT * 7}")
        print(f"CAPTURE_TO_LCR_CROSS_PERSON_DENIALS={JOURNEY_COUNT * 5}")
        print("CAPTURE_TO_LCR_HISTORICAL_RECONSTRUCTION=PASS")
        print("CAPTURE_TO_LCR_ATTRIBUTION=PASS")
        print("CAPTURE_TO_LCR_OBSERVER_SUBJECT_ATTRIBUTION=PASS")
        print("CAPTURE_TO_LCR_CONTRADICTIONS_PRESERVED=PASS")
        print("CAPTURE_TO_LCR_CORRECTION_LINEAGE=PASS")
        print("CAPTURE_TO_LCR_DELAYED_EVIDENCE_TIME=PASS")
        print("CAPTURE_TO_LCR_LCR_RETRIEVAL=PASS")
        print("CAPTURE_TO_LCR_ACCESS_ISOLATION=PASS")
    finally:
        app.dependency_overrides.clear()
        main_module.SessionLocal = original_session_local
        engine.dispose()
