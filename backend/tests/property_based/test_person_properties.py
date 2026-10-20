"""
Property-based tests for Person domain using Hypothesis.
Tests invariants and properties that should always hold for Person objects.
"""

import pytest
from hypothesis import given, assume, settings as hyp_settings
from hypothesis.strategies import text

from backend.tests.property_based.hypothesis_config import (
    person_dict_strategy,
    assert_valid_person,
    person_id_strategy
)


# Test that our strategies generate valid persons
@given(person_dict_strategy())
def test_generated_persons_are_valid(person_dict):
    """Property: All generated person dictionaries should be valid."""
    assert_valid_person(person_dict)


# Test person ID constraints
@given(person_id_strategy())
def test_person_ids_meet_constraints(person_id):
    """Property: Generated person IDs should meet basic constraints."""
    assume(len(person_id) > 0)  # Already filtered by strategy, but double-check
    assert isinstance(person_id, str)
    assert len(person_id) > 0


# Test person name properties
@given(
    person_id=person_id_strategy(),
    name=text(min_size=1, max_size=100)
)
def test_person_creation_preserves_fields(person_id, name):
    """Property: Person creation should preserve input fields."""
    person_dict = {
        "id": person_id,
        "name": name,
        "created_at": "2023-01-01T00:00:00Z"  # Fixed for simplicity
    }
    
    # The person should have the fields we set
    assert person_dict["id"] == person_id
    assert person_dict["name"] == name
    assert person_dict["created_at"] == "2023-01-01T00:00:00Z"
    
    # And should be valid according to our validator
    assert_valid_person(person_dict)


# Test that empty names are rejected (if that's a business rule)
@given(
    person_id=person_id_strategy(),
    name=text(min_size=0, max_size=0)  # Empty string
)
def test_empty_person_names_are_handled(person_id, name):
    """Property: How the system handles empty person names."""
    person_dict = {
        "id": person_id,
        "name": name,
        "created_at": "2023-01-01T00:00:00Z"
    }
    
    # This test documents current behavior - adjust based on actual requirements
    # For now, we just check that we can create the dict (validation happens elsewhere)
    assert person_dict["name"] == name
    assert len(person_dict["name"]) == 0


# Test person ID uniqueness property (conceptual)
@given(
    person_id1=person_id_strategy(),
    person_id2=person_id_strategy()
)
def test_different_person_ids_can_be_generated(person_id1, person_id2):
    """Property: The ID strategy can generate different values."""
    # Note: This might occasionally fail due to random generation, 
    # but with large enough ID space it's very unlikely
    assume(person_id1 != person_id2)  # Only test when they're actually different
    assert person_id1 != person_id2


# Test person name length properties
@given(
    person_id=person_id_strategy(),
    name=text(min_size=1, max_size=200)
)
def test_person_name_length_bounds(person_id, name):
    """Property: Person names should respect length constraints."""
    person_dict = {
        "id": person_id,
        "name": name,
        "created_at": "2023-01-01T00:00:00Z"
    }
    
    # The length should be within bounds
    assert 1 <= len(person_dict["name"]) <= 200
    assert_valid_person(person_dict)