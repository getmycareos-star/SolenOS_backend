"""1,000 end-to-end PostgreSQL uncertainty-analysis API journeys.

Each labelled scenario crosses the real FastAPI route, bearer-session authorization,
SQLAlchemy persistence, and a second HTTP retrieval request. No handler or database
mock is used. The corpus tests missing-evidence behavior only; it is not clinical
accuracy validation and does not replace the 10,000-record persistence corpus.
"""
import hashlib
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
from app.models.anonymous_session import AnonymousSession
from app.models.care import Caregiver, Person

DATABASE_URL = os.getenv("SOLENOS_BENCHMARK_DATABASE_URL") or os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgresql+psycopg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")),
    reason="requires disposable PostgreSQL benchmark database",
)

SCENARIO_COUNT = 1_000


def test_1000_real_api_scenarios_persist_and_retrieve_unknown_care_threads():
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:10]
    person_id = f"api10kp{suffix}"
    caregiver_id = f"api10kc{suffix}"
    token = f"api10k-token-{suffix}"
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    original_session_local = main_module.SessionLocal
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = Session
    try:
        with Session() as db:
            db.add(Person(id=person_id, name="Synthetic API benchmark person"))
            db.flush()
            db.add(Caregiver(
                id=caregiver_id,
                name="Synthetic API benchmark caregiver",
                relationship="family",
                person_id=person_id,
            ))
            db.flush()
            db.add(AnonymousSession(
                id=f"api10ks{suffix}",
                person_id=person_id,
                caregiver_id=caregiver_id,
                token_hash=token_hash,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=3),
            ))
            db.commit()

        headers = {"Authorization": f"Bearer {token}"}
        with TestClient(app) as client:
            for index in range(SCENARIO_COUNT):
                subject_id = f"api-thread-{index:04d}"
                response = client.post(
                    "/api/v1/uncertainty/analyze",
                    headers=headers,
                    json={
                        "person_id": person_id,
                        "subject_type": ("appointment", "medication", "mobility", "nutrition", "sleep")[index % 5],
                        "subject_id": subject_id,
                        "field": "current_status",
                        "claim": f"Independently labelled missing-status scenario {index:04d}.",
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
            expected_subjects = {f"api-thread-{index:04d}" for index in range(SCENARIO_COUNT)}
            assert expected_subjects <= actual_subjects
            assert len(expected_subjects & actual_subjects) == SCENARIO_COUNT
            for row in rows:
                if row["subject_id"] in expected_subjects:
                    assert row["epistemic_state"] == "unknown"
                    assert row.get("confidence") is None

            # A second identity cannot read the benchmark person's assessment set.
            other_person_id = f"api10ko{suffix}"
            other_caregiver_id = f"api10kd{suffix}"
            other_token = f"api10k-other-{suffix}"
            with Session() as db:
                db.add(Person(id=other_person_id, name="Isolated synthetic person"))
                db.flush()
                db.add(Caregiver(
                    id=other_caregiver_id,
                    name="Isolated synthetic caregiver",
                    relationship="family",
                    person_id=other_person_id,
                ))
                db.flush()
                db.add(AnonymousSession(
                    id=f"api10ko-session-{suffix}",
                    person_id=other_person_id,
                    caregiver_id=other_caregiver_id,
                    token_hash=hashlib.sha256(other_token.encode()).hexdigest(),
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=3),
                ))
                db.commit()
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


def test_100_real_api_care_history_lifecycles_preserve_corrections_conflicts_and_questions():
    """100 PostgreSQL-backed write/read lifecycles across evidence, correction, conflict and question APIs.

    This complements the 1,000 missing-evidence assessment journeys. Each lifecycle
    uses independent source records and exact source spans, then retrieves the
    persisted result through fresh HTTP requests and a fresh SQLAlchemy session.
    """
    from app.models.care import Evidence
    from app.models.care_continuity import EvidenceObservation
    from app.models.uncertainty import Contradiction, OpenQuestion

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:10]
    person_id = f"life10kp{suffix}"
    uploader_id = f"life10ku{suffix}"
    reporter_id = f"life10kr{suffix}"
    observer_id = f"life10ko{suffix}"
    token = f"life10k-token-{suffix}"

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    original_session_local = main_module.SessionLocal
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = Session
    try:
        with Session() as db:
            db.add(Person(id=person_id, name="Synthetic care lifecycle person"))
            db.flush()
            db.add_all([
                Caregiver(id=uploader_id, name="Uploader", relationship="family", person_id=person_id),
                Caregiver(id=reporter_id, name="Reporter", relationship="family", person_id=person_id),
                Caregiver(id=observer_id, name="Observer", relationship="family", person_id=person_id),
            ])
            db.flush()
            db.add(AnonymousSession(
                id=f"life10ks{suffix}",
                person_id=person_id,
                caregiver_id=uploader_id,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=3),
            ))
            db.commit()

        headers = {"Authorization": f"Bearer {token}"}
        expected = []
        with TestClient(app) as client:
            for index in range(100):
                subject_id = f"mobility-thread-{index:03d}"
                first_text = f"Reporter {index:03d} says walking support was needed at 08:15."
                first_response = client.post(
                    "/api/v1/evidence",
                    headers=headers,
                    json={
                        "person_id": person_id,
                        "type": "note",
                        "evidence_kind": "caregiver_report",
                        "epistemic_status": "unverified",
                        "source_text": first_text,
                        "uploaded_by_caregiver_id": uploader_id,
                        "reported_by_caregiver_id": reporter_id,
                        "reported_by_label": f"Reporter {index:03d}",
                        "collected_by_caregiver_id": observer_id,
                        "original_author": f"Shift handover {index:03d}",
                        "occurred_at": "2026-10-28T08:15:00+00:00",
                        "reported_at": "2026-10-29T09:00:00+00:00",
                        "collected_at": "2026-10-29T09:05:00+00:00",
                        "origin_channel": "family_handover",
                    },
                )
                assert first_response.status_code in {200, 201}, (
                    f"first evidence case={index}: {first_response.status_code} {first_response.text}"
                )
                first_evidence_id = first_response.json()["id"]

                first_passage = "walking support was needed"
                first_start = first_text.index(first_passage)
                first_observation = client.post(
                    f"/api/v1/care-continuity/evidence/{first_evidence_id}/observations",
                    headers=headers,
                    json={
                        "observation_kind": "caregiver_report",
                        "epistemic_status": "reported",
                        "subject_label": "Person receiving care",
                        "attribute": "mobility_support",
                        "value_text": "Needed walking support",
                        "observer_label": f"Reporter {index:03d}",
                        "source_passage": first_passage,
                        "source_span_start": first_start,
                        "source_span_end": first_start + len(first_passage),
                        "extraction_method": "benchmark_manual",
                        "extraction_version": "1",
                        "reported_at": "2026-10-29T09:00:00+00:00",
                    },
                )
                assert first_observation.status_code == 201, (
                    f"first observation case={index}: {first_observation.status_code} {first_observation.text}"
                )
                original_observation_id = first_observation.json()["id"]

                second_text = f"Reporter {index:03d} corrects the earlier wording: walking support was not confirmed."
                second_response = client.post(
                    "/api/v1/evidence",
                    headers=headers,
                    json={
                        "person_id": person_id,
                        "type": "note",
                        "evidence_kind": "caregiver_report",
                        "epistemic_status": "unverified",
                        "source_text": second_text,
                        "uploaded_by_caregiver_id": uploader_id,
                        "reported_by_caregiver_id": reporter_id,
                        "reported_by_label": f"Reporter {index:03d}",
                        "collected_by_caregiver_id": observer_id,
                        "original_author": f"Correction handover {index:03d}",
                        "occurred_at": "2026-10-28T08:15:00+00:00",
                        "reported_at": "2026-10-30T10:00:00+00:00",
                        "origin_channel": "family_handover",
                    },
                )
                assert second_response.status_code in {200, 201}, (
                    f"second evidence case={index}: {second_response.status_code} {second_response.text}"
                )
                second_evidence_id = second_response.json()["id"]
                correction_passage = "walking support was not confirmed"
                correction_start = second_text.index(correction_passage)
                correction = client.post(
                    f"/api/v1/care-continuity/evidence/{second_evidence_id}/observations",
                    headers=headers,
                    json={
                        "observation_kind": "caregiver_report",
                        "epistemic_status": "reported",
                        "subject_label": "Person receiving care",
                        "attribute": "mobility_support",
                        "value_text": "Walking support was not confirmed",
                        "observer_label": f"Reporter {index:03d}",
                        "source_passage": correction_passage,
                        "source_span_start": correction_start,
                        "source_span_end": correction_start + len(correction_passage),
                        "extraction_method": "caregiver_correction",
                        "extraction_version": "1",
                        "reported_at": "2026-10-30T10:00:00+00:00",
                        "supersedes_observation_id": original_observation_id,
                        "correction_rationale": "Reporter corrected the earlier structured interpretation.",
                    },
                )
                assert correction.status_code == 201, (
                    f"correction case={index}: {correction.status_code} {correction.text}"
                )
                correction_id = correction.json()["id"]

                conflict_response = client.post(
                    "/api/v1/uncertainty/contradictions",
                    headers=headers,
                    json={
                        "person_id": person_id,
                        "subject_type": "mobility",
                        "subject_id": subject_id,
                        "field": "mobility_support",
                        "description": "The first report and later correction disagree; retain both pending review.",
                        "evidence_ids": [first_evidence_id, second_evidence_id],
                        "resolution_status": "unresolved",
                    },
                )
                assert conflict_response.status_code == 200, (
                    f"conflict case={index}: {conflict_response.status_code} {conflict_response.text}"
                )
                conflict_id = conflict_response.json()["id"]
                assert conflict_response.json()["resolution_status"] == "unresolved"

                question_response = client.post(
                    "/api/v1/uncertainty/questions",
                    headers=headers,
                    json={
                        "person_id": person_id,
                        "question": f"Can the mobility support report for case {index:03d} be confirmed?",
                        "subject_type": "mobility",
                        "subject_id": subject_id,
                        "status": "open",
                        "evidence_ids": [first_evidence_id, second_evidence_id],
                        "created_by_caregiver_id": uploader_id,
                    },
                )
                assert question_response.status_code == 200, (
                    f"question case={index}: {question_response.status_code} {question_response.text}"
                )
                question_id = question_response.json()["id"]
                expected.append({
                    "subject_id": subject_id,
                    "first_evidence_id": first_evidence_id,
                    "second_evidence_id": second_evidence_id,
                    "original_observation_id": original_observation_id,
                    "correction_id": correction_id,
                    "conflict_id": conflict_id,
                    "question_id": question_id,
                })

            # Fresh HTTP retrieval verifies all independently created records.
            evidence_list = client.get(f"/api/v1/evidence/{person_id}", headers=headers)
            assert evidence_list.status_code == 200, evidence_list.text
            evidence_by_id = {row["id"]: row for row in evidence_list.json()}
            observations_seen = set()
            for item in expected:
                for evidence_id in (item["first_evidence_id"], item["second_evidence_id"]):
                    record = evidence_by_id[evidence_id]
                    assert record["uploaded_by_caregiver_id"] == uploader_id
                    assert record["reported_by_caregiver_id"] == reporter_id
                    assert record["collected_by_caregiver_id"] == observer_id
                    assert record["source_text_sha256"]
                    observed = client.get(
                        f"/api/v1/care-continuity/evidence/{evidence_id}/observations",
                        headers=headers,
                    )
                    assert observed.status_code == 200, observed.text
                    observations_seen.update(row["id"] for row in observed.json())

            conflicts = client.get(
                f"/api/v1/uncertainty/contradictions/{person_id}",
                headers=headers,
            )
            assert conflicts.status_code == 200, conflicts.text
            conflict_rows = {row["id"]: row for row in conflicts.json()}
            questions = client.get(
                f"/api/v1/uncertainty/questions/{person_id}",
                headers=headers,
            )
            assert questions.status_code == 200, questions.text
            question_rows = {row["id"]: row for row in questions.json()}

            for item in expected:
                assert item["original_observation_id"] in observations_seen
                assert item["correction_id"] in observations_seen
                conflict = conflict_rows[item["conflict_id"]]
                assert conflict["resolution_status"] == "unresolved"
                assert set(conflict["evidence_ids"]) == {
                    item["first_evidence_id"], item["second_evidence_id"]
                }
                question = question_rows[item["question_id"]]
                assert question["status"] == "open"
                assert set(question["evidence_ids"]) == {
                    item["first_evidence_id"], item["second_evidence_id"]
                }

        with Session() as db:
            assert db.query(Evidence).filter(Evidence.person_id == person_id).count() == 200
            assert db.query(EvidenceObservation).filter(
                EvidenceObservation.person_id == person_id
            ).count() == 200
            assert db.query(Contradiction).filter(Contradiction.person_id == person_id).count() == 100
            assert db.query(OpenQuestion).filter(OpenQuestion.person_id == person_id).count() == 100
            for item in expected:
                correction_row = db.query(EvidenceObservation).filter_by(
                    id=item["correction_id"], person_id=person_id
                ).one()
                assert correction_row.supersedes_observation_id == item["original_observation_id"]
                assert correction_row.correction_rationale
                original_row = db.query(EvidenceObservation).filter_by(
                    id=item["original_observation_id"], person_id=person_id
                ).one()
                assert original_row.supersedes_observation_id is None

        print("MESSY_CARE_POSTGRES_FULL_LIFECYCLES_EXECUTED=100")
        print("MESSY_CARE_POSTGRES_EVIDENCE_RECORDS=200")
        print("MESSY_CARE_POSTGRES_OBSERVATIONS=200")
        print("MESSY_CARE_POSTGRES_CORRECTION_LINEAGE=100")
        print("MESSY_CARE_POSTGRES_UNRESOLVED_CONTRADICTIONS=100")
        print("MESSY_CARE_POSTGRES_OPEN_QUESTIONS=100")
        print("MESSY_CARE_POSTGRES_LIFECYCLE_FRESH_HTTP_RETRIEVAL=PASS")
        print("MESSY_CARE_POSTGRES_LIFECYCLE_FRESH_SESSION=PASS")
    finally:
        app.dependency_overrides.clear()
        main_module.SessionLocal = original_session_local
        engine.dispose()


