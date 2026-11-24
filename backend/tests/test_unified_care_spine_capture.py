"""PostgreSQL HTTP integration for Daily Care -> LCR -> Companion retrieval.

This test intentionally runs only against the migrated PostgreSQL service. It
must not silently fall back to SQLite or a mocked persistence layer.
"""
import asyncio
import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import get_db
from app.api.document_lifecycle import _verify_persisted_extraction
from app.api.care_continuity import _build_situation_understanding_assessment
from app.main import app
from app.models.anonymous_session import AnonymousSession
from app.models.caregiver_access import CaregiverAccessGrant
from app.models.care import CareEvent, Caregiver, Correction, Evidence, Person
from app.models.capture_analysis import CaptureAnalysis
from app.models.care_continuity import CareAssertion, CareDeltaRun, CareRecordRelationship, CareStateRevision
from app.models.collaboration import ConversationMessage, ConversationSession, EvidenceUpload
from app.models.memory import LearningEvent
from app.models.qwen_background_job import QwenBackgroundJob, QwenJobEvent, QwenModelOutput, QwenSafetyEvaluation
import app.api.daily_care_intelligence as daily_care_intelligence
import app.api.collaboration as collaboration
import app.services.qwen_background_worker as qwen_background_worker
from app.services.unified_lcr_retrieval import build_unified_lcr_context


def _postgres_url() -> str:
    value = os.environ.get("DATABASE_URL", "").strip()
    if value.startswith("postgresql+psycopg://"):
        value = value.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
    if not value.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip("Unified care spine integration requires PostgreSQL DATABASE_URL")
    return value


