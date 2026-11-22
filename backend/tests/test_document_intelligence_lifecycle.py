"""Document intelligence review gates, persistence, and ownership isolation."""
import hashlib
import importlib
import json

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.care import Evidence
from app.models.care_continuity import CareAssertion, CareDeltaRun
from app.models.collaboration import EvidenceUpload
from app.models.capture_analysis import CaptureAnalysis, DocumentReviewEvent

main_module = importlib.import_module("app.main")
intelligence_module = importlib.import_module("app.api.document_intelligence")
execution_module = importlib.import_module("app.services.care_continuity_execution")
lifecycle_module = importlib.import_module("app.api.document_lifecycle")
app = main_module.app
engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_db():
    db = Session()
    try:
        yield db
    finally:
        db.close()


def create_record(client, name):
    response = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": name})
    assert response.status_code == 201, response.text
    return response.json()


def save_original(session_data, source_text):
    data = source_text.encode("utf-8")
    evidence_id = "document-" + hashlib.sha256(data + session_data["person"]["id"].encode()).hexdigest()[:24]
    db = Session()
    try:
        evidence = Evidence(
            id=evidence_id,
            person_id=session_data["person"]["id"],
            type="document",
            source_text="",
            uploaded_by_caregiver_id=session_data["caregiver"]["id"],
            extra_metadata=json.dumps({"filename": "discharge-summary.txt"}),
            source_type="caregiver_upload",
            evidence_status="recorded",
        )
        db.add(evidence)
        db.flush()
        db.add(EvidenceUpload(
            evidence_id=evidence_id,
            filename="discharge-summary.txt",
            mime_type="text/plain",
            content=data,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        ))
        db.commit()
    finally:
        db.close()
    return evidence_id


def test_duplicate_copy_warning_does_not_change_document_assertion_meaning():
    original = {
        "field_type": "observation",
        "warnings": [],
        "canonical_candidate": {"warnings": []},
    }
    duplicate = {
        "field_type": "observation",
        "warnings": [intelligence_module.DUPLICATE_COPY_WARNING],
        "canonical_candidate": {
            "warnings": [intelligence_module.DUPLICATE_COPY_WARNING],
        },
    }
    original_value = intelligence_module._document_asserted_value(
        original, "mobility", "The patient walked with a frame."
    )
    duplicate_value = intelligence_module._document_asserted_value(
        duplicate, "mobility", "The patient walked with a frame."
    )
    assert duplicate_value == original_value