def test_postgres_open_question_answer_versions_and_historical_unresolved_retrieval():
    """Answer revisions remain append-only; old unresolved questions remain queryable."""
    from app.models.care import Evidence
    from app.models.uncertainty import OpenQuestion, OpenQuestionAnswerEvent

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:10]
    person_id = f"qhistp{suffix}"
    caregiver_id = f"qhistc{suffix}"
    token = f"qhist-token-{suffix}"

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    original_session_local = main_module.SessionLocal
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = Session
    try:
        with Session() as db:
            db.add(Person(id=person_id, name="Synthetic question history person"))
            db.flush()
            db.add(Caregiver(
                id=caregiver_id, name="Question history caregiver",
                relationship="family", person_id=person_id,
            ))
            db.flush()
            db.add(AnonymousSession(
                id=f"qhists{suffix}", person_id=person_id,
                caregiver_id=caregiver_id,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=3),
            ))
            db.commit()

        headers = {"Authorization": f"Bearer {token}"}
        with TestClient(app) as client:
            evidence_ids = []
            for index in range(2):
                response = client.post(
                    "/api/v1/evidence",
                    headers=headers,
                    json={
                        "person_id": person_id,
                        "type": "note",
                        "evidence_kind": "caregiver_report",
                        "epistemic_status": "unverified",
                        "source_text": f"Question answer source {index}: mobility status recorded.",
                        "uploaded_by_caregiver_id": caregiver_id,
                        "reported_by_caregiver_id": caregiver_id,
                        "reported_by_label": "Question history caregiver",
                        "occurred_at": f"2026-10-{20 + index:02d}T08:00:00+00:00",
                        "reported_at": f"2026-10-{21 + index:02d}T09:00:00+00:00",
                        "origin_channel": "family_handover",
                    },
                )
                assert response.status_code in {200, 201}, response.text
                evidence_ids.append(response.json()["id"])

            question_response = client.post(
                "/api/v1/uncertainty/questions",
                headers=headers,
                json={
                    "person_id": person_id,
                    "question": "Was walking support required on the earlier shift?",
                    "subject_type": "mobility",
                    "subject_id": "historical-mobility",
                    "status": "open",
                    "evidence_ids": evidence_ids[:1],
                    "created_by_caregiver_id": caregiver_id,
                },
            )
            assert question_response.status_code == 200, question_response.text
            question_id = question_response.json()["id"]

            first_answer = client.patch(
                f"/api/v1/uncertainty/questions/{question_id}",
                headers=headers,
                json={
                    "status": "answered",
                    "answer": "The first source says support was required.",
                    "answer_provenance": "Initial review of source one.",
                    "evidence_ids": [evidence_ids[0]],
                },
            )
            assert first_answer.status_code == 200, first_answer.text

            revised_answer = client.patch(
                f"/api/v1/uncertainty/questions/{question_id}",
                headers=headers,
                json={
                    "status": "answered",
                    "answer": "The second source qualifies the first report; support remains unconfirmed.",
                    "answer_provenance": "Revised after reviewing the later source.",
                    "evidence_ids": [evidence_ids[1]],
                },
            )
            assert revised_answer.status_code == 200, revised_answer.text

            # Create older unresolved questions and verify the unfiltered history
            # endpoint returns them rather than only the latest item.
            historical_ids = []
            for index in range(50):
                response = client.post(
                    "/api/v1/uncertainty/questions",
                    headers=headers,
                    json={
                        "person_id": person_id,
                        "question": f"Historical unresolved mobility question {index:02d}?",
                        "subject_type": "mobility",
                        "subject_id": f"historical-thread-{index:02d}",
                        "status": "open",
                        "evidence_ids": [evidence_ids[0]],
                        "created_by_caregiver_id": caregiver_id,
                    },
                )
                assert response.status_code == 200, (
                    f"historical question={index}: {response.status_code} {response.text}"
                )
                historical_ids.append(response.json()["id"])

            history = client.get(
                f"/api/v1/uncertainty/questions/{question_id}/answer-history",
                headers=headers,
            )
            assert history.status_code == 200, history.text
            versions = history.json()
            assert [row["answer_version"] for row in versions] == [1, 2]
            assert versions[0]["answer"] == "The first source says support was required."
            assert versions[1]["answer"] == (
                "The second source qualifies the first report; support remains unconfirmed."
            )
            assert versions[0]["evidence_ids"] == [evidence_ids[0]]
            assert versions[1]["evidence_ids"] == [evidence_ids[1]]

            all_questions = client.get(
                f"/api/v1/uncertainty/questions/{person_id}",
                headers=headers,
            )
            assert all_questions.status_code == 200, all_questions.text
            question_rows = {row["id"]: row for row in all_questions.json()}
            assert question_id in question_rows
            assert question_rows[question_id]["status"] == "answered"
            assert set(historical_ids).issubset(question_rows)
            assert all(question_rows[item]["status"] == "open" for item in historical_ids)

            # Another person must not be able to retrieve this question or its
            # answer history through the same session-bound API.
            denied = client.get(
                f"/api/v1/uncertainty/questions/other-person-{suffix}",
                headers=headers,
            )
            assert denied.status_code == 403, denied.text
            hidden_history = client.get(
                f"/api/v1/uncertainty/questions/{question_id}/answer-history",
                headers={"Authorization": f"Bearer invalid-{token}"},
            )
            assert hidden_history.status_code in {401, 403}, hidden_history.text

        with Session() as db:
            assert db.query(OpenQuestion).filter(
                OpenQuestion.person_id == person_id
            ).count() == 51
            events = db.query(OpenQuestionAnswerEvent).filter(
                OpenQuestionAnswerEvent.person_id == person_id,
                OpenQuestionAnswerEvent.question_id == question_id,
            ).order_by(OpenQuestionAnswerEvent.answer_version.asc()).all()
            assert len(events) == 2
            assert [event.answer_version for event in events] == [1, 2]
            assert events[0].evidence_ids == [evidence_ids[0]]
            assert events[1].evidence_ids == [evidence_ids[1]]

        print("MESSY_CARE_POSTGRES_QUESTION_ANSWER_VERSIONS=2")
        print("MESSY_CARE_POSTGRES_HISTORICAL_OPEN_QUESTIONS=50")
        print("MESSY_CARE_POSTGRES_QUESTION_HISTORY_FRESH_SESSION=PASS")
        print("MESSY_CARE_POSTGRES_QUESTION_HISTORY_AUTHORIZATION=PASS")
    finally:
        app.dependency_overrides.clear()
        main_module.SessionLocal = original_session_local
        engine.dispose()
