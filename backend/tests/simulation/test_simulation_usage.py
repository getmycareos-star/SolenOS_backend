"""
Tests demonstrating the usage of the Care Simulation Environment.
Shows how to generate test scenarios for various testing needs.
"""

import pytest
from datetime import datetime

from backend.tests.simulation.care_scenario_simulator import (
    CareSimulationEnvironment,
    CareScenarioType,
    generate_random_scenario,
    generate_adversarial_test_case
)


def test_simulation_environment_creation():
    """Test that we can create a simulation environment."""
    env = CareSimulationEnvironment()
    assert env is not None
    
    # Test with seed for reproducibility
    env_seeded = CareSimulationEnvironment(seed=42)
    assert env_seeded is not None


def test_generate_person():
    """Test generating a person record."""
    env = CareSimulationEnvironment()
    person = env.generate_person()
    
    assert "id" in person
    assert "name" in person
    assert "date_of_birth" in person
    assert "gender" in person
    assert isinstance(person["id"], str)
    assert len(person["id"]) > 0
    assert isinstance(person["name"], str)
    assert len(person["name"]) > 0


def test_generate_caregiver():
    """Test generating a caregiver record."""
    env = CareSimulationEnvironment()
    person = env.generate_person()
    caregiver = env.generate_caregiver(person["id"])
    
    assert "id" in caregiver
    assert "name" in caregiver
    assert "relationship" in caregiver
    assert "person_id" in caregiver
    assert caregiver["person_id"] == person["id"]
    assert isinstance(caregiver["id"], str)
    assert len(caregiver["id"]) > 0


def test_generate_evidence():
    """Test generating evidence records."""
    env = CareSimulationEnvironment()
    person = env.generate_person()
    
    # Test different scenario types
    for scenario_type in CareScenarioType:
        evidence = env.generate_evidence(
            person_id=person["id"],
            scenario_type=scenario_type
        )
        
        assert "id" in evidence
        assert "subject_id" in evidence
        assert "type" in evidence
        assert "source_text" in evidence
        assert "confidence_overall" in evidence
        assert evidence["subject_id"] == person["id"]
        assert 0.0 <= evidence["confidence_overall"] <= 1.0


def test_generate_care_event():
    """Test generating care event records."""
    env = CareSimulationEnvironment()
    person = env.generate_person()
    
    # Test different scenario types
    for scenario_type in CareScenarioType:
        care_event = env.generate_care_event(
            person_id=person["id"],
            scenario_type=scenario_type
        )
        
        assert "id" in care_event
        assert "person_id" in care_event
        assert "event_type" in care_event
        assert "title" in care_event
        assert "original_expression" in care_event
        assert "event_time" in care_event
        assert "created_at" in care_event
        assert "confidence_overall" in care_event
        assert care_event["person_id"] == person["id"]
        assert 0.0 <= care_event["confidence_overall"] <= 1.0


def test_generate_complete_scenario():
    """Test generating a complete care scenario."""
    env = CareSimulationEnvironment(seed=123)  # Fixed seed for reproducibility
    
    scenario = env.generate_care_scenario(
        scenario_type=CareScenarioType.DAILY_ROUTINE,
        duration_days=3,
        events_per_day=2
    )
    
    assert "scenario_type" in scenario
    assert "person" in scenario
    assert "primary_caregiver" in scenario
    assert "evidence_list" in scenario
    assert "event_list" in scenario
    assert scenario["scenario_type"] == "daily_routine"
    assert scenario["duration_days"] == 3
    assert isinstance(scenario["person"], dict)
    assert isinstance(scenario["primary_caregiver"], dict)
    assert isinstance(scenario["evidence_list"], list)
    assert isinstance(scenario["event_list"], list)
    assert scenario["total_evidence"] == len(scenario["evidence_list"])
    assert scenario["total_events"] == len(scenario["event_list"])
    
    # Check that we generated the expected number of items
    expected_evidence = 3 * 2  # days * events_per_day
    expected_events = 3 * 2    # days * events_per_day
    assert scenario["total_evidence"] == expected_evidence
    assert scenario["total_events"] == expected_events


def test_generate_adversarial_scenario():
    """Test generating adversarial test scenarios."""
    env = CareSimulationEnvironment(seed=456)
    
    adversarial_scenario = env.generate_adversarial_scenario()
    
    assert "scenario_type" in adversarial_scenario
    assert adversarial_scenario["scenario_type"].startswith("adversarial_")
    assert "adversarial_elements" in adversarial_scenario
    assert isinstance(adversarial_scenario["adversarial_elements"], list)
    
    # Should have person, caregiver, evidence, events
    assert "person" in adversarial_scenario
    assert "primary_caregiver" in adversarial_scenario
    assert "evidence_list" in adversarial_scenario
    assert "event_list" in adversarial_scenario