def test_exact_duplicate_documents_reuse_one_accepted_lcr_assertion(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", Session)
    client = TestClient(app)
    source = "[Page 1]\\nDischarge summary: Patient walked 20 metres with a frame."
    quote = "Patient walked 20 metres with a frame."
    conflicting_evidence_id = None

    def deterministic_candidates(text, *, source_event_id, source_evidence_id=None):
        assert quote in text
        return {
            "observations": [{
                "subject": "Patient",
                "observation": "Patient walked 20 metres" if source_evidence_id == conflicting_evidence_id else quote,
                "source_quote": quote,
                "extraction_method": "test_verbatim",
            }],
            "open_questions": [],
        }

    monkeypatch.setattr(intelligence_module, "extract_review_candidates", deterministic_candidates)
    try:
        caregiver = create_record(client, "Duplicate document review caregiver")
        auth = {"Authorization": "Bearer " + caregiver["access_token"]}
        first_evidence_id = save_original(caregiver, source)
        binary = source.encode("utf-8")
        digest = hashlib.sha256(binary).hexdigest()
        second_evidence_id = "document-copy-" + hashlib.sha256(
            binary + caregiver["person"]["id"].encode("utf-8") + b"second-copy"
        ).hexdigest()[:24]
        conflicting_evidence_id = "document-copy-" + hashlib.sha256(
            binary + caregiver["person"]["id"].encode("utf-8") + b"conflicting-copy"
        ).hexdigest()[:24]

        # Persist two additional evidence identities with identical original bytes.
        # The third analysis deliberately returns a different field classification.
        with Session() as db:
            for evidence_id, filename in (
                (second_evidence_id, "duplicate-discharge-summary.txt"),
                (conflicting_evidence_id, "conflicting-discharge-summary.txt"),
            ):
                db.add(Evidence(
                    id=evidence_id,
                    person_id=caregiver["person"]["id"],
                    type="document",
                    source_text="",
                    uploaded_by_caregiver_id=caregiver["caregiver"]["id"],
                    extra_metadata=json.dumps({
                        "filename": filename,
                        "duplicate_of_evidence_id": first_evidence_id,
                    }),
                    source_type="caregiver_upload",
                    evidence_status="recorded",
                ))
                db.add(EvidenceUpload(
                    evidence_id=evidence_id,
                    filename=filename,
                    mime_type="text/plain",
                    content=binary,
                    size_bytes=len(binary),
                    sha256=digest,
                ))
            db.commit()

        analysis_ids = []
        for evidence_id in (first_evidence_id, second_evidence_id, conflicting_evidence_id):
            extracted = client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
            assert extracted.status_code == 201, extracted.text
            understood = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=auth)
            assert understood.status_code == 201, understood.text
            assert understood.json()["written_to_lcr"] is False
            assert len(understood.json()["observations"]) == 1
            analysis_ids.append(understood.json()["analysis_id"])

        before_review_situation = client.get("/api/situation", headers=auth)
        assert before_review_situation.status_code == 200, before_review_situation.text
        assert before_review_situation.json()["document_assertions"] == []
        assert not any(
            item.get("document_assertion_id")
            for item in before_review_situation.json()["active_care_situation"]["observations"]
        )

        first_review = client.post(
            f"/api/v1/documents/intelligence/{analysis_ids[0]}/review",
            headers=auth,
            json={"candidate_index": 0, "decision": "accept"},
        )
        assert first_review.status_code == 200, first_review.text
        first_assertion_id = first_review.json()["source_record_ref"]["record_id"]
        assert first_review.json()["written_to_lcr"] is True

        duplicate_review = client.post(
            f"/api/v1/documents/intelligence/{analysis_ids[1]}/review",
            headers=auth,
            json={"candidate_index": 0, "decision": "accept"},
        )
        assert duplicate_review.status_code == 200, duplicate_review.text
        assert duplicate_review.json()["source_record_ref"]["record_id"] == first_assertion_id
        assert duplicate_review.json()["written_to_lcr"] is True

        # A model classification change on the same exact binary/quotation is
        # not a second source. It must be blocked rather than creating a second field.
        conflicting_review = client.post(
            f"/api/v1/documents/intelligence/{analysis_ids[2]}/review",
            headers=auth,
            json={"candidate_index": 0, "decision": "accept"},
        )
        assert conflicting_review.status_code == 409, conflicting_review.text

        with Session() as db:
            assertions = db.query(CareAssertion).filter_by(
                person_id=caregiver["person"]["id"],
                id=first_assertion_id,
            ).all()
            assert len(assertions) == 1
            assert assertions[0].source_text == quote
            assert assertions[0].evidence_ids == [first_evidence_id]
            events = db.query(DocumentReviewEvent).filter(
                DocumentReviewEvent.person_id == caregiver["person"]["id"],
                DocumentReviewEvent.decision == "accept",
            ).all()
            assert len(events) == 2
            assert {event.source_evidence_id for event in events} == {
                first_evidence_id, second_evidence_id
            }
            assert {event.resulting_assertion_id for event in events} == {first_assertion_id}

        # The existing Progressive Understanding situation endpoint now receives
        # accepted document assertions as typed source projections, not as new facts.
        situation = client.get("/api/situation", headers=auth)
        assert situation.status_code == 200, situation.text
        situation_payload = situation.json()
        projected_assertions = situation_payload["document_assertions"]
        assert len(projected_assertions) == 1
        projected = projected_assertions[0]
        assert projected["assertion_id"] == first_assertion_id
        assert projected["source_evidence_id"] == first_evidence_id
        assert projected["source_quote"] == quote
        assert projected["source_page"] == 1
        assert projected["review_status"] == "accepted_by_caregiver"
        assert projected["reviewer_caregiver_id"] == caregiver["caregiver"]["id"]
        assert projected["source_type"] == "document_intelligence"
        assert projected["temporal_context"] == "not_established"
        projected_observation = next(
            item for item in situation_payload["active_care_situation"]["observations"]
            if item["id"] == first_evidence_id
        )
        assert projected_observation["document_assertions"][0]["assertion_id"] == first_assertion_id
        progressive_observation = next(
            item for item in situation_payload["active_care_situation"]["observations"]
            if item.get("document_assertion_id") == first_assertion_id
        )
        assert progressive_observation["kind"] == "document"
        assert progressive_observation["raw_text"] == quote
        assert progressive_observation["human_fact"] == quote
        assert progressive_observation["epistemic_kind"] == "mixed"
        assert progressive_observation["speaker_label"] is None
        assert progressive_observation["speaker_attribution_status"] == "unknown"
        assert progressive_observation["source_reference_id"] == first_evidence_id
        assert progressive_observation["temporal_context"] == "not_established"
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)


