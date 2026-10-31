"""10,000 PostgreSQL-backed uncertainty-analysis API journeys.

Every case crosses the real FastAPI route, server-issued bearer-session
authorization, PostgreSQL persistence, and a subsequent HTTP retrieval request.
The corpus qualifies missing-evidence handling only; it is not clinical
accuracy validation or a substitute for full capture-to-LCR qualification.
"""
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import get_db
from app.main import app
import app.main as main_module

DATABASE_URL = os.getenv("SOLENOS_BENCHMARK_DATABASE_URL") or os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgresql+psycopg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")),
    reason="requires disposable PostgreSQL benchmark database",
)

SCENARIO_COUNT = 10_000
DOMAINS = ("appointment", "medication", "mobility", "nutrition", "sleep")


def test_10000_real_api_scenarios_persist_and_retrieve_unknown_care_threads():
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
    suffix = uuid.uuid4().hex[:10]

    try:
        with TestClient(app) as client:
            # Use the production session-bootstrap route so all relational
            # identities and FK dependencies are created by the real code path.
            created_response = client.post(
                "/api/v1/sessions/anonymous",
                json={
                    "name": f"Synthetic API benchmark person {suffix}",
                    "caregiver_name": f"Synthetic API benchmark caregiver {suffix}",
                },
            )
            assert created_response.status_code == 201, created_response.text
            created = created_response.json()
            person_id = created["person"]["id"]
            headers = {"Authorization": f"Bearer {created['access_token']}"}

            for index in range(SCENARIO_COUNT):
                subject_id = f"api-thread-{index:05d}"
                response = client.post(
                    "/api/v1/uncertainty/analyze",
                    headers=headers,
                    json={
                        "person_id": person_id,
                        "subject_type": DOMAINS[index % len(DOMAINS)],
                        "subject_id": subject_id,
                        "field": "current_status",
                        "claim": f"Independently labelled missing-status scenario {index:05d}.",
                        "task": "recommendation",
                        "evidence_ids": [],
                        "required_fields": ["current_status"],
                    },
                )
                assert response.status_code == 200, (
                    f"scenario={index:04d} status={response.status_code} body={response.text}"
                )
                payload = response.json()
                assert payload["analysis"]["epistemic_state"] == "unknown", f"scenario={index:04d}"
                assert payload["analysis"]["conclusion_confidence"] is None, f"scenario={index:04d}"
                assert payload["analysis"]["review_required"] is True, f"scenario={index:04d}"

            # A new HTTP request obtains a new dependency-managed SQLAlchemy session.
            listed = client.get(
                f"/api/v1/uncertainty/assessments/{person_id}",
                headers=headers,
            )
            assert listed.status_code == 200, listed.text
            rows = listed.json()
            actual_subjects = {row["subject_id"] for row in rows}
            expected_subjects = {f"api-thread-{index:05d}" for index in range(SCENARIO_COUNT)}
            assert expected_subjects <= actual_subjects
            assert len(expected_subjects & actual_subjects) == SCENARIO_COUNT
            for row in rows:
                if row["subject_id"] in expected_subjects:
                    assert row["epistemic_state"] == "unknown"
                    assert row.get("confidence") is None

            # A second server-issued identity cannot read the first person's records.
            other_response = client.post(
                "/api/v1/sessions/anonymous",
                json={
                    "name": f"Isolated synthetic person {suffix}",
                    "caregiver_name": f"Isolated synthetic caregiver {suffix}",
                },
            )
            assert other_response.status_code == 201, other_response.text
            other_token = other_response.json()["access_token"]
            denied = client.get(
                f"/api/v1/uncertainty/assessments/{person_id}",
                headers={"Authorization": f"Bearer {other_token}"},
            )
            assert denied.status_code == 403, denied.text

        print(f"MESSY_CARE_POSTGRES_API_SCENARIOS_EXECUTED={SCENARIO_COUNT}")
        print(f"MESSY_CARE_POSTGRES_API_ASSESSMENTS_RETRIEVED={SCENARIO_COUNT}")
        print("MESSY_CARE_POSTGRES_API_FRESH_HTTP_SESSION=PASS")
        print("MESSY_CARE_POSTGRES_API_CROSS_PERSON_ISOLATION=PASS")
    finally:
        app.dependency_overrides.clear()
        main_module.SessionLocal = original_session_local
        engine.dispose()


