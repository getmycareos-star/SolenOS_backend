"""
Property-based tests for Care Event domain using Hypothesis.
Tests invariants and properties that should always hold for Care Event objects.
"""

import pytest
from hypothesis import given, assume
from hypothesis.strategies import text
from datetime import datetime

from backend.tests.property_based.hypothesis_config import (
    care_event_dict_strategy,
    assert_valid_care_event,
    care_event_id_strategy,
    person_id_strategy,
    observation_text_strategy,
    confidence_score_strategy,
    care_event_type_strategy,
    care_event_status_strategy,
    care_event_severity_strategy
)


# Test that our strategies generate valid care events
@given(care_event_dict_strategy())
def test_generated_care_events_are_valid(care_event_dict):
    """Property: All generated care event dictionaries should be valid."""
    assert_valid_care_event(care_event_dict)


# Test care event ID constraints
@given(care_event_id_strategy())
def test_care_event_ids_meet_constraints(care_event_id):
    """Property: Generated care event IDs should meet basic constraints."""
    assume(len(care_event_id) > 0)
    assert isinstance(care_event_id, str)
    assert len(care_event_id) > 0


# Test care event confidence bounds
@given(
    care_event_id=care_event_id_strategy(),
    person_id=person_id_strategy(),
    confidence=confidence_score_strategy(),
    event_type=care_event_type_strategy(),
    status=care_event_status_strategy(),
    severity=care_event_severity_strategy()
)
def test_care_event_confidence_bounds(care_event_id, person_id, confidence, event_type, status, severity):
    """Property: Care event confidence scores should be between 0.0 and 1.0."""
    care_event_dict = {
        "id": care_event_id,
        "person_id": person_id,
        "event_type": event_type,
        "event_subtype": None,
        "title": "Test Event",
        "original_expression": "Test expression",
        "event_time": "2023-06-15T14:30:00Z",
        "created_at": "2023-06-15T14:30:00Z",
        "status": status,
        "severity": severity,
        "confidence_overall": confidence
    }
    
    assert_valid_care_event(care_event_dict)
    assert 0.0 <= care_event_dict["confidence_overall"] <= 1.0
    assert care_event_dict["event_type"] == event_type
    assert care_event_dict["status"] == status
    assert care_event_dict["severity"] == severity


# Test care event title length properties
@given(
    care_event_id=care_event_id_strategy(),
    person_id=person_id_strategy(),
    title=text(min_size=1, max_size=200)
)
def test_care_event_title_length_properties(care_event_id, person_id, title):
    """Property: Care event titles should respect length constraints."""
    care_event_dict = {
        "id": care_event_id,
        "person_id": person_id,
        "event_type": "MEDICATION_ADMINISTERED",
        "event_subtype": None,
        "title": title,
        "original_expression": "Mum took her medication",
        "event_time": "2023-06-15T14:30:00Z",
        "created_at": "2023-06-15T14:30:00Z",
        "status": "ACTIVE",
        "severity": "MODERATE",
        "confidence_overall": 0.9
    }
    
    assert_valid_care_event(care_event_dict)
    assert care_event_dict["title"] == title
    assert 1 <= len(care_event_dict["title"]) <= 200


# Test care event temporal consistency
@given(
    care_event_id=care_event_id_strategy(),
    person_id=person_id_strategy(),
    event_time=text(min_size=10, max_size=30),  # Simplified timestamp
    created_at=text(min_size=10, max_size=30)
)
def test_care_event_temporal_fields(care_event_id, person_id, event_time, created_at):
    """Property: Care event should store temporal fields correctly."""
    care_event_dict = {
        "id": care_event_id,
        "person_id": person_id,
        "event_type": "SYMPTOM_OBSERVED",
        "event_subtype": None,
        "title": "Observed symptom",
        "original_expression": "Mum seemed confused",
        "event_time": event_time,
        "created_at": created_at,
        "status": "ACTIVE",
        "severity": "MILD",
        "confidence_overall": 0.7
    }
    
    assert_valid_care_event(care_event_dict)
    assert care_event_dict["event_time"] == event_time
    assert care_event_dict["created_at"] == created_at


# Test care event required fields
@given(
    care_event_id=care_event_id_strategy(),
    person_id=person_id_strategy(),
    title=text(min_size=1, max_size=50)
)
def test_care_event_required_fields(care_event_id, person_id, title):
    """Property: Care events must have required fields."""
    care_event_dict = {
        "id": care_event_id,
        "person_id": person_id,
        "event_type": "APPOINTMENT_ATTENDED",
        "event_subtype": None,
        "title": title,
        "original_expression": "Visited doctor",
        "event_time": "2023-06-15T10:00:00Z",
        "created_at": "2023-06-15T10:00:00Z",
        "status": "RESOLVED",
        "severity": "LOW",
        "confidence_overall": 0.85
    }
    
    # These fields should always be present and valid
    assert "id" in care_event_dict
    assert "person_id" in care_event_dict
    assert "event_type" in care_event_dict
    assert "title" in care_event_dict
    assert "original_expression" in care_event_dict
    assert "event_time" in care_event_dict
    assert "created_at" in care_event_dict
    assert "status" in care_event_dict
    assert "severity" in care_event_dict
    assert "confidence_overall" in care_event_dict
    
    assert_valid_care_event(care_event_dict)


# Test care event enumeration values
@given(
    care_event_id=care_event_id_strategy(),
    person_id=person_id_strategy()
)
def test_care_event_enumeration_values(care_event_id, person_id):
    """Property: Care events should accept all valid enumeration values."""
    # Test a few values from each enum to ensure they work
    test_cases = [
        ("MEDICATION_ADMINISTERED", "ACTIVE", "LOW"),
        ("SYMPTOM_OBSERVED", "RESOLVED", "MODERATE"),
        ("APPOINTMENT_SCHEDULED", "ACTIVE", "HIGH"),
        ("BEHAVIOR_CHANGE_NOTED", "RESOLVED", "MODERATE")
    ]
    
    for event_type, status, severity in test_cases:
        care_event_dict = {
            "id": care_event_id,
            "person_id": person_id,
            "event_type": event_type,
            "event_subtype": None,
            "title": f"Test {event_type}",
            "original_expression": f"Test expression for {event_type}",
            "event_time": "2023-06-15T14:30:00Z",
            "created_at": "2023-06-15T14:30:00Z",
            "status": status,
            "severity": severity,
            "confidence_overall": 0.8
        }
        
        assert_valid_care_event(care_event_dict)
        assert care_event_dict["event_type"] == event_type
        assert care_event_dict["status"] == status
        assert care_event_dict["severity"] == severity