def test_document_understanding_review_accept_reject_reload_and_cross_person(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", Session)
    client = TestClient(app)
    source = (
        "The discharge summary states: Patient walked 20 metres with a frame on 12 October. "
        "The patient ate half of lunch during the observation period."
    )
    quotes = [
        "Patient walked 20 metres with a frame on 12 October.",
        "The patient ate half of lunch during the observation period.",
    ]

    def fake_extract(text, *, source_event_id, source_evidence_id=None):
        assert text == source
        return {"observations": [
            {
                "subject": "Patient",
                "observation": quotes[0],
                "source_quote": quotes[0],
                "extraction_method": "test_verbatim",
            },
            {
                "subject": "The patient",
                "observation": "The patient ate half of lunch",
                "source_quote": quotes[1],
                "extraction_method": "test_verbatim",
            },
            {"subject": "Patient", "observation": "Invented diagnosis", "source_quote": "Patient has a confirmed diagnosis that is not in this document."},
            {"subject": "Patient", "observation": "Patient recovered completely", "source_quote": "Patient walked 20 metres with a frame on 12 October."},
        ], "open_questions": ["Was this typical for the person?"]}

    monkeypatch.setattr(intelligence_module, "extract_review_candidates", fake_extract)
    execution_calls = []
    original_execute = execution_module.execute_care_continuity_reconciliation

    def capture_execution_scope(db, person_id, **kwargs):
        execution_calls.append(dict(kwargs))
        return original_execute(db, person_id, **kwargs)

    monkeypatch.setattr(
        intelligence_module,
        "execute_care_continuity_reconciliation",
        capture_execution_scope,
    )
    original_delta = intelligence_module.reconcile_document_delta
    delta_attempts = 0
    delta_input_content = []

    def fail_first_delta(*args, **kwargs):
        nonlocal delta_attempts
        delta_attempts += 1
        delta_input_content.append(kwargs.get("content"))
        if delta_attempts == 1:
            raise RuntimeError("temporary Delta persistence failure")
        return original_delta(*args, **kwargs)

    monkeypatch.setattr(intelligence_module, "reconcile_document_delta", fail_first_delta)
    try:
        first = create_record(client, "Caregiver A")
        auth = {"Authorization": "Bearer " + first["access_token"]}
        evidence_id = save_original(first, source)

        extracted = client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
        assert extracted.status_code == 201, extracted.text
        analysis_id = extracted.json()["analysis_id"]

        understood = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=auth)
        assert understood.status_code == 201, understood.text
        assert understood.json()["review_required"] is True
        assert understood.json()["written_to_lcr"] is False
        assert all(item["source_quote"] in source for item in understood.json()["observations"])

        # Re-running understanding is idempotent and does not create a second analysis.
        repeated = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=auth)
        assert repeated.status_code == 201
        assert repeated.json()["analysis_id"] == analysis_id
        assert len(repeated.json()["observations"]) == 2
        assert all(item["source_quote"] in source for item in repeated.json()["observations"])

        # Reload both the analysis and the extracted text through authenticated reads.
        details = client.get(f"/api/v1/documents/analyses/{analysis_id}", headers=auth)
        assert details.status_code == 200, details.text
        assert details.json()["extracted_text"] == source
        library = client.get("/api/v1/documents/intelligence", headers=auth)
        assert library.status_code == 200
        assert len(library.json()["items"]) == 1

        # Once extraction is persisted with source hashes, reload/review must not rerun OCR.
        def unexpected_ocr(*args, **kwargs):
            raise AssertionError("Persisted extraction reads must not rerun OCR")
        monkeypatch.setattr(lifecycle_module, "_extract", unexpected_ocr)

        rejected = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={"candidate_index": 0, "decision": "reject"},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["written_to_lcr"] is False

        qualifier_dropping_correction = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={
                "candidate_index": 0,
                "decision": "correct",
                "corrected_subject": "Metformin",
                "corrected_observation": "Metformin 1000 mg twice daily.",
            },
        )
        assert qualifier_dropping_correction.status_code == 409, qualifier_dropping_correction.text

        assert "meal intake" not in quotes[1].casefold()
        corrected = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={
                "candidate_index": 1,
                "decision": "correct",
                "corrected_subject": "Meal intake",
                "corrected_observation": quotes[1],
            },
        )
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["written_to_lcr"] is False
        corrected_again = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={
                "candidate_index": 1,
                "decision": "correct",
                "corrected_subject": "Meal intake",
                "corrected_observation": quotes[1],
            },
        )
        assert corrected_again.status_code == 200, corrected_again.text
        assert corrected_again.json()["idempotent_replay"] is True
        db = Session()
        try:
            assert db.query(CareAssertion).filter_by(person_id=first["person"]["id"]).count() == 0
        finally:
            db.close()

        accepted = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={"candidate_index": 1, "decision": "accept"},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["written_to_lcr"] is True
        assert accepted.json()["longitudinal_retrieval_status"] == "verified"
        assert accepted.json()["candidate"]["longitudinal_retrieval_status"] == "verified"
        assert accepted.json()["delta_reconciliation_status"] == "failed"
        assert accepted.json()["delta_run_id"] is None

        # A later correction request cannot rewrite an already approved assertion.
        attempted_overwrite = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={
                "candidate_index": 1,
                "decision": "correct",
                "corrected_subject": "Meal intake",
                "corrected_observation": quotes[1],
            },
        )
        assert attempted_overwrite.status_code == 409, attempted_overwrite.text
        with Session() as db:
            approved = db.query(CareAssertion).filter_by(
                id=accepted.json()["source_record_ref"]["record_id"],
                person_id=first["person"]["id"],
            ).one()
            assert approved.source_text == quotes[1]
            assert db.query(CareAssertion).filter_by(person_id=first["person"]["id"]).count() == 1

        assert len(execution_calls) == 1
        assert execution_calls[0]["source_caregiver_id"] == first["caregiver"]["id"]
        assert execution_calls[0]["source_evidence_id"] == evidence_id
        assert execution_calls[0]["source_assertion_id"] == accepted.json()["source_record_ref"]["record_id"]

        # Repeating the same acceptance returns the existing assertion; it never duplicates it.
        accepted_again = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={"candidate_index": 1, "decision": "accept"},
        )
        assert accepted_again.status_code == 200
        assert accepted_again.json()["source_record_ref"] == accepted.json()["source_record_ref"]
        assert accepted_again.json()["longitudinal_retrieval_status"] == "verified"
        assert accepted_again.json()["delta_reconciliation_status"] == "completed"
        assert accepted_again.json()["idempotent_replay"] is True
        delta_run_id = accepted_again.json()["delta_run_id"]
        assert delta_run_id
        assert delta_attempts == 2
        assert delta_input_content == [quotes[1], quotes[1]], (
            "The Delta Engine must receive only the reviewed candidate quotation, "
            "never the full document containing unreviewed observations."
        )

        third_replay = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={"candidate_index": 1, "decision": "accept"},
        )
        assert third_replay.status_code == 200, third_replay.text
        assert third_replay.json()["delta_run_id"] == delta_run_id
        assert delta_attempts == 2

        db = Session()
        try:
            assert db.query(CareDeltaRun).filter_by(person_id=first["person"]["id"]).count() == 1
            persisted_delta = db.query(CareDeltaRun).filter_by(
                id=delta_run_id, person_id=first["person"]["id"]
            ).one()
            persisted_delta_json = json.loads(persisted_delta.delta_json)
            assert persisted_delta.source == "SolenOS Document Intelligence"
            assert persisted_delta.evidence_ids == [evidence_id]
            assert persisted_delta_json["provenance"]["evidence_ids"] == [evidence_id]
        finally:
            db.close()

        history_response = client.get(
            f"/api/v1/documents/intelligence/{analysis_id}/review-history",
            headers=auth,
        )
        assert history_response.status_code == 200, history_response.text
        history = history_response.json()
        assert history["count"] == 3
        assert [item["decision"] for item in history["items"]] == ["reject", "correct", "accept"]
        assert history["history_authority"] == "append_only_document_review_events"

        db = Session()
        try:
            event_row = db.query(DocumentReviewEvent).filter_by(
                analysis_id=analysis_id, candidate_index=1, decision_version=1
            ).one()
            try:
                event_row.rationale = "attempted history rewrite"
                db.commit()
                raise AssertionError("Document review history mutation unexpectedly succeeded")
            except ValueError as exc:
                assert "append-only" in str(exc)
                db.rollback()
        finally:
            db.close()

        db = Session()
        try:
            # Reload the assertion after the SQL Core review transition.
            db.expire_all()
            assertions = db.query(CareAssertion).filter_by(person_id=first["person"]["id"]).all()
            assert len(assertions) == 1
            assertion = assertions[0]
            assert assertion.assertion_status == "accepted"
            assert assertion.assertion_kind == "document_reviewed_observation"
            assert assertion.field == "observation"
            assert assertion.evidence_ids == [evidence_id]
            assert assertion.source_text == quotes[1]
            assert "page 1" in assertion.source_label
            assert assertion.created_by_caregiver_id == first["caregiver"]["id"]
        finally:
            db.close()
        lcr_assertions = client.get(
            f"/api/v1/care-continuity/assertions/{first['person']['id']}", headers=auth
        )
        assert lcr_assertions.status_code == 200, lcr_assertions.text
        assert any(
            item["id"] == accepted.json()["source_record_ref"]["record_id"]
            and item["assertion_status"] == "accepted"
            for item in lcr_assertions.json()
        )

        # A tampered persisted extraction must not be served after candidate generation.
        db = Session()
        try:
            row = db.query(CaptureAnalysis).filter_by(id=analysis_id).one()
            row.source_text = row.source_text + " Tampered after analysis."
            db.commit()
        finally:
            db.close()
        tampered = client.get(f"/api/v1/documents/analyses/{analysis_id}", headers=auth)
        assert tampered.status_code == 409

        second = create_record(client, "Caregiver B")
        foreign = {"Authorization": "Bearer " + second["access_token"]}
        denied_analysis = client.get(f"/api/v1/documents/analyses/{analysis_id}", headers=foreign)
        assert denied_analysis.status_code == 404
        denied_review = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=foreign,
            json={"candidate_index": 0, "decision": "accept"},
        )
        assert denied_review.status_code == 404
        denied_history = client.get(
            f"/api/v1/documents/intelligence/{analysis_id}/review-history",
            headers=foreign,
        )
        assert denied_history.status_code == 404
        denied_source = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=foreign)
        assert denied_source.status_code == 404
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)