LIFECYCLE_COUNT = 10_000
LIFECYCLES_PER_PERSON = 10
LIFECYCLE_PERSON_COUNT = LIFECYCLE_COUNT // LIFECYCLES_PER_PERSON


def test_10000_real_api_care_to_lcr_lifecycles_preserve_provenance_and_isolation():
    """10,000 full capture-to-current-care API lifecycles on disposable PostgreSQL.

    Each journey writes two independent caregiver source records, two
    source-span-bound observations (the second append-only and corrective),
    and an unresolved contradiction. It then reads the current-care projection
    through a new request and proves the newest correction and both evidence
    references are visible. Ten journeys share a person so the bounded
    12-item projection can still expose the newest correction on every pass.
    A fresh SQLAlchemy session verifies every row after each ten-journey group.
    """
    import hashlib
    from datetime import datetime, timedelta, timezone

    from app.models.anonymous_session import AnonymousSession
    from app.models.care import Caregiver, Evidence, Person
    from app.models.care_continuity import EvidenceObservation
    from app.models.uncertainty import Contradiction

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:10]
    now = datetime.now(timezone.utc)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    original_session_local = main_module.SessionLocal
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = Session

    person_ids = []
    caregiver_ids = {}
    tokens = {}
    expected_by_person = {index: [] for index in range(LIFECYCLE_PERSON_COUNT)}

    def utc(value):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    try:
        # Identity fixtures are synthetic and isolated. All care evidence,
        # observations, corrections, contradictions and projections below use
        # the real application HTTP routes and bearer-session authorization.
        people = []
        caregivers = []
        sessions = []
        for person_index in range(LIFECYCLE_PERSON_COUNT):
            person_id = f"lcr10kp-{suffix}-{person_index:04d}"
            uploader_id = f"lcr10ku-{suffix}-{person_index:04d}"
            reporter_id = f"lcr10kr-{suffix}-{person_index:04d}"
            observer_id = f"lcr10ko-{suffix}-{person_index:04d}"
            token = f"lcr10k-token-{suffix}-{person_index:04d}"
            person_ids.append(person_id)
            caregiver_ids[person_index] = {
                "uploader": uploader_id,
                "reporter": reporter_id,
                "observer": observer_id,
            }
            tokens[person_index] = token
            people.append(Person(id=person_id, name=f"Synthetic LCR benchmark person {person_index:04d}"))
            caregivers.extend([
                Caregiver(id=uploader_id, name=f"Uploader {person_index:04d}", relationship="family", person_id=person_id),
                Caregiver(id=reporter_id, name=f"Reporter {person_index:04d}", relationship="family", person_id=person_id),
                Caregiver(id=observer_id, name=f"Observer {person_index:04d}", relationship="family", person_id=person_id),
            ])

        with Session() as db:
            db.add_all(people)
            db.flush()
            db.add_all(caregivers)
            db.flush()
            for person_index, person_id in enumerate(person_ids):
                token = tokens[person_index]
                sessions.append(AnonymousSession(
                    id=f"lcr10ks-{suffix}-{person_index:04d}",
                    person_id=person_id,
                    caregiver_id=caregiver_ids[person_index]["uploader"],
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    expires_at=now + timedelta(hours=8),
                ))
            db.add_all(sessions)
            db.commit()

        with TestClient(app) as client:
            for journey_index in range(LIFECYCLE_COUNT):
                person_index = journey_index // LIFECYCLES_PER_PERSON
                local_index = journey_index % LIFECYCLES_PER_PERSON
                person_id = person_ids[person_index]
                roles = caregiver_ids[person_index]
                headers = {"Authorization": f"Bearer {tokens[person_index]}"}
                subject_id = f"mobility-thread-{journey_index:05d}"
                event_time = now - timedelta(days=14, minutes=journey_index)
                first_reported_at = now - timedelta(days=2, seconds=journey_index)
                second_reported_at = now - timedelta(days=1, seconds=journey_index)
                first_collected_at = first_reported_at + timedelta(minutes=5)
                second_collected_at = second_reported_at + timedelta(minutes=5)

                first_text = (
                    f"Reporter {person_index:04d} case {journey_index:05d} "
                    "reported that walking support was needed."
                )
                first_evidence_response = client.post(
                    "/api/v1/evidence",
                    headers=headers,
                    json={
                        "person_id": person_id,
                        "type": "note",
                        "evidence_kind": "caregiver_report",
                        "epistemic_status": "unverified",
                        "source_text": first_text,
                        "uploaded_by_caregiver_id": roles["uploader"],
                        "reported_by_caregiver_id": roles["reporter"],
                        "reported_by_label": f"Reporter {person_index:04d}",
                        "collected_by_caregiver_id": roles["observer"],
                        "original_author": f"Reporter {person_index:04d}",
                        "occurred_at": event_time.isoformat(),
                        "reported_at": first_reported_at.isoformat(),
                        "collected_at": first_collected_at.isoformat(),
                        "origin_channel": "family_handover",
                    },
                )
                assert first_evidence_response.status_code in {200, 201}, (
                    f"journey={journey_index} first evidence: "
                    f"{first_evidence_response.status_code} {first_evidence_response.text}"
                )
                first_evidence_id = first_evidence_response.json()["id"]

                first_passage = "walking support was needed"
                first_start = first_text.index(first_passage)
                first_observation_response = client.post(
                    f"/api/v1/care-continuity/evidence/{first_evidence_id}/observations",
                    headers=headers,
                    json={
                        "observation_kind": "caregiver_report",
                        "epistemic_status": "reported",
                        "subject_label": "Person receiving care",
                        "attribute": "mobility_support",
                        "value_text": "Needed walking support",
                        "observer_label": f"Observer {person_index:04d}",
                        "observer_caregiver_id": roles["observer"],
                        "source_passage": first_passage,
                        "source_span_start": first_start,
                        "source_span_end": first_start + len(first_passage),
                        "occurred_at": event_time.isoformat(),
                        "reported_at": first_reported_at.isoformat(),
                        "extraction_method": "benchmark_manual",
                        "extraction_version": "1",
                    },
                )
                assert first_observation_response.status_code == 201, (
                    f"journey={journey_index} first observation: "
                    f"{first_observation_response.status_code} {first_observation_response.text}"
                )
                original_observation_id = first_observation_response.json()["id"]

                correction_text = (
                    f"Reporter {person_index:04d} case {journey_index:05d} "
                    "later corrected the report: walking support was not confirmed."
                )
                correction_evidence_response = client.post(
                    "/api/v1/evidence",
                    headers=headers,
                    json={
                        "person_id": person_id,
                        "type": "note",
                        "evidence_kind": "caregiver_report",
                        "epistemic_status": "unverified",
                        "source_text": correction_text,
                        "uploaded_by_caregiver_id": roles["uploader"],
                        "reported_by_caregiver_id": roles["reporter"],
                        "reported_by_label": f"Reporter {person_index:04d}",
                        "collected_by_caregiver_id": roles["observer"],
                        "original_author": f"Correction handover {person_index:04d}",
                        "occurred_at": event_time.isoformat(),
                        "reported_at": second_reported_at.isoformat(),
                        "collected_at": second_collected_at.isoformat(),
                        "origin_channel": "family_handover",
                    },
                )
                assert correction_evidence_response.status_code in {200, 201}, (
                    f"journey={journey_index} correction evidence: "
                    f"{correction_evidence_response.status_code} {correction_evidence_response.text}"
                )
                correction_evidence_id = correction_evidence_response.json()["id"]

                correction_passage = "walking support was not confirmed"
                correction_start = correction_text.index(correction_passage)
                correction_response = client.post(
                    f"/api/v1/care-continuity/evidence/{correction_evidence_id}/observations",
                    headers=headers,
                    json={
                        "observation_kind": "caregiver_report",
                        "epistemic_status": "reported",
                        "subject_label": "Person receiving care",
                        "attribute": "mobility_support",
                        "value_text": "Walking support was not confirmed",
                        "observer_label": f"Observer {person_index:04d}",
                        "observer_caregiver_id": roles["observer"],
                        "source_passage": correction_passage,
                        "source_span_start": correction_start,
                        "source_span_end": correction_start + len(correction_passage),
                        "occurred_at": event_time.isoformat(),
                        "reported_at": second_reported_at.isoformat(),
                        "extraction_method": "caregiver_correction",
                        "extraction_version": "1",
                        "supersedes_observation_id": original_observation_id,
                        "correction_rationale": "The reporting caregiver corrected the earlier interpretation; retain both source versions.",
                    },
                )
                assert correction_response.status_code == 201, (
                    f"journey={journey_index} correction observation: "
                    f"{correction_response.status_code} {correction_response.text}"
                )
                correction_observation_id = correction_response.json()["id"]

                conflict_response = client.post(
                    "/api/v1/uncertainty/contradictions",
                    headers=headers,
                    json={
                        "person_id": person_id,
                        "subject_type": "mobility",
                        "subject_id": subject_id,
                        "field": "mobility_support",
                        "description": (
                            "The original report and later correction differ. "
                            "Both source records remain available pending review."
                        ),
                        "evidence_ids": [first_evidence_id, correction_evidence_id],
                        "resolution_status": "unresolved",
                    },
                )
                assert conflict_response.status_code == 200, (
                    f"journey={journey_index} contradiction: "
                    f"{conflict_response.status_code} {conflict_response.text}"
                )
                conflict_id = conflict_response.json()["id"]
                assert conflict_response.json()["resolution_status"] == "unresolved"

                # The current-care projection reads care_conflicts, while the
                # uncertainty API stores contradictions in a separate table.
                # Link the LCR conflict directly to both source observations.
                lcr_conflict_response = client.post(
                    "/api/v1/care-continuity/conflicts",
                    headers=headers,
                    json={
                        "conflict_type": "conflicting_caregiver_reports",
                        "status": "unresolved",
                        "claim_refs": [original_observation_id, correction_observation_id],
                        "description": (
                            "Two caregiver source observations disagree about "
                            "the same mobility event; neither is silently selected."
                        ),
                    },
                )
                assert lcr_conflict_response.status_code == 201, (
                    f"journey={journey_index} LCR conflict: "
                    f"{lcr_conflict_response.status_code} {lcr_conflict_response.text}"
                )
                lcr_conflict_id = lcr_conflict_response.json()["id"]

                # This is the caregiver-facing LCR projection, not a direct table
                # read. Each journey is checked immediately while its ten-case
                # person partition keeps the newest correction inside the
                # projection's documented twelve-item bound.
                projection_response = client.get(
                    "/api/v1/care-continuity/current-care",
                    headers=headers,
                    params={"limit_per_source": 50},
                )
                assert projection_response.status_code == 200, (
                    f"journey={journey_index} current-care projection: "
                    f"{projection_response.status_code} {projection_response.text}"
                )
                projection_items = projection_response.json()["items"]
                conflict_item = next(
                    (item for item in projection_items
                     if item["source_collection"] == "conflicts" and item["id"] == lcr_conflict_id),
                    None,
                )
                assert conflict_item is not None, f"journey={journey_index}: contradiction missing from LCR projection"
                assert set(conflict_item["claim_refs"]) == {original_observation_id, correction_observation_id}

                correction_item = next(
                    (item for item in projection_items
                     if item["source_collection"] == "evidence_observations"
                     and item["id"] == correction_observation_id),
                    None,
                )
                assert correction_item is not None, f"journey={journey_index}: corrected observation missing from LCR projection"
                assert correction_item["evidence_ids"] == [correction_evidence_id]
                assert correction_item["supersedes_observation_id"] == original_observation_id
                assert correction_item["correction_rationale"]

                # Every journey independently attempts a cross-person read
                # using another caregiver's server-issued session.
                other_person_index = (person_index + 1) % LIFECYCLE_PERSON_COUNT
                denied = client.get(
                    f"/api/v1/evidence/{person_id}",
                    headers={"Authorization": f"Bearer {tokens[other_person_index]}"},
                )
                assert denied.status_code == 403, (
                    f"journey={journey_index} cross-person isolation: "
                    f"{denied.status_code} {denied.text}"
                )

                expected_by_person[person_index].append({
                    "journey_index": journey_index,
                    "event_time": event_time,
                    "first_reported_at": first_reported_at,
                    "second_reported_at": second_reported_at,
                    "first_evidence_id": first_evidence_id,
                    "correction_evidence_id": correction_evidence_id,
                    "original_observation_id": original_observation_id,
                    "correction_observation_id": correction_observation_id,
                    "conflict_id": conflict_id,
                    "lcr_conflict_id": lcr_conflict_id,
                })

                # A new SQLAlchemy session reads all ten journeys for this
                # person. This proves persistence of original and corrected
                # observations, source attribution, time provenance and
                # unresolved conflict links independently of the request
                # sessions that created them.
                if local_index == LIFECYCLES_PER_PERSON - 1:
                    with Session() as verify_db:
                        evidence_rows = verify_db.query(Evidence).filter(
                            Evidence.person_id == person_id
                        ).all()
                        observation_rows = verify_db.query(EvidenceObservation).filter(
                            EvidenceObservation.person_id == person_id
                        ).all()
                        conflict_rows = verify_db.query(Contradiction).filter(
                            Contradiction.person_id == person_id
                        ).all()

                        expected = expected_by_person[person_index]
                        assert len(evidence_rows) == LIFECYCLES_PER_PERSON * 2
                        assert len(observation_rows) == LIFECYCLES_PER_PERSON * 2
                        assert len(conflict_rows) == LIFECYCLES_PER_PERSON
                        evidence_by_id = {row.id: row for row in evidence_rows}
                        observation_by_id = {row.id: row for row in observation_rows}
                        conflict_by_id = {row.id: row for row in conflict_rows}

                        for item in expected:
                            for evidence_id, reported_at in (
                                (item["first_evidence_id"], item["first_reported_at"]),
                                (item["correction_evidence_id"], item["second_reported_at"]),
                            ):
                                evidence = evidence_by_id[evidence_id]
                                assert evidence.uploaded_by_caregiver_id == roles["uploader"]
                                assert evidence.reported_by_caregiver_id == roles["reporter"]
                                assert evidence.collected_by_caregiver_id == roles["observer"]
                                assert evidence.source_text_sha256
                                assert utc(evidence.occurred_at) == utc(item["event_time"])
                                assert utc(evidence.reported_at) == utc(reported_at)
                                assert utc(evidence.occurred_at) < utc(evidence.reported_at) < utc(evidence.ingested_at)

                            original = observation_by_id[item["original_observation_id"]]
                            corrected = observation_by_id[item["correction_observation_id"]]
                            assert original.supersedes_observation_id is None
                            assert corrected.supersedes_observation_id == original.id
                            assert corrected.correction_rationale
                            assert utc(corrected.occurred_at) == utc(item["event_time"])
                            assert utc(corrected.reported_at) == utc(item["second_reported_at"])
                            assert utc(corrected.occurred_at) < utc(corrected.reported_at) < utc(corrected.recorded_at)

                            conflict = conflict_by_id[item["conflict_id"]]
                            assert conflict.resolution_status == "unresolved"
                            assert set(conflict.evidence_ids) == {
                                item["first_evidence_id"], item["correction_evidence_id"]
                            }

        print(f"MESSY_CARE_POSTGRES_FULL_API_LIFECYCLES_EXECUTED={LIFECYCLE_COUNT}")
        print(f"MESSY_CARE_POSTGRES_LIFECYCLE_PERSONS={LIFECYCLE_PERSON_COUNT}")
        print(f"MESSY_CARE_POSTGRES_LIFECYCLE_EVIDENCE_RECORDS={LIFECYCLE_COUNT * 2}")
        print(f"MESSY_CARE_POSTGRES_LIFECYCLE_OBSERVATIONS={LIFECYCLE_COUNT * 2}")
        print(f"MESSY_CARE_POSTGRES_LIFECYCLE_CORRECTIONS={LIFECYCLE_COUNT}")
        print(f"MESSY_CARE_POSTGRES_LIFECYCLE_UNRESOLVED_CONTRADICTIONS={LIFECYCLE_COUNT}")
        print(f"MESSY_CARE_POSTGRES_LIFECYCLE_LCR_PROJECTIONS={LIFECYCLE_COUNT}")
        print(f"MESSY_CARE_POSTGRES_LIFECYCLE_FRESH_SESSION_GROUPS={LIFECYCLE_PERSON_COUNT}")
        print(f"MESSY_CARE_POSTGRES_LIFECYCLE_CROSS_PERSON_ISOLATION_CHECKS={LIFECYCLE_COUNT}")
        print("MESSY_CARE_POSTGRES_LIFECYCLE_LCR_PROJECTION=PASS")
        print("MESSY_CARE_POSTGRES_LIFECYCLE_FRESH_SESSION=PASS")
        print("MESSY_CARE_POSTGRES_LIFECYCLE_EVENT_REPORTED_RECORDED_TIME=PASS")
        print("MESSY_CARE_POSTGRES_LIFECYCLE_CROSS_PERSON_ISOLATION=PASS")
    finally:
        app.dependency_overrides.clear()
        main_module.SessionLocal = original_session_local
        engine.dispose()