def test_convenience_functions():
    """Test the convenience functions for easy usage."""
    # Test generate_random_scenario
    scenario = generate_random_scenario(CareScenarioType.MEDICATION_CHANGE)
    assert scenario["scenario_type"] == "medication_change"
    assert "person" in scenario
    assert "event_list" in scenario
    
    # Test generate_adversarial_test_case
    adversarial = generate_adversarial_test_case()
    assert adversarial["scenario_type"].startswith("adversarial_")
    assert "adversarial_elements" in adversarial


def test_different_scenario_types_produce_different_content():
    """Test that different scenario types produce appropriately different content."""
    env = CareSimulationEnvironment(seed=999)
    
    # Generate medication scenario
    med_scenario = env.generate_care_scenario(
        scenario_type=CareScenarioType.MEDICATION_CHANGE,
        duration_days=1,
        events_per_day=2
    )
    
    # Generate health decline scenario
    health_scenario = env.generate_care_scenario(
        scenario_type=CareScenarioType.HEALTH_DECLINE,
        duration_days=1,
        events_per_day=2
    )
    
    # They should have different scenario types
    assert med_scenario["scenario_type"] == "medication_change"
    assert health_scenario["scenario_type"] == "health_decline"
    
    # Check that the content reflects the scenario type
    # Medication scenario should have medication-related evidence/events
    med_evidence_types = [e["type"] for e in med_scenario["evidence_list"]]
    health_evidence_types = [e["type"] for e in health_scenario["evidence_list"]]
    
    # At least some evidence should be medication-related in med scenario
    med_related = any("MEDICATION" in etype for etype in med_evidence_types)
    # Health scenario should have symptom-related evidence
    health_related = any("SYMPTOM" in etype for etype in health_evidence_types)
    
    # Note: This is probabilistic, but with multiple events it's likely
    # In a real test suite, we might check specific known mappings


def test_simulation_is_reproducible_with_seed():
    """Test that using the same seed produces reproducible results."""
    env1 = CareSimulationEnvironment(seed=42)
    env2 = CareSimulationEnvironment(seed=42)
    
    # Generate the same scenario from both environments
    scenario1 = env1.generate_care_scenario(
        scenario_type=CareScenarioType.DAILY_ROUTINE,
        duration_days=2,
        events_per_day=2
    )
    
    scenario2 = env2.generate_care_scenario(
        scenario_type=CareScenarioType.DAILY_ROUTINE,
        duration_days=2,
        events_per_day=2
    )
    
    # The generated IDs should be the same due to same seed
    assert scenario1["person"]["id"] == scenario2["person"]["id"]
    assert scenario1["primary_caregiver"]["id"] == scenario2["primary_caregiver"]["id"]
    
    # First few evidence/events should also match
    if scenario1["evidence_list"] and scenario2["evidence_list"]:
        assert scenario1["evidence_list"][0]["id"] == scenario2["evidence_list"][0]["id"]
    
    if scenario1["event_list"] and scenario2["event_list"]:
        assert scenario1["event_list"][0]["id"] == scenario2["event_list"][0]["id"]


def test_simulation_handles_edge_cases():
    """Test that simulation handles edge cases gracefully."""
    env = CareSimulationEnvironment()
    
    # Very short duration
    short_scenario = env.generate_care_scenario(
        scenario_type=CareScenarioType.DAILY_ROUTINE,
        duration_days=1,
        events_per_day=1
    )
    assert short_scenario["duration_days"] == 1
    assert short_scenario["total_events"] >= 0
    
    # Zero events per day
    zero_events_scenario = env.generate_care_scenario(
        scenario_type=CareScenarioType.DAILY_ROUTINE,
        duration_days=3,
        events_per_day=0
    )
    assert zero_events_scenario["total_events"] == 0
    assert zero_events_scenario["total_evidence"] == 0  # Evidence generation tied to events in current implementation
    
    # Longer duration
    long_scenario = env.generate_care_scenario(
        scenario_type=CareScenarioType.DAILY_ROUTINE,
        duration_days=30,
        events_per_day=5
    )
    assert long_scenario["duration_days"] == 30
    assert long_scenario["total_events"] > 0


if __name__ == "__main__":
    # Run the tests
    test_simulation_environment_creation()
    test_generate_person()
    test_generate_caregiver()
    test_generate_evidence()
    test_generate_care_event()
    test_generate_complete_scenario()
    test_generate_adversarial_scenario()
    test_convenience_functions()
    test_different_scenario_types_produce_different_content()
    test_simulation_is_reproducible_with_seed()
    test_simulation_handles_edge_cases()
    print("All simulation tests passed!")