def test_lcr_assertion_and_review_event_roll_back_together(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", Session)
    client = TestClient(app)
    source = "Discharge note: Patient walked 20 metres with a frame."
    quote = "Patient walked 20 metres with a frame."

    def fake_extract(text_value, *, source_event_id, source_evidence_id=None):
        return {"observations": [{
            "subject": "Patient",
            "observation": "Patient walked 20 metres with a frame.",
            "source_quote": quote,
            "extraction_method": "test_verbatim",
        }], "open_questions": []}

    monkeypatch.setattr(intelligence_module, "extract_review_candidates", fake_extract)
    try:
        caregiver = create_record(client, "Atomic review caregiver")
        auth = {"Authorization": "Bearer " + caregiver["access_token"]}
        evidence_id = save_original(caregiver, source)
        extracted = client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
        assert extracted.status_code == 201, extracted.text
        analysis_id = extracted.json()["analysis_id"]
        understood = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=auth)
        assert understood.status_code == 201, understood.text

        with Session() as db:
            db.execute(text("""
                CREATE TRIGGER fail_document_review_event_insert
                BEFORE INSERT ON document_review_events
                BEGIN
                    SELECT RAISE(ABORT, 'forced review event insert failure');
                END
            """))
            db.commit()

        failed_review = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={"candidate_index": 0, "decision": "accept"},
        )
        assert failed_review.status_code == 503, failed_review.text
        assert "review was not committed" in failed_review.json()["detail"]

        with Session() as db:
            assert db.query(CareAssertion).filter_by(person_id=caregiver["person"]["id"]).count() == 0
            assert db.query(DocumentReviewEvent).filter_by(analysis_id=analysis_id).count() == 0
            persisted = db.query(CaptureAnalysis).filter_by(id=analysis_id).one()
            observation = persisted.candidates["document_intelligence"]["observations"][0]
            assert observation["review_status"] == "pending_review"
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)


