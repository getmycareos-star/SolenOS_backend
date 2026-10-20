"""
Hypothesis configuration for property-based testing in SolenOS.
Provides strategies for generating valid care domain objects.
"""

from hypothesis import HealthCheck, settings, Phase
from hypothesis.strategies import text, integers, booleans, floats, sampled_from, lists, dates, times, datetimes, fixed_dictionaries, one_of, none
import datetime
from uuid import uuid4

# Configure hypothesis settings for SolenOS
settings.register_profile(
    "solenos",
    max_examples=100,  # Reasonable number for CI
    deadline=None,  # No deadline for complex domain logic
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
    phases=[Phase.explicit, Phase.reuse, Phase.generate, Phase.target],
    stateful_step_count=10,  # For stateful testing
)

# Load the profile
settings.load_profile("solenos")


# Strategies for care domain primitives
def person_id_strategy():
    """Generate valid person IDs."""
    return text(min_size=1, max_size=50).filter(lambda x: x.isalnum() or '_' in x or '-' in x)


def caregiver_id_strategy():
    """Generate valid caregiver IDs."""
    return text(min_size=1, max_size=50).filter(lambda x: x.isalnum() or '_' in x or '-' in x)


def evidence_id_strategy():
    """Generate valid evidence IDs."""
    return text(min_size=1, max_size=50).filter(lambda x: x.isalnum() or '_' in x or '-' in x)


def care_event_id_strategy():
    """Generate valid care event IDs."""
    return text(min_size=1, max_size=50).filter(lambda x: x.isalnum() or '_' in x or '-' in x)


def observation_text_strategy():
    """Generate realistic observation text."""
    return text(min_size=1, max_size=500)


def timestamp_strategy():
    """Generate valid timestamps."""
    return datetimes(
        min_value=datetime.datetime(2020, 1, 1),
        max_value=datetime.datetime(2030, 12, 31)
    )


def care_event_type_strategy():
    """Generate valid care event types."""
    # Import here to avoid circular dependencies
    from app.core.event_enums import CareEventType
    return sampled_from(list(CareEventType))


def care_event_status_strategy():
    """Generate valid care event statuses."""
    from app.core.event_enums import CareEventStatus
    return sampled_from(list(CareEventStatus))


def care_event_severity_strategy():
    """Generate valid care event severities."""
    from app.core.event_enums import CareEventSeverity
    return sampled_from(list(CareEventSeverity))


def boolean_strategy():
    """Generate boolean values."""
    return booleans()


def positive_integer_strategy():
    """Generate positive integers."""
    return integers(min_value=1, max_value=10000)


def non_negative_float_strategy():
    """Generate non-negative floats."""
    return floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False)


def confidence_score_strategy():
    """Generate valid confidence scores (0.0 to 1.0)."""
    return floats(min_value=0.0, max_value=1.0, allow_nan=False)


# Composite strategies for domain objects
def person_dict_strategy():
    """Generate valid person dictionaries."""
    return fixed_dictionaries({
        "id": person_id_strategy(),
        "name": text(min_size=1, max_size=100),
        "created_at": timestamp_strategy(),
    })


def evidence_dict_strategy():
    """Generate valid evidence dictionaries."""
    return fixed_dictionaries({
        "id": evidence_id_strategy(),
        "subject_id": person_id_strategy(),
        "type": sampled_from(["OBSERVATION", "MEDICATION", "SYMPTOM", "ACTIVITY"]),
        "source_text": observation_text_strategy(),
        "uploaded_by_caregiver_id": caregiver_id_strategy(),
        "source_type": sampled_from(["MANUAL_CAREGIVER_INPUT", "DEVICE", "LAB_RESULT"]),
        "status": sampled_from(["RECORDED", "PROCESSED", "VALIDATED"]),
        "created_at": timestamp_strategy(),
        "confidence_overall": confidence_score_strategy(),
    })


def care_event_dict_strategy():
    """Generate valid care event dictionaries."""
    return fixed_dictionaries({
        "id": care_event_id_strategy(),
        "person_id": person_id_strategy(),
        "event_type": care_event_type_strategy(),
        "event_subtype": one_of(text(min_size=0, max_size=50), none()),
        "title": text(min_size=1, max_size=200),
        "original_expression": observation_text_strategy(),
        "event_time": timestamp_strategy(),
        "created_at": timestamp_strategy(),
        "status": care_event_status_strategy(),
        "severity": care_event_severity_strategy(),
        "confidence_overall": confidence_score_strategy(),
    })


# Helper functions for property-based testing
def assert_valid_person(person_dict):
    """Assert that a person dictionary has valid structure."""
    assert "id" in person_dict and isinstance(person_dict["id"], str)
    assert len(person_dict["id"]) > 0
    assert "name" in person_dict and isinstance(person_dict["name"], str)
    assert len(person_dict["name"]) > 0


def assert_valid_evidence(evidence_dict):
    """Assert that an evidence dictionary has valid structure."""
    assert "id" in evidence_dict and isinstance(evidence_dict["id"], str)
    assert "subject_id" in evidence_dict and isinstance(evidence_dict["subject_id"], str)
    assert "source_text" in evidence_dict and isinstance(evidence_dict["source_text"], str)
    assert "confidence_overall" in evidence_dict
    assert 0.0 <= evidence_dict["confidence_overall"] <= 1.0


def assert_valid_care_event(event_dict):
    """Assert that a care event dictionary has valid structure."""
    assert "id" in event_dict and isinstance(event_dict["id"], str)
    assert "person_id" in event_dict and isinstance(event_dict["person_id"], str)
    assert "title" in event_dict and isinstance(event_dict["title"], str)
    assert len(event_dict["title"]) > 0
    assert "confidence_overall" in event_dict
    assert 0.0 <= event_dict["confidence_overall"] <= 1.0


# Export commonly used strategies
__all__ = [
    "settings",
    "person_id_strategy",
    "caregiver_id_strategy", 
    "evidence_id_strategy",
    "care_event_id_strategy",
    "observation_text_strategy",
    "timestamp_strategy",
    "care_event_type_strategy",
    "care_event_status_strategy",
    "care_event_severity_strategy",
    "person_dict_strategy",
    "evidence_dict_strategy",
    "care_event_dict_strategy",
    "assert_valid_person",
    "assert_valid_evidence",
    "assert_valid_care_event",
]