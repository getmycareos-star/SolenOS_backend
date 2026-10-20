"""
Property-based tests for Evidence domain using Hypothesis.
Tests invariants and properties that should always hold for Evidence objects.
"""

import pytest
from hypothesis import given, assume
from datetime import datetime

from backend.tests.property_based.hypothesis_config import (
    evidence_dict_strategy,
    assert_valid_evidence,
    evidence_id_strategy,
    person_id_strategy,
    caregiver_id_strategy,
    observation_text_strategy,
    confidence_score_strategy
)


# Test that our strategies generate valid evidence
@given(evidence_dict_strategy())
def test_generated_evidence_is_valid(evidence_dict):
    """Property: All generated evidence dictionaries should be valid."""
    assert_valid_evidence(evidence_dict)


# Test evidence ID constraints
@given(evidence_id_strategy())
def test_evidence_ids_meet_constraints(evidence_id):
    """Property: Generated evidence IDs should meet basic constraints."""
    assume(len(evidence_id) > 0)
    assert isinstance(evidence_id, str)
    assert len(evidence_id) > 0


# Test evidence subject ID references valid person
@given(
    evidence_id=evidence_id_strategy(),
    subject_id=person_id_strategy(),
    uploaded_by=caregiver_id_strategy(),
    source_text=observation_text_strategy(),
    confidence=confidence_score_strategy()
)
def test_evidence_references_valid_ids(evidence_id, subject_id, uploaded_by, source_text, confidence):
    """Property: Evidence should reference valid person and caregiver IDs."""
    evidence_dict = {
        "id": evidence_id,
        "subject_id": subject_id,
        "type": "OBSERVATION",
        "source_text": source_text,
        "uploaded_by_caregiver_id": uploaded_by,
        "source_type": "MANUAL_CAREGIVER_INPUT",
        "status": "RECORDED",
        "created_at": "2023-01-01T00:00:00Z",
        "confidence_overall": confidence
    }
    
    assert_valid_evidence(evidence_dict)
    assert evidence_dict["subject_id"] == subject_id
    assert evidence_dict["uploaded_by_caregiver_id"] == uploaded_by


# Test confidence score bounds
@given(
    evidence_id=evidence_id_strategy(),
    subject_id=person_id_strategy(),
    confidence=confidence_score_strategy()
)
def test_evidence_confidence_bounds(evidence_id, subject_id, confidence):
    """Property: Evidence confidence scores should be between 0.0 and 1.0."""
    evidence_dict = {
        "id": evidence_id,
        "subject_id": subject_id,
        "type": "OBSERVATION",
        "source_text": "Test observation",
        "uploaded_by_caregiver_id": "cg1",
        "source_type": "MANUAL_CAREGIVER_INPUT",
        "status": "RECORDED",
        "created_at": "2023-01-01T00:00:00Z",
        "confidence_overall": confidence
    }
    
    assert_valid_evidence(evidence_dict)
    assert 0.0 <= evidence_dict["confidence_overall"] <= 1.0


# Test evidence text length properties
@given(
    evidence_id=evidence_id_strategy(),
    subject_id=person_id_strategy(),
    source_text=text(min_size=0, max_size=1000)
)
def test_evidence_text_length_properties(evidence_id, subject_id, source_text):
    """Property: Evidence source text should respect length constraints."""
    evidence_dict = {
        "id": evidence_id,
        "subject_id": subject_id,
        "type": "OBSERVATION",
        "source_text": source_text,
        "uploaded_by_caregiver_id": "cg1",
        "source_type": "MANUAL_CAREGIVER_INPUT",
        "status": "RECORDED",
        "created_at": "2023-01-01T00:00:00Z",
        "confidence_overall": 0.8
    }
    
    assert_valid_evidence(evidence_dict)
    assert evidence_dict["source_text"] == source_text
    assert len(evidence_dict["source_text"]) == len(source_text)


# Test evidence type validation
@given(
    evidence_id=evidence_id_strategy(),
    subject_id=person_id_strategy()
)
def test_evidence_type_validation(evidence_id, subject_id):
    """Property: Evidence should accept valid types."""
    valid_types = ["OBSERVATION", "MEDICATION", "SYMPTOM", "ACTIVITY", "LAB_RESULT", "VITAL_SIGN"]
    
    for evidence_type in valid_types:
        evidence_dict = {
            "id": evidence_id,
            "subject_id": subject_id,
            "type": evidence_type,
            "source_text": "Test evidence",
            "uploaded_by_caregiver_id": "cg1",
            "source_type": "MANUAL_CAREGIVER_INPUT",
            "status": "RECORDED",
            "created_at": "2023-01-01T00:00:00Z",
            "confidence_overall": 0.8
        }
        
        # Should not raise validation error for valid types
        assert_valid_evidence(evidence_dict)
        assert evidence_dict["type"] == evidence_type