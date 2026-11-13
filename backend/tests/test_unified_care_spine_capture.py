"""PostgreSQL HTTP integration for Daily Care -> LCR -> Companion retrieval.

This test intentionally runs only against the migrated PostgreSQL service. It
must not silently fall back to SQLite or a mocked persistence layer.
"""
import asyncio
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
from app.models.anonymous_session import AnonymousSession
from app.models.care import CareEvent, Caregiver, Evidence, Person
from app.models.capture_analysis import CaptureAnalysis
from app.models.collaboration import EvidenceUpload
from app.models.qwen_background_job import QwenBackgroundJob
import app.api.daily_care_intelligence as daily_care_intelligence
import app.api.collaboration as collaboration
import app.services.qwen_background_worker as qwen_background_worker
import app.services.care_continuity_execution as care_continuity_execution
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

        with TestClient(app) as client:
            response = client.post(
                "/api/v1/daily/log",
                headers=headers,
                json={
                    "person_id": person_id,
                    "caregiver_id": caregiver_id,
                    "capture_idempotency_key": "daily-capture-" + suffix,
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
            def fake_capture_extraction(source_text, *, source_event_id, source_evidence_id):
                assert "Mum was unusually confused this afternoon and refused lunch." in source_text
                return {
                    "observations": [{
                        "source_quote": "Mum was unusually confused this afternoon and refused lunch.",
                        "observation": "Caregiver reported confusion and refused lunch.",
                        "review_status": "pending_review",
                    }],
                    "open_questions": [],
                }

            monkeypatch.setattr(
                qwen_background_worker, "extract_review_candidates", fake_capture_extraction
            )
            monkeypatch.setattr(
                care_continuity_execution,
                "execute_care_continuity_reconciliation",
                lambda *args, **kwargs: {"run_id": "ci-reconciliation"},
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
                assert completed_analysis.candidates["observations"][0]["review_status"] == "pending_review"
                assert completed_analysis.execution_status["review"] == "required"
                assert completed_analysis.execution_status["projection"] == "verified"
                assert completed_analysis.execution_status["longitudinal_retrieval"] == "verified"
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
            assert analysis_response.json()["candidates"]["observations"][0]["review_status"] == "pending_review"
            foreign_analysis_response = client.get(
                f"/api/v1/capture/analyses/{analysis_id}",
                headers={"Authorization": "Bearer " + other_token},
            )
            assert foreign_analysis_response.status_code == 404

            retry = client.post(
                "/api/v1/daily/log",
                headers=headers,
                json={
                    "person_id": person_id,
                    "caregiver_id": caregiver_id,
                    "capture_idempotency_key": "daily-capture-" + suffix,
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
                assert evidence.reported_by_caregiver_id == caregiver_id
                assert evidence.epistemic_status == "caregiver_reported"

                # Companion uses this shared retrieval composition. It must
                # retrieve the same committed source IDs from a fresh session.
                context = build_unified_lcr_context(
                    db, person_id, caregiver_id, limit_per_source=50
                )

            event_rows = [
                row for row in context["sources"]["events"]["items"]
                if row["id"] == event_id
            ]
            evidence_rows = [
                row for row in context["sources"]["evidence"]["items"]
                if row["id"] == evidence_id
            ]
            assert len(event_rows) == 1
            assert len(evidence_rows) == 1
            assert event_rows[0]["evidence_ids"] == [evidence_id]
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
            assert len(lcr_matches) == 1
            assert lcr_matches[0]["evidence_ids"] == [evidence_id]

            # Exercise the actual Companion HTTP path, not only the retrieval
            # helper. The saved Daily Care event and its linked evidence must be
            # supplied to the model and cited in the response metadata.
            captured_prompts = []

            def fake_companion_inference(request_id, model, messages, options, retrieval_duration_ms):
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
                    "text": "What was recorded about confusion and lunch?",
                    "client_turn_id": str(uuid.uuid4()),
                },
            )
            assert companion_daily.status_code == 200, companion_daily.text
            companion_daily_body = companion_daily.json()
            assert event_id in companion_daily_body.get("source_event_ids", [])
            assert evidence_id in companion_daily_body.get("source_evidence_ids", [])
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
            assert old_history_event_id in historical_prompt
            assert "repeated waking overnight" in historical_prompt
            # Internal database IDs belong in response provenance metadata, not
            # caregiver-facing model prose. Verify the exact saved report content
            # and epistemic boundary are present in the prompt instead.
            assert "Mum was unusually confused this afternoon and refused lunch." in daily_prompt
            assert "caregiver_reported" in daily_prompt
            assert event_id in companion_daily_body.get("source_event_ids", [])
            assert evidence_id in companion_daily_body.get("source_evidence_ids", [])

            # The Companion document question must include this caregiver's
            # persisted document extraction and exclude a peer caregiver's
            # private document from the same person's account.
            companion_document = client.post(
                "/api/v1/conversation/message",
                headers=headers,
                json={
                    "person_id": person_id,
                    "text": "What does the uploaded care document say about sleep?",
                    "client_turn_id": "document-companion-" + suffix,
                },
            )
            assert companion_document.status_code == 200, companion_document.text
            companion_document_body = companion_document.json()
            assert own_document_analysis_id in companion_document_body.get(
                "source_document_analysis_ids", []
            )
            assert peer_document_analysis_id not in companion_document_body.get(
                "source_document_analysis_ids", []
            )
            document_prompt = "\\n".join(
                str(message.get("content", "")) for message in captured_prompts[-1]
            )
            assert own_document_analysis_id in document_prompt
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