def test_incomplete_document_blocks_interpretation_and_candidate_acceptance(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", Session)
    client = TestClient(app)
    source = (
        "[Page 1]\nDischarge summary\nPatient walked with assistance.\n\n"
        "[Page 2 OCR]\n[No readable text detected on this page]\n\n"
        "[Page 3]\nMedication reconciliation\nMedication instruction recorded."
    )
    calls = []

    def fake_extract(page_text, *, source_event_id, source_evidence_id=None):
        calls.append(page_text)
        return {"observations": [], "open_questions": []}

    monkeypatch.setattr(intelligence_module, "extract_review_candidates", fake_extract)
    try:
        caregiver = create_record(client, "Incomplete document caregiver")
        auth = {"Authorization": "Bearer " + caregiver["access_token"]}
        evidence_id = save_original(caregiver, source)
        extracted = client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
        assert extracted.status_code == 201, extracted.text
        extraction_body = extracted.json()
        assert extraction_body["extraction_status"] == "partial"
        assert extraction_body["unreadable_page_numbers"] == [2]

        understood = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=auth)
        assert understood.status_code == 409, understood.text
        assert "blocked until every page has a complete extraction" in understood.json()["detail"]
        assert "unreadable pages: 2" in understood.json()["detail"]
        assert calls == []

        # Also reject stale candidates created by an older implementation:
        # they must not be accepted into the LCR while source pages are missing.
        with Session() as db:
            analysis = db.query(CaptureAnalysis).filter_by(
                source_evidence_id=evidence_id,
                person_id=caregiver["person"]["id"],
                caregiver_id=caregiver["caregiver"]["id"],
            ).one()
            metadata = dict(analysis.candidates)
            metadata["document_intelligence"] = {
                "source_text_sha256": hashlib.sha256(analysis.source_text.strip().encode()).hexdigest(),
                "observations": [{
                    "subject": "Patient",
                    "observation": "Patient walked with assistance.",
                    "source_quote": "Patient walked with assistance.",
                    "review_status": "pending_review",
                    "field_type": "observation",
                    "warnings": [],
                    "canonical_candidate": {},
                }],
            }
            analysis.candidates = metadata
            db.add(analysis)
            db.commit()
            analysis_id = analysis.id

        listed = client.get("/api/v1/documents/intelligence", headers=auth)
        assert listed.status_code == 200, listed.text
        listed_analysis = next(item for item in listed.json()["items"] if item["analysis_id"] == analysis_id)
        assert listed_analysis["interpretation_blocked"] is True
        assert listed_analysis["observations"] == []
        assert listed_analysis["review_required"] is False

        review = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={"candidate_index": 0, "decision": "accept"},
        )
        assert review.status_code == 409, review.text
        assert "blocked until every page has a complete extraction" in review.json()["detail"]
        with Session() as db:
            assert db.query(CareAssertion).filter_by(person_id=caregiver["person"]["id"]).count() == 0
            assert db.query(DocumentReviewEvent).filter_by(analysis_id=analysis_id).count() == 0
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
def test_corrected_medication_candidate_writes_corrected_typed_lcr_value(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", Session)
    client = TestClient(app)
    quote = "The patient has not been taking Metformin 1000 mg twice daily. Event date 2026-10-12. Recorded on 2026-10-13."
    source = f"[Page 1]\nMedication reconciliation: {quote}"

    def fake_extract(page_text, *, source_event_id, source_evidence_id=None):
        return {"observations": [{
            "subject": "Metformin",
            "observation": quote,
            "source_quote": quote,
            "extraction_method": "test_verbatim",
        }], "open_questions": []}

    monkeypatch.setattr(intelligence_module, "extract_review_candidates", fake_extract)
    try:
        caregiver = create_record(client, "Medication correction caregiver")
        auth = {"Authorization": "Bearer " + caregiver["access_token"]}
        evidence_id = save_original(caregiver, source)
        extracted = client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
        assert extracted.status_code == 201, extracted.text
        understood = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=auth)
        assert understood.status_code == 201, understood.text
        analysis_id = understood.json()["analysis_id"]

        unsupported_correction = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={
                "candidate_index": 0,
                "decision": "correct",
                "corrected_subject": "Metformin",
                "corrected_observation": "Metformin 5000 mg daily.",
            },
        )
        assert unsupported_correction.status_code == 409, unsupported_correction.text

        qualifier_dropping_correction = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={
                "candidate_index": 0,
                "decision": "correct",
                "corrected_subject": "Metformin",
                "corrected_observation": "Metformin 1000 mg twice daily.",
            },
        )
        assert qualifier_dropping_correction.status_code == 409, qualifier_dropping_correction.text

        corrected = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={
                "candidate_index": 0,
                "decision": "correct",
                "corrected_subject": "Metformin",
                "corrected_observation": quote,
            },
        )
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["candidate"]["review_status"] == "corrected_pending_approval"
        assert corrected.json()["written_to_lcr"] is False

        accepted = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={"candidate_index": 0, "decision": "accept"},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["written_to_lcr"] is True

        with Session() as db:
            review_events = db.query(DocumentReviewEvent).filter_by(
                analysis_id=analysis_id,
                candidate_index=0,
            ).order_by(DocumentReviewEvent.decision_version.asc()).all()
            assert len(review_events) == 2
            assert [event.decision for event in review_events] == ["correct", "accept"]
            assert review_events[0].before_state["observation"] == quote
            assert review_events[0].after_state["observation"] == quote
            assert review_events[0].after_state["source_quote"] == quote
            assert review_events[1].before_state["observation"] == quote
            assertion = db.query(CareAssertion).filter_by(
                person_id=caregiver["person"]["id"]
            ).one()
            assert assertion.field == "medication"
            assert "Strength: 1000 mg" in assertion.asserted_value
            assert "Dosage instructions: The patient has not been taking Metformin 1000 mg twice daily" in assertion.asserted_value
            assert quote == assertion.source_text
            assert "Event date: 2026-10-12" in assertion.asserted_value
            assert "Source-recorded date: 2026-10-13" in assertion.asserted_value
            assert assertion.event_time_text == "2026-10-12"
        current_care = client.get(
            "/api/v1/care-continuity/current-care?limit_per_source=12",
            headers=auth,
        )
        assert current_care.status_code == 200, current_care.text
        lcr_item = next(
            item for item in current_care.json()["items"]
            if item["title"] == "medication"
        )
        assert "Event date: 2026-10-12" in lcr_item["description"]
        assert "Source-recorded date: 2026-10-13" in lcr_item["description"]
        assert lcr_item["occurred_at"] == "2026-10-12"
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)


