"""PostgreSQL HTTP integration for Daily Care -> LCR -> Companion retrieval.

This test intentionally runs only against the migrated PostgreSQL service. It
must not silently fall back to SQLite or a mocked persistence layer.
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
from app.models.anonymous_session import AnonymousSession
from app.models.care import CareEvent, Caregiver, Evidence, Person
from app.models.capture_analysis import CaptureAnalysis
import app.api.daily_care_intelligence as daily_care_intelligence
import app.api.collaboration as collaboration
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
            own_document_analysis = CaptureAnalysis(
                person_id=person_id, caregiver_id=caregiver_id,
                source_text="Care document reports interrupted sleep overnight.",
                extraction_status="available",
                candidates={"kind": "document_extraction_v1", "filename": "own.pdf"},
                review_history=[],
            )
            peer_document_analysis = CaptureAnalysis(
                person_id=person_id, caregiver_id=same_person_peer_caregiver_id,
                source_text="PRIVATE_PEER_DOCUMENT: another caregiver's separately scoped upload.",
                extraction_status="available",
                candidates={"kind": "document_extraction_v1", "filename": "peer.pdf"},
                review_history=[],
            )
            db.add_all([own_document_analysis, peer_document_analysis])
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
            event_id = receipt["id"]
            evidence_id = receipt["evidence_ids"][0]

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
