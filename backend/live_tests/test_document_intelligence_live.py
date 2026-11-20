"""Real Ollama/Qwen document intelligence through upload, extraction, review and LCR."""
from __future__ import annotations

import json
import os
from pathlib import Path

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
    fixture_path = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "document_intelligence_semantic_eval.json"
    semantic_dataset = json.loads(fixture_path.read_text(encoding="utf-8"))
    source = (
        "[Page 1]\nDischarge summary. Patient walked 20 metres with a frame on 12 October.\n\n"
        "[Page 2]\nThe patient ate half of lunch during the observation period.\n\n"
        "[Page 3]\nThe caregiver reported that the patient slept for five uninterrupted hours on 13 October."
    )
    for page_number, case in enumerate(semantic_dataset["cases"], start=4):
        source += f"\n\n[Page {page_number}]\n{case['text']}"
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
        assert data["processed_page_numbers"] == list(range(1, 4 + len(semantic_dataset["cases"])))
        assert data["unreadable_page_numbers"] == []
        candidates = data["observations"]
        assert any("walked 20 metres" in candidate["source_quote"] for candidate in candidates), candidates
        assert any("ate half of lunch" in candidate["source_quote"] for candidate in candidates), candidates

        assert all(candidate["review_status"] == "pending_review" for candidate in candidates)
        assert all(candidate["source_quote"] in source for candidate in candidates)
        assert all(candidate["page_number"] in set(range(1, 4 + len(semantic_dataset["cases"]))) for candidate in candidates)
        assert all(candidate["observation"] in candidate["source_quote"] for candidate in candidates)
        assert all(candidate["subject"].casefold() in candidate["source_quote"].casefold() for candidate in candidates)

        # Each manually labelled synthetic case must survive actual Qwen inference,
        # exact-quote validation and deterministic temporal/negation normalization.
        matched_gold = []
        for case in semantic_dataset["cases"]:
            matched = next(
                (candidate for candidate in candidates if case["text"] in candidate["source_quote"]),
                None,
            )
            assert matched is not None, f"Live Qwen omitted gold case {case['id']}: {candidates!r}"
            canonical = matched["canonical_candidate"]
            matched_gold.append((case, canonical))
            for field, expected in case["expected"].items():
                assert canonical[field] == expected, (
                    f"Live Qwen case {case['id']} lost {field}: "
                    f"expected {expected!r}, got {canonical[field]!r}"
                )

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
            assertion_id = str(assertion.id)
            assert assertion.evidence_ids == [evidence_id]
            assert candidates[0]["source_quote"] in assertion.source_text
            assert f"page {candidates[0]['page_number']}" in assertion.source_label
        finally:
            db.close()

        # Historical and tentative document statements must also enter the
        # canonical LCR only after explicit caregiver approval.
        historical_text = next(
            case["text"] for case in semantic_dataset["cases"]
            if case["id"] == "previous-diagnosis"
        )
        historical_index = next(
            index for index, candidate in enumerate(candidates)
            if historical_text in candidate["source_quote"]
        )
        historical_review = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=headers,
            json={"candidate_index": historical_index, "decision": "accept"},
        )
        assert historical_review.status_code == 200, historical_review.text
        assert historical_review.json()["written_to_lcr"] is True
        historical_assertion_id = historical_review.json()["source_record_ref"]["record_id"]

        tentative_text = next(
            case["text"] for case in semantic_dataset["cases"]
            if case["id"] == "possible-condition"
        )
        tentative_index = next(
            index for index, candidate in enumerate(candidates)
            if tentative_text in candidate["source_quote"]
        )
        tentative_review = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=headers,
            json={"candidate_index": tentative_index, "decision": "accept"},
        )
        assert tentative_review.status_code == 200, tentative_review.text
        assert tentative_review.json()["written_to_lcr"] is True
        tentative_assertion_id = tentative_review.json()["source_record_ref"]["record_id"]

        db = TestingSessionLocal()
        try:
            historical_assertion = db.query(CareAssertion).filter_by(
                id=historical_assertion_id,
                person_id=session["person"]["id"],
            ).one()
            assert historical_assertion.source_context["document_candidate"]["temporal_context"] == "historical"
            tentative_assertion = db.query(CareAssertion).filter_by(
                id=tentative_assertion_id,
                person_id=session["person"]["id"],
            ).one()
            assert tentative_assertion.source_context["document_candidate"]["modality"] == "tentative"
            accepted_assertion_count = db.query(CareAssertion).filter_by(
                person_id=session["person"]["id"],
            ).count()
            assert accepted_assertion_count == 3
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
        assert grounded.get("ai_generated") in {True, False}, grounded
        assert evidence_id in grounded.get("source_evidence_ids", []), grounded
        assert analysis_id in grounded.get("source_document_analysis_ids", []), grounded
        assert assertion_id in grounded.get("source_assertion_ids", []), grounded
        if grounded["ai_generated"] is False:
            # A verified verbatim excerpt is an acceptable safe fallback when
            # Qwen fails to use the extracted source; it must disclose its origin.
            assert grounded.get("reply") == (
                "The uploaded document says: "
                "“Patient walked 20 metres with a frame on 12 October.”"
            ), grounded
            assert "Quoted from the uploaded document" in (grounded.get("safety_note") or "")
        else:
            assert any(
                term in grounded.get("reply", "").casefold()
                for term in ("20 metres", "20 meter", "frame", "walked 20")
            ), grounded
        assert grounded.get("written_to_lcr") is False, grounded

        historical_companion = client.post(
            "/api/v1/conversation/message",
            headers=headers,
            json={
                "person_id": session["person"]["id"],
                "text": "What does the discharge document say about the person's previous Parkinson's diagnosis? Is it described as current?",
            },
        )
        assert historical_companion.status_code == 200, historical_companion.text
        historical_payload = historical_companion.json()
        assert evidence_id in historical_payload.get("source_evidence_ids", [])
        assert analysis_id in historical_payload.get("source_document_analysis_ids", [])
        assert historical_assertion_id in historical_payload.get("source_assertion_ids", [])
        historical_reply = historical_payload.get("reply", "").casefold()
        assert any(term in historical_reply for term in ("previous", "history", "historical")), historical_payload

        tentative_companion = client.post(
            "/api/v1/conversation/message",
            headers=headers,
            json={
                "person_id": session["person"]["id"],
                "text": "What does the discharge document say about pneumonia? Is the condition confirmed or only suspected?",
            },
        )
        assert tentative_companion.status_code == 200, tentative_companion.text
        tentative_payload = tentative_companion.json()
        assert evidence_id in tentative_payload.get("source_evidence_ids", [])
        assert analysis_id in tentative_payload.get("source_document_analysis_ids", [])
        assert tentative_assertion_id in tentative_payload.get("source_assertion_ids", [])
        tentative_reply = tentative_payload.get("reply", "").casefold()
        assert any(term in tentative_reply for term in ("possible", "suspected", "suspects", "not confirmed")), tentative_payload

        total_gold = len(semantic_dataset["cases"])
        temporal_correct = sum(
            canonical["temporal_context"] == case["expected"]["temporal_context"]
            for case, canonical in matched_gold
        )
        negation_correct = sum(
            canonical["negation_status"] == case["expected"]["negation_status"]
            for case, canonical in matched_gold
        )
        attribution_correct = sum(
            canonical["source_attribution"] == case["expected"]["source_attribution"]
            for case, canonical in matched_gold
        )
        print(
            "DOCUMENT_INTELLIGENCE_LIVE_EVALUATION="
            + json.dumps(
                {
                    "dataset_scope": semantic_dataset["scope"],
                    "gold_cases_total": total_gold,
                    "gold_cases_extracted": len(matched_gold),
                    "gold_case_recall": round(len(matched_gold) / total_gold, 4),
                    "temporal_classification_accuracy": round(temporal_correct / total_gold, 4),
                    "negation_preservation_accuracy": round(negation_correct / total_gold, 4),
                    "source_attribution_accuracy": round(attribution_correct / total_gold, 4),
                    "source_quote_accuracy": 1.0,
                    "unsupported_candidate_rate": 0.0,
                    "companion_lcr_assertion_retrieval": True,
                    "accepted_assertion_count": accepted_assertion_count,
                    "duplicate_assertion_count": 0,
                },
                sort_keys=True,
            )
        )

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

        # Companion retrieval must derive its sources from the authenticated
        # person's canonical LCR, never from another person's accepted document.
        foreign_companion = client.post(
            "/api/v1/conversation/message",
            headers=other_headers,
            json={
                "person_id": other.json()["person"]["id"],
                "text": "What did the discharge summary say about walking?",
            },
        )
        assert foreign_companion.status_code == 200, foreign_companion.text
        foreign_payload = foreign_companion.json()
        assert evidence_id not in foreign_payload.get("source_evidence_ids", [])
        assert analysis_id not in foreign_payload.get("source_document_analysis_ids", [])
        assert assertion_id not in foreign_payload.get("source_assertion_ids", [])
    finally:
        client.close()
        app.dependency_overrides.clear()
        engine.dispose()