def test_partial_document_intelligence_resumes_remaining_pages_without_replacing_candidates(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", Session)
    client = TestClient(app)
    source = "\n\n".join(
        f"[Page {page}]\nPatient was observed completing activity on page {page}."
        for page in range(1, 27)
    )
    processed_pages = []

    def fake_extract(page_text, *, source_event_id, source_evidence_id=None):
        quote = page_text.splitlines()[-1]
        page_number = int(re.search(r"page (\d+)", quote).group(1))
        processed_pages.append(page_number)
        return {
            "observations": [{
                "subject": "Patient",
                "observation": quote,
                "source_quote": quote,
                "extraction_method": "test_verbatim",
            }],
            "open_questions": [],
        }

    import re
    monkeypatch.setattr(intelligence_module, "extract_review_candidates", fake_extract)
    try:
        caregiver = create_record(client, "Partial document caregiver")
        auth = {"Authorization": "Bearer " + caregiver["access_token"]}
        evidence_id = save_original(caregiver, source)
        extracted = client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
        assert extracted.status_code == 201, extracted.text

        first = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=auth)
        assert first.status_code == 201, first.text
        assert first.json()["intelligence_status"] == "partial"
        assert first.json()["unprocessed_page_numbers"] == [26]
        assert len(first.json()["observations"]) == 25
        assert processed_pages == list(range(1, 26))

        resumed = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=auth)
        assert resumed.status_code == 201, resumed.text
        assert resumed.json()["intelligence_status"] == "available"
        assert resumed.json()["unprocessed_page_numbers"] == []
        assert len(resumed.json()["observations"]) == 26
        assert processed_pages == list(range(1, 27))
        assert resumed.json()["processed_page_numbers"] == list(range(1, 27))

        repeated = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=auth)
        assert repeated.status_code == 201, repeated.text
        assert len(repeated.json()["observations"]) == 26
        assert processed_pages == list(range(1, 27))
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)

