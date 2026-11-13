"""Real Ollama/Qwen document intelligence through upload, extraction, review and LCR."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import get_db
from app.models.care_continuity import CareAssertion
from app.main import app
import app.main as main_module

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///:memory:")
POSTGRES_DATABASE = DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://", "postgresql+psycopg2://"))
pytestmark = pytest.mark.skipif(
    os.getenv("SOLENOS_RUN_LIVE_QWEN_EVAL") != "1" or not POSTGRES_DATABASE,
    reason="requires the dedicated live-Qwen workflow, running Ollama model and migrated PostgreSQL",
)
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_live_qwen_document_understanding_is_verbatim_review_gated_and_persisted(monkeypatch):
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)
    client = TestClient(app)
    source = (
        "[Page 1]\nDischarge summary. Patient walked 20 metres with a frame on 12 October.\n\n"
        "[Page 2]\nThe patient ate half of lunch during the observation period.\n\n"
        "[Page 3]\nThe caregiver reported that the patient slept for six uninterrupted hours on 14 October."
    )
    try:
        created = client.post(
            "/api/v1/sessions/anonymous",
            json={"caregiver_name": "Synthetic document intelligence evaluator"},
        )
        assert created.status_code == 201, created.text
        session = created.json()
        headers = {"Authorization": "Bearer " + session["access_token"]}

        uploaded = client.post(
            "/api/v1/upload",
            headers=headers,
            data={
                "person_id": session["person"]["id"],
                "caregiver_id": session["caregiver"]["id"],
                "evidence_type": "document",
                "extracted_text": "",
            },
            files={"file": ("discharge-summary.txt", source.encode("utf-8"), "text/plain")},
        )
        assert uploaded.status_code == 201, uploaded.text
        evidence_id = uploaded.json()["evidence_id"]
        assert uploaded.json()["persisted"] is True

        extraction = client.post(
            f"/api/v1/documents/{evidence_id}/extract",
            headers=headers,
        )
        assert extraction.status_code == 201, extraction.text
        analysis_id = extraction.json()["analysis_id"]
        assert extraction.json()["written_to_lcr"] is False

        understanding = client.post(
            f"/api/v1/documents/{evidence_id}/intelligence",
            headers=headers,
        )
        assert understanding.status_code == 201, understanding.text
        data = understanding.json()
        assert data["analysis_id"] == analysis_id
        assert data["review_required"] is True
        assert data["written_to_lcr"] is False
        assert data["intelligence_status"] == "available"
        assert data["processed_page_numbers"] == [1, 2, 3]
        assert data["unreadable_page_numbers"] == []
        candidates = data["observations"]
        assert len(candidates) >= 2, (
            "Live Qwen plus exact-source fallback did not preserve both explicit "
            f"care observations: {candidates!r}"
        )
        assert all(candidate["review_status"] == "pending_review" for candidate in candidates)
        assert all(candidate["source_quote"] in source for candidate in candidates)
        assert all(candidate["page_number"] in {1, 2, 3} for candidate in candidates)
        assert all(candidate["observation"] in candidate["source_quote"] for candidate in candidates)
        assert all(candidate["subject"].casefold() in candidate["source_quote"].casefold() for candidate in candidates)

        # A reload must return the same persisted extraction and pending review state.
        listing = client.get("/api/v1/documents/intelligence", headers=headers)
        assert listing.status_code == 200, listing.text
        assert listing.json()["count"] == 1
        reloaded = client.get(
            f"/api/v1/documents/analyses/{analysis_id}",
            headers=headers,
        )
        assert reloaded.status_code == 200, reloaded.text
        assert reloaded.json()["extracted_text"] == source
        assert reloaded.json()["candidate_count"] == len(candidates)
        assert reloaded.json()["written_to_lcr"] is False

        # Rejecting a candidate must not create a care assertion.
        rejected = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=headers,
            json={"candidate_index": 1, "decision": "reject"},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["written_to_lcr"] is False
        db = TestingSessionLocal()
        try:
            assert db.query(CareAssertion).filter_by(person_id=session["person"]["id"]).count() == 0
        finally:
            db.close()

        # Explicit acceptance creates one attributed, evidence-linked assertion.
        accepted = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=headers,
            json={"candidate_index": 0, "decision": "accept"},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["written_to_lcr"] is True
        db = TestingSessionLocal()
        try:
            assertion = db.query(CareAssertion).filter_by(person_id=session["person"]["id"]).one()
            assert assertion.assertion_status == "accepted"
            assert assertion.evidence_ids == [evidence_id]
            assert candidates[0]["source_quote"] in assertion.source_text
            assert f"page {candidates[0]['page_number']}" in assertion.source_label
        finally:
            db.close()

        # The accepted document assertion must be available to the same caregiver's
        # real-Qwen Companion response, with the original upload retained as source.
        companion = client.post(
            "/api/v1/conversation/message",
            headers=headers,
            json={
                "person_id": session["person"]["id"],
                "text": "What does the discharge summary say about walking?",
            },
        )
        assert companion.status_code == 200, companion.text
        grounded = companion.json()
        assert grounded.get("ai_generated") is True, grounded
        assert evidence_id in grounded.get("source_evidence_ids", []), grounded
        assert analysis_id in grounded.get("source_document_analysis_ids", []), grounded
        assert any(
            term in grounded.get("reply", "").casefold()
            for term in ("20 metres", "20 meter", "frame", "walked 20")
        ), grounded
        assert grounded.get("written_to_lcr") is False, grounded

        # A separate care record cannot list, retrieve, or review the first record's analysis.
        other = client.post(
            "/api/v1/sessions/anonymous",
            json={"caregiver_name": "Different synthetic caregiver"},
        )
        assert other.status_code == 201, other.text
        other_headers = {"Authorization": "Bearer " + other.json()["access_token"]}
        assert client.get(f"/api/v1/documents/analyses/{analysis_id}", headers=other_headers).status_code == 404
        assert client.post(
            f"/api/v1/documents/{evidence_id}/intelligence",
            headers=other_headers,
        ).status_code == 404
    finally:
        client.close()
        app.dependency_overrides.clear()
        engine.dispose()