def test_daily_capture_is_durable_and_retrievable_by_authorized_companion_context(monkeypatch):
    engine = create_engine(_postgres_url(), pool_pre_ping=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    suffix = uuid.uuid4().hex
    person_id = f"unified-spine-person-{suffix}"
    caregiver_id = f"unified-spine-caregiver-{suffix}"
    other_person_id = f"unified-spine-other-person-{suffix}"
    other_caregiver_id = f"unified-spine-other-caregiver-{suffix}"
    token = f"unified-spine-token-{suffix}"
    other_token = f"unified-spine-other-token-{suffix}"
    headers = {"Authorization": f"Bearer {token}"}

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with SessionLocal() as db:
            db.add_all([
                Person(id=person_id, name="PostgreSQL care spine recipient"),
                Person(id=other_person_id, name="Isolated PostgreSQL recipient"),
            ])
            db.flush()
            same_person_peer_caregiver_id = f"unified-spine-peer-caregiver-{suffix}"
            db.add_all([
                Caregiver(
                    id=caregiver_id, name="Family caregiver",
                    relationship="Family", person_id=person_id,
                ),
                Caregiver(
                    id=same_person_peer_caregiver_id, name="Other authorised caregiver",
                    relationship="Family", person_id=person_id,
                ),
                Caregiver(
                    id=other_caregiver_id, name="Other caregiver",
                    relationship="Family", person_id=other_person_id,
                ),
            ])
            db.flush()
            db.add_all([
                CaregiverAccessGrant(person_id=person_id, caregiver_id=caregiver_id, role="primary", access_scope="full_record", status="active", can_manage_members=True, granted_by_caregiver_id=caregiver_id),
                CaregiverAccessGrant(person_id=other_person_id, caregiver_id=other_caregiver_id, role="primary", access_scope="full_record", status="active", can_manage_members=True, granted_by_caregiver_id=other_caregiver_id),
            ])
            db.flush()
            own_document_text = "Care document reports interrupted sleep overnight."
            peer_document_text = "PRIVATE_PEER_DOCUMENT: another caregiver's separately scoped upload."
            own_document_bytes = b"CI document fixture: own caregiver upload"
            peer_document_bytes = b"CI document fixture: peer caregiver upload"
            own_document_evidence = Evidence(
                person_id=person_id, type="document", source_text=own_document_text,
                uploaded_by_caregiver_id=caregiver_id,
                reported_by_caregiver_id=caregiver_id,
                epistemic_status="caregiver_reported", evidence_kind="caregiver_report",
            )
            peer_document_evidence = Evidence(
                person_id=person_id, type="document", source_text=peer_document_text,
                uploaded_by_caregiver_id=same_person_peer_caregiver_id,
                reported_by_caregiver_id=same_person_peer_caregiver_id,
                epistemic_status="caregiver_reported", evidence_kind="caregiver_report",
            )
            db.add_all([own_document_evidence, peer_document_evidence])
            db.flush()
            own_document_analysis = CaptureAnalysis(
                person_id=person_id, caregiver_id=caregiver_id,
                source_evidence_id=own_document_evidence.id,
                source_text=own_document_text, extraction_status="available",
                candidates={
                    "kind": "document_extraction_v1", "filename": "own.pdf",
                    "binary_sha256": hashlib.sha256(own_document_bytes).hexdigest(),
                    "source_text_sha256": hashlib.sha256(own_document_text.encode()).hexdigest(),
                },
                review_history=[],
            )
            peer_document_analysis = CaptureAnalysis(
                person_id=person_id, caregiver_id=same_person_peer_caregiver_id,
                source_evidence_id=peer_document_evidence.id,
                source_text=peer_document_text, extraction_status="available",
                candidates={
                    "kind": "document_extraction_v1", "filename": "peer.pdf",
                    "binary_sha256": hashlib.sha256(peer_document_bytes).hexdigest(),
                    "source_text_sha256": hashlib.sha256(peer_document_text.encode()).hexdigest(),
                },
                review_history=[],
            )
            db.add_all([own_document_analysis, peer_document_analysis])
            db.flush()
            db.add_all([
                EvidenceUpload(
                    evidence_id=own_document_evidence.id, filename="own.pdf",
                    mime_type="application/pdf", content=own_document_bytes,
                    size_bytes=len(own_document_bytes),
                    sha256=hashlib.sha256(own_document_bytes).hexdigest(),
                ),
                EvidenceUpload(
                    evidence_id=peer_document_evidence.id, filename="peer.pdf",
                    mime_type="application/pdf", content=peer_document_bytes,
                    size_bytes=len(peer_document_bytes),
                    sha256=hashlib.sha256(peer_document_bytes).hexdigest(),
                ),
            ])
            db.flush()
            own_document_analysis_id = own_document_analysis.id
            peer_document_analysis_id = peer_document_analysis.id
            db.add_all([
                AnonymousSession(
                    person_id=person_id, caregiver_id=caregiver_id,
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                ),
                AnonymousSession(
                    person_id=other_person_id, caregiver_id=other_caregiver_id,
                    token_hash=hashlib.sha256(other_token.encode()).hexdigest(),
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                ),
            ])
            # Put a directly relevant historical event behind more than 250
            # newer unrelated records. Companion must still retrieve it by query.
            old_history_event = CareEvent(
                person_id=person_id,
                event_type="daily_capture",
                status="recorded",
                occurred_at=datetime.now(timezone.utc) - timedelta(days=500),
                title="Night waking report from last year",
                description="Caregiver reported repeated waking overnight for several nights.",
                evidence_ids=[],
                created_by_caregiver_id=caregiver_id,
            )
            db.add(old_history_event)
            db.flush()
            old_history_event_id = old_history_event.id
            now = datetime.now(timezone.utc)
            db.add_all([
                CareEvent(
                    person_id=person_id,
                    event_type="daily_capture",
                    status="recorded",
                    occurred_at=now - timedelta(minutes=index + 1),
                    title="Routine meal delivery",
                    description="Caregiver recorded meal delivery; no other details.",
                    evidence_ids=[],
                    created_by_caregiver_id=caregiver_id,
                )
                for index in range(260)
            ])
            db.commit()

        # Daily Care's history projection must apply the authenticated caregiver
        # filter to document extractions, just as the direct evidence list does.
        with SessionLocal() as db:
            original_builder = daily_care_intelligence.build_lcr_context
            observed_scopes = []

            def scoped_builder(*args, **kwargs):
                observed_scopes.append(kwargs.get("caregiver_id"))
                return original_builder(*args, **kwargs)

            monkeypatch.setattr(daily_care_intelligence, "build_lcr_context", scoped_builder)
            daily_care_intelligence._history_bundle(db, person_id, caregiver_id)
            assert observed_scopes == [caregiver_id]
            scoped_context = original_builder(db, person_id, caregiver_id=caregiver_id, limit_per_source=50)
            document_ids = {row["id"] for row in scoped_context["sources"]["document_extractions"]["items"]}
            assert own_document_analysis_id in document_ids
            assert peer_document_analysis_id not in document_ids

        reported_event_time = datetime.now(timezone.utc) - timedelta(seconds=30)
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/daily/log",
                headers=headers,
                json={
                    "person_id": person_id,
                    "caregiver_id": caregiver_id,
                    "capture_idempotency_key": "daily-capture-" + suffix,
                    "occurred_at": reported_event_time.isoformat(),
                    "occurred_at_timezone": "Africa/Lagos",
                    "notes": "Mum was unusually confused this afternoon and refused lunch.",
                },
            )
            assert response.status_code == 200, response.text
            receipt = response.json()
            assert receipt["success"] is True
            assert len(receipt["evidence_ids"]) == 1
            assert receipt["qwen_processing_status"] == "queued"
            assert receipt["qwen_extraction_status"] == "queued"
            event_id = receipt["id"]
            evidence_id = receipt["evidence_ids"][0]
            job_id = receipt["qwen_job_id"]
            assert job_id

            # The caregiver-facing receipt must not wait for extraction or
            # longitudinal reconciliation. Complete the durable work separately.
            def fake_capture_extraction(source_text, *, workflow_id, source_event_id, source_evidence_id):
                assert workflow_id == "daily_capture_extraction"
                assert "Mum was unusually confused this afternoon and refused lunch." in source_text
                return {
                    "observations": [
                        {
                            "source_quote": "Mum was unusually confused this afternoon",
                            "observation": "unusually confused this afternoon",
                            "review_status": "pending_review",
                        },
                        {
                            "source_quote": "refused lunch",
                            "observation": "refused lunch",
                            "review_status": "pending_review",
                        },
                    ],
                    "open_questions": [],
                }

            monkeypatch.setattr(
                qwen_background_worker, "extract_review_candidates", fake_capture_extraction
            )
            assert asyncio.run(qwen_background_worker.process_one_qwen_job(job_id=job_id)) is True
            with SessionLocal() as db:
                completed_job = db.query(QwenBackgroundJob).filter_by(
                    id=job_id, person_id=person_id, caregiver_id=caregiver_id
                ).one()
                completed_analysis = db.query(CaptureAnalysis).filter_by(
                    person_id=person_id, caregiver_id=caregiver_id,
                    source_event_id=event_id, source_evidence_id=evidence_id,
                ).one()
                assert completed_job.status == "completed"
                assert completed_analysis.extraction_status == "available"
                assert len(completed_analysis.candidates["observations"]) == 2
                assert all(
                    candidate["review_status"] == "pending_review"
                    for candidate in completed_analysis.candidates["observations"]
                )
                assert all(
                    candidate["source_quote"] in "Mum was unusually confused this afternoon and refused lunch."
                    for candidate in completed_analysis.candidates["observations"]
                )
                assert completed_analysis.execution_status["review"] == "required"
                assert completed_analysis.execution_status["projection"] == "verified"
                assert completed_analysis.execution_status["longitudinal_retrieval"] == "verified"
                # The Delta Engine is not mocked in this PostgreSQL journey:
                # require a persisted run tied to this exact source event.
                assert completed_analysis.execution_status["delta_reconciliation"] == "completed"
                delta_run_id = completed_analysis.execution_status["delta_run_id"]
                assert delta_run_id
                assert db.query(CareDeltaRun).filter_by(
                    id=delta_run_id,
                    person_id=person_id,
                    source_event_id=event_id,
                    source="SolenOS Daily Capture",
                ).one_or_none() is not None
                analysis_id = completed_analysis.id

            job_status_response = client.get(
                f"/api/v1/assistant/jobs/{job_id}", headers=headers
            )
            assert job_status_response.status_code == 200, job_status_response.text
            assert job_status_response.json()["status"] == "completed"
            assert job_status_response.json()["daily_capture"]["projection_status"] == "verified"
            assert job_status_response.json()["review_required"] is True

            analysis_response = client.get(
                f"/api/v1/capture/analyses/{analysis_id}", headers=headers
            )
            assert analysis_response.status_code == 200, analysis_response.text
            assert analysis_response.json()["review_required"] is True
            assert len(analysis_response.json()["candidates"]["observations"]) == 2
            assert analysis_response.json()["candidates"]["observations"][0]["review_status"] == "pending_review"
            assert analysis_response.json()["candidates"]["observations"][1]["review_status"] == "pending_review"
            foreign_analysis_response = client.get(
                f"/api/v1/capture/analyses/{analysis_id}",
                headers={"Authorization": "Bearer " + other_token},
            )
            assert foreign_analysis_response.status_code == 404

            history_response = client.get(
                "/api/v1/daily-care/intelligence/history?limit=200&offset=0",
                headers=headers,
            )
            assert history_response.status_code == 200, history_response.text
            history_capture = next(
                row for row in history_response.json()["records"]
                if row.get("source_id") == event_id
            )
            assert history_capture["capture_processing"]["analysis_id"] == analysis_id
            assert history_capture["capture_processing"]["extraction_status"] == "available"
            assert history_capture["capture_processing"]["review_required"] is True

            # AI-extracted detail must remain pending until this caregiver explicitly approves it.
            # The accepted assertion must preserve the original source and be visible to a fresh LCR read.
            review_response = client.post(
                f"/api/v1/capture/analyses/{analysis_id}/review",
                headers=headers,
                json={
                    "candidate_index": 0,
                    "decision": "accept",
                    "expected_source_quote": analysis_response.json()["candidates"]["observations"][0].get("source_quote") or "",
                    "expected_subject": analysis_response.json()["candidates"]["observations"][0].get("subject") or "",
                    "expected_observation": analysis_response.json()["candidates"]["observations"][0].get("observation") or "",
                },
            )
            assert review_response.status_code == 200, review_response.text
            review_body = review_response.json()
            assert review_body["review_decision"] == "accept"
            assert review_body["written_to_lcr"] is True
            assert review_body["assertion_id"]
            assert review_body["longitudinal_retrieval_status"] == "verified"

            accepted_analysis = client.get(
                f"/api/v1/capture/analyses/{analysis_id}", headers=headers
            )
            assert accepted_analysis.status_code == 200, accepted_analysis.text
            assert accepted_analysis.json()["review_required"] is True
            assert accepted_analysis.json()["candidates"]["observations"][0]["review_status"] == "accepted_by_caregiver"
            assert accepted_analysis.json()["candidates"]["observations"][1]["review_status"] == "pending_review", (
                "accepting one observation must not silently accept the other extracted observation"
            )

            with SessionLocal() as db:
                accepted_assertion = db.query(CareAssertion).filter_by(
                    id=review_body["assertion_id"], person_id=person_id
                ).one()
                assert accepted_assertion.assertion_status == "caregiver_reviewed"
                assert accepted_assertion.source_event_id == event_id
                assert accepted_assertion.evidence_ids == [evidence_id]
                accepted_context = build_unified_lcr_context(
                    db, person_id, caregiver_id, query="confused lunch", limit_per_source=50
                )
                accepted_ids = {
                    row["id"] for row in accepted_context["sources"]["assertions"]["items"]
                }
                assert accepted_assertion.id in accepted_ids

            reviewed_history = client.get(
                "/api/v1/daily-care/intelligence/history?limit=200&offset=0",
                headers=headers,
            )
            assert reviewed_history.status_code == 200, reviewed_history.text
            reviewed_capture = next(
                row for row in reviewed_history.json()["records"]
                if row.get("source_id") == event_id
            )
            assert reviewed_capture["capture_processing"]["review_required"] is True, (
                "one accepted observation must not clear the remaining observation's review requirement"
            )

            retry = client.post(
                "/api/v1/daily/log",
                headers=headers,
                json={
                    "person_id": person_id,
                    "caregiver_id": caregiver_id,
                    "capture_idempotency_key": "daily-capture-" + suffix,
                    "occurred_at": reported_event_time.isoformat(),
                    "occurred_at_timezone": "Africa/Lagos",
                    "notes": "Mum was unusually confused this afternoon and refused lunch.",
                },
            )
            assert retry.status_code == 200, retry.text
            assert retry.json()["id"] == event_id
            assert retry.json()["evidence_ids"] == [evidence_id]
            assert retry.json()["idempotent_replay"] is True

            conflicting_retry = client.post(
                "/api/v1/daily/log",
                headers=headers,
                json={
                    "person_id": person_id,
                    "caregiver_id": caregiver_id,
                    "capture_idempotency_key": "daily-capture-" + suffix,
                    "notes": "Different content with the same request key.",
                },
            )
            assert conflicting_retry.status_code == 409, conflicting_retry.text

            # A separate database session must read the committed write.
            with SessionLocal() as db:
                event = db.query(CareEvent).filter_by(
                    id=event_id, person_id=person_id
                ).one()
                evidence = db.query(Evidence).filter_by(
                    id=evidence_id, person_id=person_id
                ).one()
                assert event.evidence_ids == [evidence_id]
                assert event.created_by_caregiver_id == caregiver_id
                assert event.occurred_at == reported_event_time
                assert event.occurred_at_timezone == "Africa/Lagos"
                assert evidence.occurred_at == reported_event_time
                assert evidence.occurred_at_timezone == "Africa/Lagos"
                assert evidence.reported_by_caregiver_id == caregiver_id
                assert evidence.epistemic_status == "caregiver_reported"

                # Companion uses this shared retrieval composition. It must
                # retrieve the same committed source IDs from a fresh session.
                context = build_unified_lcr_context(
                    db, person_id, caregiver_id,
                    query="confused lunch",
                    limit_per_source=50,
                )
                situation = _build_situation_understanding_assessment(db, person_id)
                situation_event = next(
                    item for item in situation["current_care_reality"]["records"]
                    if item.get("source") == "events" and item.get("id") == event_id
                )
                assert evidence_id in situation_event["evidence_ids"]
                situation_timeline_entry = next(
                    item for item in situation["situation_trajectory"]["entries"]
                    if item.get("record_id") == event_id
                )
                assert evidence_id in situation_timeline_entry["evidence_ids"]
                assert situation["assessment_method"] == "deterministic_composition_of_persisted_lcr_sources"

            event_rows = [
                row for row in context["sources"]["events"]["items"]
                if row["id"] == event_id
            ]
            evidence_rows = [
                row for row in context["sources"]["evidence"]["items"]
                if row["id"] == evidence_id
            ]
            assert len(event_rows) == 1, context["sources"]["events"]
            assert len(evidence_rows) == 1, context["sources"]["evidence"]
            assert event_rows[0]["evidence_ids"] == [evidence_id]
            assert event_rows[0]["occurred_at_timezone"] == "Africa/Lagos"
            assert event_rows[0]["occurred_at"] == reported_event_time.isoformat()
            assert evidence_rows[0]["occurred_at_timezone"] == "Africa/Lagos"
            assert evidence_rows[0]["epistemic_status"] == "caregiver_reported"
            assert context["retrieval_contract"]["caregiver_scope_applied"] is True

            lcr = client.get(
                "/api/v1/care-continuity/current-care?limit_per_source=50",
                headers=headers,
            )
            assert lcr.status_code == 200, lcr.text
            lcr_matches = [
                item for item in lcr.json()["items"] if item["id"] == event_id
            ]
            assert len(lcr_matches) == 1, (
                "Daily Capture event missing from current-care projection; "
                f"response={lcr.json()}"
            )
            assert lcr_matches[0]["evidence_ids"] == [evidence_id]

            # Exercise the actual Companion HTTP path, not only the retrieval
            # helper. The saved Daily Care event and its linked evidence must be
            # supplied to the model and cited in the response metadata.
            captured_prompts = []

            def fake_companion_inference(request_id, model, messages, options, retrieval_duration_ms, audit_context=None):
                captured_prompts.append(messages)
                return "The care record contains the saved caregiver report."

            monkeypatch.setattr(
                collaboration,
                "_run_companion_inference_with_metrics",
                fake_companion_inference,
            )
            monkeypatch.setattr(
                collaboration,
                "_ollama_url",
                lambda: ("http://ollama.test", "qwen-test"),
            )
            companion_daily = client.post(
                "/api/v1/conversation/message",
                headers=headers,
                json={
                    "person_id": person_id,
                    "text": "What was recorded about Mum being unusually confused and refusing lunch?",
                    "client_turn_id": str(uuid.uuid4()),
                },
            )
            assert companion_daily.status_code == 200, companion_daily.text
            companion_daily_body = companion_daily.json()
            assert event_id in companion_daily_body.get("source_event_ids", [])
            assert evidence_id in companion_daily_body.get("source_evidence_ids", [])
            assert review_body["assertion_id"] in companion_daily_body.get("source_assertion_ids", []), "Companion must retrieve the same caregiver-approved assertion from the shared LCR"
            assert companion_daily_body["written_to_lcr"] is False
            daily_prompt = "\\n".join(
                str(message.get("content", "")) for message in captured_prompts[-1]
            )

            historical_companion = client.post(
                "/api/v1/conversation/message",
                headers=headers,
                json={
                    "person_id": person_id,
                    "text": "What was recorded about waking overnight?",
                    "client_turn_id": str(uuid.uuid4()),
                },
            )
            assert historical_companion.status_code == 200, historical_companion.text
            historical_body = historical_companion.json()
            assert old_history_event_id in historical_body.get("source_event_ids", [])
            historical_prompt = "\\n".join(
                str(message.get("content", "")) for message in captured_prompts[-1]
            )
            # Internal IDs belong in response provenance metadata, not
            # caregiver-facing prompt prose. The old event's source content
            # must be present in the model context.
            assert old_history_event_id in historical_body.get("source_event_ids", [])
            assert "repeated waking overnight" in historical_prompt
            # Internal database IDs belong in response provenance metadata, not
            # caregiver-facing model prose. Verify the exact saved report content
            # and epistemic boundary are present in the prompt instead.
            assert "Mum was unusually confused this afternoon and refused lunch." in daily_prompt
            assert "caregiver_reported" in daily_prompt
            assert event_id in companion_daily_body.get("source_event_ids", [])
            assert evidence_id in companion_daily_body.get("source_evidence_ids", [])

            # Prove the exact join used by Companion can see the upload,
            # analysis, and uploader identity before exercising the HTTP path.
            with SessionLocal() as db:
                linked_document = (
                    db.query(CaptureAnalysis, Evidence)
                    .join(Evidence, Evidence.id == CaptureAnalysis.source_evidence_id)
                    .filter(
                        CaptureAnalysis.id == own_document_analysis_id,
                        CaptureAnalysis.person_id == person_id,
                        CaptureAnalysis.caregiver_id == caregiver_id,
                        CaptureAnalysis.extraction_status == "available",
                        Evidence.person_id == person_id,
                        Evidence.uploaded_by_caregiver_id == caregiver_id,
                    )
                    .one_or_none()
                )
                assert linked_document is not None, "caregiver-scoped document analysis/evidence join returned no row"
                assert linked_document[1].type == "document"
                linked_upload = db.query(EvidenceUpload).filter_by(
                    evidence_id=linked_document[1].id
                ).one_or_none()
                assert linked_upload is not None
                assert _verify_persisted_extraction(
                    linked_document[0], linked_upload, db
                ) == own_document_bytes

            # The Companion document question must include this caregiver's
            # persisted document extraction and exclude a peer caregiver's
            # private document from the same person's account.
            companion_document = client.post(
                "/api/v1/conversation/message",
                headers=headers,
                json={
                    "person_id": person_id,
                    "text": "What does the discharge summary say about sleep?",
                    "client_turn_id": str(uuid.uuid4()),
                },
            )
            assert companion_document.status_code == 200, companion_document.text
            companion_document_body = companion_document.json()
            assert own_document_analysis_id in companion_document_body.get(
                "source_document_analysis_ids", []
            ), (
                "verified document extraction was not cited in Companion response; "
                f"source_ids={companion_document_body.get('source_document_analysis_ids')}, "
                f"coverage={companion_document_body.get('lcr_context_coverage')}, "
                f"prompt={captured_prompts[-1]}"
            )
            assert peer_document_analysis_id not in companion_document_body.get(
                "source_document_analysis_ids", []
            )
            document_prompt = "\\n".join(
                str(message.get("content", "")) for message in captured_prompts[-1]
            )
            # Internal analysis IDs remain response provenance metadata;
            # only the document's relevant source content enters model prose.
            assert own_document_analysis_id in companion_document_body.get(
                "source_document_analysis_ids", []
            )
            assert "Care document reports interrupted sleep overnight." in document_prompt
            assert "PRIVATE_PEER_DOCUMENT" not in document_prompt
            assert companion_document_body["written_to_lcr"] is False

            foreign_capture = client.post(
                "/api/v1/daily/log",
                headers={"Authorization": "Bearer " + other_token},
                json={
                    "person_id": person_id,
                    "caregiver_id": caregiver_id,
                    "notes": "Unauthorized cross-recipient write attempt.",
                },
            )
            assert foreign_capture.status_code == 403, foreign_capture.text
            foreign_history = client.get(
                "/api/v1/events/" + person_id,
                headers={"Authorization": "Bearer " + other_token},
            )
            assert foreign_history.status_code == 403, foreign_history.text

            # A different server-issued session cannot read this recipient's LCR.
            foreign = client.get(
                "/api/v1/care-continuity/current-care?limit_per_source=50",
                headers={"Authorization": f"Bearer {other_token}"},
            )
            assert foreign.status_code == 200, foreign.text
            assert not any(
                item["id"] == event_id for item in foreign.json()["items"]
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()

def test_postgres_worker_recovers_stale_qwen_job_after_restart(monkeypatch):
    """A crashed worker's processing lease is reclaimed and its result is durable in PostgreSQL."""
    engine = create_engine(_postgres_url(), pool_pre_ping=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    suffix = uuid.uuid4().hex
    person_id = f"qwen-restart-person-{suffix}"
    caregiver_id = f"qwen-restart-caregiver-{suffix}"
    job_id = None
    monkeypatch.setattr(qwen_background_worker, "SessionLocal", SessionLocal)

    try:
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            db.add(Person(id=person_id, name="Worker restart test recipient"))
            db.flush()
            db.add(Caregiver(
                id=caregiver_id,
                name="Worker restart test caregiver",
                relationship="Family",
                person_id=person_id,
            ))
            db.flush()
            db.add(CaregiverAccessGrant(
                person_id=person_id,
                caregiver_id=caregiver_id,
                role="primary",
                access_scope="full_record",
                status="active",
                can_manage_members=True,
                granted_by_caregiver_id=caregiver_id,
            ))
            db.flush()
            job = QwenBackgroundJob(
                person_id=person_id,
                caregiver_id=caregiver_id,
                task_text="Please summarize the supplied appointment note.",
                context_text="Appointment date: not supplied.",
                status="processing",
                attempt_count=1,
                locked_at=now - timedelta(minutes=20),
                available_at=now - timedelta(minutes=20),
            )
            db.add(job)
            db.commit()
            job_id = job.id

        async def classify(*args, **kwargs):
            return "appointment"

        async def complete_assist(payload, request, db):
            assert payload.workflow == "appointment"
            assert request.state.scoped_person_id == person_id
            return {
                "workflow": "appointment",
                "reply": "The appointment date was not supplied.",
                "ai_generated": True,
                "provider_status": "available",
                "model": "qwen3.5:4b",
                "source_event_ids": [],
                "source_evidence_ids": [],
                "source_appointment_ids": ["appointment-record-restart-test"],
                "source_handoff_ids": [],
                "source_notification_ids": [],
                "source_freshness_assessment_ids": [],
                "source_freshness_status": "available",
                "claim_provenance": {
                    "version": "claim-provenance-v1",
                    "claims": [{
                        "claim_id": "statement-1",
                        "text": "The appointment date was not supplied.",
                        "support_status": "not_matched_to_saved_wording",
                        "source_references": [],
                    }],
                },
            }

        monkeypatch.setattr(qwen_background_worker, "_classify_task", classify)
        monkeypatch.setattr(collaboration, "qwen_support_assist", complete_assist)

        assert asyncio.run(qwen_background_worker.process_one_qwen_job()) is True

        with SessionLocal() as db:
            job = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
            assert job.status == "completed"
            assert job.workflow == "appointment"
            assert job.attempt_count == 2
            assert job.result_text is None
            assert job.completed_at is not None
            assert job.locked_at is None

            output = db.query(QwenModelOutput).filter_by(job_id=job_id).one()
            assert output.output_text == "The appointment date was not supplied."
            assert output.review_status == "pending_review"
            assert output.review_required is True
            assert output.written_to_lcr is False
            assert output.output_metadata["live_inference_confirmed"] is True
            assert output.output_metadata["source_appointment_ids"] == ["appointment-record-restart-test"]

            safety = db.query(QwenSafetyEvaluation).filter_by(output_id=output.id).one()
            assert safety.decision == "review_required"
            assert safety.human_review_required is True
            assert db.query(QwenJobEvent).filter_by(
                job_id=job_id, event_type="job_claimed"
            ).count() == 1
            assert db.query(QwenJobEvent).filter_by(
                job_id=job_id, event_type="job_completed"
            ).count() == 1
    finally:
        if job_id:
            with SessionLocal() as db:
                db.query(QwenSafetyEvaluation).filter_by(job_id=job_id).delete(synchronize_session=False)
                db.query(QwenModelOutput).filter_by(job_id=job_id).delete(synchronize_session=False)
                db.query(QwenJobEvent).filter_by(job_id=job_id).delete(synchronize_session=False)
                db.query(QwenBackgroundJob).filter_by(id=job_id).delete(synchronize_session=False)
                db.query(CareStateRevision).filter_by(person_id=person_id).delete(synchronize_session=False)
                db.query(Caregiver).filter_by(id=caregiver_id).delete(synchronize_session=False)
                db.query(Person).filter_by(id=person_id).delete(synchronize_session=False)
                db.commit()
        engine.dispose()

def test_postgres_companion_history_retrieval_never_answers_from_corrected_or_pending_reports(monkeypatch):
    """Correction lineage is checked in PostgreSQL before direct Companion history recall."""
    engine = create_engine(_postgres_url(), pool_pre_ping=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    suffix = uuid.uuid4().hex
    person_id = str(uuid.uuid4())
    caregiver_id = str(uuid.uuid4())
    corrected_event_id = str(uuid.uuid4())
    pending_event_id = str(uuid.uuid4())
    current_event_id = str(uuid.uuid4())
    applied_correction_id = str(uuid.uuid4())
    pending_correction_id = str(uuid.uuid4())
    aggregate_event_id = str(uuid.uuid4())
    aggregate_correction_id = str(uuid.uuid4())
    relationship_id = str(uuid.uuid4())
    aggregate_event_time = datetime.now(timezone.utc) - timedelta(hours=1)

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with SessionLocal() as db:
            db.add(Person(id=person_id, name="PostgreSQL Companion lineage recipient"))
            db.flush()
            db.add(Caregiver(
                id=caregiver_id, name="Family caregiver",
                relationship="Family", person_id=person_id,
            ))
            db.flush()
            db.add_all([
                CareEvent(
                    id=corrected_event_id, person_id=person_id,
                    event_type="observation", status="recorded",
                    occurred_at=datetime(2026, 2, 3, 9, 0, tzinfo=timezone.utc),
                    title="Corrected sleep report",
                    description="Caregiver reported that Mum slept poorly.",
                    evidence_ids=[], created_by_caregiver_id=caregiver_id,
                    time_provenance="caregiver_reported",
                ),
                CareEvent(
                    id=pending_event_id, person_id=person_id,
                    event_type="observation", status="recorded",
                    occurred_at=datetime(2026, 2, 4, 9, 0, tzinfo=timezone.utc),
                    title="Sleep report awaiting correction",
                    description="Caregiver reported that Mum slept through the night.",
                    evidence_ids=[], created_by_caregiver_id=caregiver_id,
                    time_provenance="caregiver_reported",
                ),
                CareEvent(
                    id=current_event_id, person_id=person_id,
                    event_type="observation", status="recorded",
                    occurred_at=datetime(2026, 2, 5, 9, 0, tzinfo=timezone.utc),
                    title="Current sleep report",
                    description="Caregiver reported that Mum slept well.",
                    evidence_ids=[], created_by_caregiver_id=caregiver_id,
                    time_provenance="caregiver_reported",
                ),
                CareEvent(
                    id=aggregate_event_id, person_id=person_id,
                    event_type="observation", status="recorded",
                    occurred_at=aggregate_event_time,
                    title="Unsteadiness observation",
                    description="Caregiver reported an unsteadiness episode.",
                    evidence_ids=[], created_by_caregiver_id=caregiver_id,
                    time_provenance="caregiver_reported",
                ),
            ])
            db.flush()
            db.add_all([
                Correction(
                    id=applied_correction_id, person_id=person_id,
                    target_type="care_event", target_id=corrected_event_id,
                    original_text="Mum slept poorly.",
                    corrected_text="Caregiver did not observe Mum sleeping poorly.",
                    reason="The original report overstated what was observed.",
                    caregiver_id=caregiver_id, processing_status="completed",
                    processing_attempts=1,
                ),
                Correction(
                    id=pending_correction_id, person_id=person_id,
                    target_type="care_event", target_id=pending_event_id,
                    original_text="Mum slept through the night.",
                    corrected_text="Caregiver is unsure whether Mum slept through the night.",
                    reason="The caregiver is checking the overnight notes.",
                    caregiver_id=caregiver_id, processing_status="pending",
                    processing_attempts=0,
                ),
                Correction(
                    id=aggregate_correction_id, person_id=person_id,
                    target_type="care_event", target_id=aggregate_event_id,
                    original_text="Caregiver reported an unsteadiness episode.",
                    corrected_text="Caregiver is unsure whether this was an unsteadiness episode.",
                    reason="The event details need confirmation.",
                    caregiver_id=caregiver_id, processing_status="pending",
                    processing_attempts=0,
                ),
            ])
            db.add(CareRecordRelationship(
                id=relationship_id, person_id=person_id,
                source_type="correction", source_id=applied_correction_id,
                relationship_type="corrects", target_type="care_event",
                target_id=corrected_event_id,
                provenance="Caregiver correction; original report retained.",
                created_by_caregiver_id=caregiver_id,
            ))
            db.commit()

            context = collaboration._care_history_recall_context_lines(
                db, person_id, "What has the caregiver recorded about sleep?"
            )
            records = {
                json.loads(line.partition(":")[2].strip())["event_id"]:
                json.loads(line.partition(":")[2].strip())["record"]
                for line in context["event_lines"]
            }
            assert context["correction_lineage_complete"] is True
            assert records[corrected_event_id]["information_status"] == "historical_event"
            assert records[corrected_event_id]["corrections"][0]["corrected_text"] == (
                "Caregiver did not observe Mum sleeping poorly."
            )
            assert records[pending_event_id]["information_status"] == "event_correction_pending"
            assert records[current_event_id]["information_status"] == "caregiver_reported"
            assert records[current_event_id]["occurred_at"] is not None
            assert records[current_event_id]["recorded_at"] is not None

            answer = collaboration._care_history_extractive_answer(
                "What has the caregiver recorded about sleep?", context["event_lines"]
            )
            assert answer is not None
            assert "slept well" in answer.casefold()
            assert "slept poorly" not in answer.casefold()
            assert "slept through the night" not in answer.casefold()

            aggregate = collaboration._requested_event_aggregates(
                db, person_id, caregiver_id,
                "How many unsteadiness incidents this month?",
            )[0]
            assert aggregate["status"] == "source_correction_requires_review"
            assert aggregate["count"] is None

            # Exercise the authenticated Companion response against the same
            # PostgreSQL rows. This question is intentionally handled by the
            # deterministic source-quote path: corrected and pending records
            # must be excluded before any model inference is considered.
            token = f"companion-lineage-token-{suffix}"
            db.add_all([
                CaregiverAccessGrant(
                    person_id=person_id, caregiver_id=caregiver_id,
                    role="primary", access_scope="full_record", status="active",
                    can_manage_members=True, granted_by_caregiver_id=caregiver_id,
                ),
                AnonymousSession(
                    person_id=person_id, caregiver_id=caregiver_id,
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                ),
            ])
            db.commit()

        captured_prompts = []

        def fake_companion_inference(request_id, model, messages, options, retrieval_duration_ms, audit_context=None):
            captured_prompts.append(messages)
            return "The saved sleep reports include a correction and an unresolved report."

        monkeypatch.setattr(
            collaboration, "_run_companion_inference_with_metrics",
            fake_companion_inference,
        )
        monkeypatch.setattr(
            collaboration, "_ollama_url",
            lambda: ("http://ollama.test", "qwen-test"),
        )
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/conversation/message",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "person_id": person_id,
                    "text": "What has been recorded about sleep?",
                    "client_turn_id": str(uuid.uuid4()),
                },
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert corrected_event_id in body.get("source_event_ids", [])
        assert pending_event_id in body.get("source_event_ids", [])
        assert body["written_to_lcr"] is False
        assert body["ai_generated"] is False
        assert body["response_mode"] == "source_quoted_care_history"
        assert captured_prompts == [], "A source-answerable history question should not call Qwen."
        reply = body["reply"].casefold()
        assert "slept well" in reply
        assert "slept poorly" not in reply
        assert "slept through the night" not in reply
    finally:
        app.dependency_overrides.pop(get_db, None)
        with SessionLocal() as db:
            session_ids = [
                row[0] for row in db.query(ConversationSession.id).filter(
                    ConversationSession.person_id == person_id
                ).all()
            ]
            if session_ids:
                db.query(ConversationMessage).filter(
                    ConversationMessage.session_id.in_(session_ids)
                ).delete(synchronize_session=False)
                db.query(ConversationSession).filter(
                    ConversationSession.id.in_(session_ids)
                ).delete(synchronize_session=False)
            db.query(AnonymousSession).filter(
                AnonymousSession.person_id == person_id
            ).delete(synchronize_session=False)
            db.query(CaregiverAccessGrant).filter(
                CaregiverAccessGrant.person_id == person_id
            ).delete(synchronize_session=False)
            db.query(CareRecordRelationship).filter(
                CareRecordRelationship.person_id == person_id
            ).delete(synchronize_session=False)
            db.query(Correction).filter(
                Correction.person_id == person_id
            ).delete(synchronize_session=False)
            db.query(CareEvent).filter(
                CareEvent.person_id == person_id
            ).delete(synchronize_session=False)
            db.query(LearningEvent).filter(
                LearningEvent.person_id == person_id
            ).delete(synchronize_session=False)
            db.query(Caregiver).filter(
                Caregiver.person_id == person_id
            ).delete(synchronize_session=False)
            # Care-state revisions are retained by FK policy; remove this
            # isolated test fixture's revisions explicitly before its person.
            db.query(CareStateRevision).filter(
                CareStateRevision.person_id == person_id
            ).delete(synchronize_session=False)
            # The Daily Care delta engine may persist baseline lineage for
            # this isolated test person. Keep that parent row rather than
            # deleting it underneath durable baseline references; IDs are
            # unique per run and the PostgreSQL service is discarded after CI.
            db.commit()
        engine.dispose()