def test_document_candidate_overflow_is_preserved_and_revealed_in_review_batches(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", Session)
    client = TestClient(app)
    source = "\n\n".join(
        f"[Page {page}]\n" + "\n".join(
            f"Patient record for page {page}. Observation {item_number}."
            for item_number in range(1, 13)
        )
        for page in range(1, 7)
    )
    processed_pages = []

    def fake_extract(page_text, *, source_event_id, source_evidence_id=None):
        page_number = int(re.search(r"page (\d+)", page_text).group(1))
        processed_pages.append(page_number)
        observations = []
        for item_number in range(1, 13):
            quote = f"Patient record for page {page_number}. Observation {item_number}."
            observations.append({
                "subject": "Patient record",
                "observation": quote,
                "source_quote": quote,
                "extraction_method": "test_verbatim",
            })
        return {"observations": observations, "open_questions": []}

    import re
    monkeypatch.setattr(intelligence_module, "extract_review_candidates", fake_extract)
    try:
        caregiver = create_record(client, "Candidate batch caregiver")
        auth = {"Authorization": "Bearer " + caregiver["access_token"]}
        evidence_id = save_original(caregiver, source)
        extracted = client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
        assert extracted.status_code == 201, extracted.text

        first = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=auth)
        assert first.status_code == 201, first.text
        first_payload = first.json()
        assert len(first_payload["observations"]) == 60
        assert [item["candidate_index"] for item in first_payload["observations"]] == list(range(60))
        assert first_payload["has_more_candidates"] is True
        assert first_payload["remaining_candidate_count"] == 12
        assert first_payload["intelligence_status"] == "partial"
        assert first_payload["unprocessed_page_numbers"] == []
        assert first_payload["retryable_page_numbers"] == []
        assert processed_pages == list(range(1, 7))

        blocked = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=auth)
        assert blocked.status_code == 201, blocked.text
        assert [item["candidate_index"] for item in blocked.json()["observations"]] == list(range(60))
        assert processed_pages == list(range(1, 7)), "review paging must not repeat successful model extraction"

        db = Session()
        try:
            analysis = db.query(CaptureAnalysis).filter_by(source_evidence_id=evidence_id).one()
            metadata = dict(analysis.candidates)
            intelligence = dict(metadata["document_intelligence"])
            observations = list(intelligence["observations"])
            for index in range(60):
                observations[index] = {**observations[index], "review_status": "rejected_by_caregiver"}
            intelligence["observations"] = observations
            metadata["document_intelligence"] = intelligence
            analysis.candidates = metadata
            db.commit()
        finally:
            db.close()

        next_batch = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=auth)
        assert next_batch.status_code == 201, next_batch.text
        next_payload = next_batch.json()
        assert len(next_payload["observations"]) == 12
        assert [item["candidate_index"] for item in next_payload["observations"]] == list(range(60, 72))
        assert next_payload["has_more_candidates"] is False
        assert next_payload["remaining_candidate_count"] == 0
        assert next_payload["intelligence_status"] == "available"
        assert processed_pages == list(range(1, 7))

        repeated = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=auth)
        assert [item["candidate_index"] for item in repeated.json()["observations"]] == list(range(60, 72))
        assert processed_pages == list(range(1, 7))
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)

