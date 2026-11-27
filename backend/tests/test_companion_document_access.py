"""Regression coverage for Companion document-access revocation checks."""

from app.api.collaboration import (
    _companion_response_requires_document_permission,
    _is_document_derived_record_type,
)


class _EvidenceQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *criteria):
        return self

    def with_for_update(self):
        return self

    def all(self):
        return self.rows


class _EvidenceDatabase:
    def __init__(self, rows):
        self.rows = rows

    def query(self, *columns):
        return _EvidenceQuery(self.rows)


def test_document_record_type_aliases_are_normalized_case_insensitively():
    for value in (
        "document",
        "Document_Extraction",
        "document-extractions",
        "CAPTURE_ANALYSES",
        "Uploaded Document",
    ):
        assert _is_document_derived_record_type(value) is True


def test_non_document_record_types_are_not_classified_as_documents():
    for value in ("care_event", "care_assertion", "care_evidence", None, ""):
        assert _is_document_derived_record_type(value) is False


def test_replay_requires_document_permission_for_uppercase_document_evidence():
    db = _EvidenceDatabase([("evidence-1", "DOCUMENT")])

    required = _companion_response_requires_document_permission(
        db,
        person_id="person-1",
        response={"evidence_ids": ["evidence-1"]},
    )

    assert required is True


def test_replay_requires_document_permission_for_document_extraction_evidence_type():
    db = _EvidenceDatabase([("evidence-2", "DOCUMENT_EXTRACTION")])

    required = _companion_response_requires_document_permission(
        db,
        person_id="person-1",
        response={"evidence_ids": ["evidence-2"]},
    )

    assert required is True


def test_replay_fails_closed_when_source_reference_metadata_is_missing():
    db = _EvidenceDatabase([])

    required = _companion_response_requires_document_permission(
        db,
        person_id="person-1",
        response={"source_record_refs": None},
    )

    assert required is True


def test_replay_fails_closed_when_source_reference_metadata_is_not_a_list():
    db = _EvidenceDatabase([])

    required = _companion_response_requires_document_permission(
        db,
        person_id="person-1",
        response={"source_record_refs": {"record_type": "care_event"}},
    )

    assert required is True


def test_replay_fails_closed_when_a_source_reference_has_no_type():
    db = _EvidenceDatabase([])

    required = _companion_response_requires_document_permission(
        db,
        person_id="person-1",
        response={"source_record_refs": [{"record_id": "source-1"}]},
    )

    assert required is True


def test_replay_fails_closed_when_an_evidence_source_cannot_be_resolved():
    db = _EvidenceDatabase([])

    required = _companion_response_requires_document_permission(
        db,
        person_id="person-1",
        response={"source_evidence_ids": ["missing-evidence"]},
    )

    assert required is True


def test_replay_requires_document_permission_for_normalized_document_source_refs():
    db = _EvidenceDatabase([])

    required = _companion_response_requires_document_permission(
        db,
        person_id="person-1",
        response={
            "source_record_refs": [
                {"record_type": "Capture-Analysis", "record_id": "analysis-1"}
            ]
        },
    )

    assert required is True