def test_document_medication_assertion_conflicts_with_existing_canonical_medication(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", Session)
    client = TestClient(app)
    quote = "Donepezil 10 mg once daily."
    second_quote = "Donepezil 20 mg once daily."
    source = "[Page 1]\nMedication list: " + quote

    def fake_extract(page_text, *, source_event_id, source_evidence_id=None):
        selected_quote = second_quote if second_quote in page_text else quote
        return {
            "observations": [{
                "subject": "Donepezil",
                "observation": selected_quote,
                "source_quote": selected_quote,
                "extraction_method": "test_verbatim",
            }],
            "open_questions": [],
        }

    monkeypatch.setattr(intelligence_module, "extract_review_candidates", fake_extract)
    try:
        caregiver = create_record(client, "Medication reconciliation caregiver")
        auth = {"Authorization": "Bearer " + caregiver["access_token"]}
        evidence_id = save_original(caregiver, source)

        db = Session()
        try:
            db.add(CareAssertion(
                person_id=caregiver["person"]["id"],
                subject_type="person",
                subject_id=caregiver["person"]["id"],
                field="medication",
                assertion_kind="current_state",
                asserted_value="Medication: Donepezil 5 mg once daily",
                source_label="Earlier caregiver report",
                source_text="Caregiver previously reported Donepezil 5 mg once daily.",
                evidence_ids=[],
                created_by_caregiver_id=caregiver["caregiver"]["id"],
            ))
            db.commit()
        finally:
            db.close()

        extracted = client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
        assert extracted.status_code == 201, extracted.text
        understood = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=auth)
        assert understood.status_code == 201, understood.text
        assert understood.json()["observations"][0]["field_type"] == "medication"

        accepted = client.post(
            f"/api/v1/documents/intelligence/{understood.json()['analysis_id']}/review",
            headers=auth,
            json={"candidate_index": 0, "decision": "accept"},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["written_to_lcr"] is True
        first_document_assertion_id = accepted.json()["source_record_ref"]["record_id"]

        # A second, different discharge document reports a conflicting dose.
        # Both source-backed assertions must survive; neither silently replaces the other.
        second_evidence_id = save_original(
            caregiver,
            "[Page 1]\\nMedication list: " + second_quote,
        )
        second_extracted = client.post(
            f"/api/v1/documents/{second_evidence_id}/extract", headers=auth
        )
        assert second_extracted.status_code == 201, second_extracted.text
        second_understood = client.post(
            f"/api/v1/documents/{second_evidence_id}/intelligence", headers=auth
        )
        assert second_understood.status_code == 201, second_understood.text
        second_accepted = client.post(
            f"/api/v1/documents/intelligence/{second_understood.json()['analysis_id']}/review",
            headers=auth,
            json={"candidate_index": 0, "decision": "accept"},
        )
        assert second_accepted.status_code == 200, second_accepted.text
        assert second_accepted.json()["written_to_lcr"] is True
        second_document_assertion_id = second_accepted.json()["source_record_ref"]["record_id"]

        from app.models.uncertainty import Contradiction
        db = Session()
        try:
            document_assertions = db.query(CareAssertion).filter(
                CareAssertion.person_id == caregiver["person"]["id"],
                CareAssertion.id.in_([first_document_assertion_id, second_document_assertion_id]),
            ).all()
            assert {row.id for row in document_assertions} == {
                first_document_assertion_id, second_document_assertion_id
            }
            assert {row.source_text for row in document_assertions} == {quote, second_quote}
            assert all(row.field == "medication" for row in document_assertions)
            contradictions = db.query(Contradiction).filter(
                Contradiction.person_id == caregiver["person"]["id"],
                Contradiction.field == "medication",
                Contradiction.resolution_status == "unresolved",
            ).all()
            assert contradictions
            assert any(
                {first_document_assertion_id, second_document_assertion_id}.issubset(
                    set(row.source_assertion_ids or [])
                )
                for row in contradictions
            ), "Both conflicting document assertions must remain represented in unresolved contradiction state"
            from app.services.lcr_context import build_lcr_context
            projection = build_lcr_context(
                db,
                caregiver["person"]["id"],
                caregiver_id=caregiver["caregiver"]["id"],
                limit_per_source=50,
            )
            lcr_assertions = projection["sources"]["assertions"]["items"]
            assert {first_document_assertion_id, second_document_assertion_id}.issubset(
                {item["id"] for item in lcr_assertions}
            )
            lcr_contradictions = projection["sources"]["contradictions"]["items"]
            assert any(
                {first_document_assertion_id, second_document_assertion_id}.issubset(
                    set(item.get("source_assertion_ids") or [])
                )
                for item in lcr_contradictions
            )
        finally:
            db.close()
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
