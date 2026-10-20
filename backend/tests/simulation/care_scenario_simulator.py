"""
Care scenario simulation environment for SolenOS testing.
Provides tools to generate realistic care scenarios for testing system behavior.
"""

import random
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from enum import Enum


class CareScenarioType(Enum):
    """Types of care scenarios that can be simulated."""
    DAILY_ROUTINE = "daily_routine"
    MEDICATION_CHANGE = "medication_change"
    HEALTH_DECLINE = "health_decline"
    EMERGENCY_EVENT = "emergency_event"
    FAMILY_VISIT = "family_visit"
    APPOINTMENT_SCHEDULE = "appointment_schedule"
    BEHAVIORAL_CHANGE = "behavioral_change"
    RECOVERY_PERIOD = "recovery_period"


class CareSimulationEnvironment:
    """Simulation environment for generating care scenarios."""
    
    def __init__(self, seed: Optional[int] = None):
        """Initialize the simulation environment.
        
        Args:
            seed: Optional seed for reproducible simulations
        """
        if seed is not None:
            random.seed(seed)
        
        # Sample data for generating realistic scenarios
        self.person_names = [
            "Margaret Thompson", "Robert Chen", "Elizabeth Wright", 
            "Thomas Baker", "Anna Martinez", "James Wilson"
        ]
        
        self.caregiver_names = [
            "Sarah Thompson", "Michael Chen", "Jessica Wright",
            "David Baker", "Lisa Martinez", "Robert Wilson"
        ]
        
        self.medications = [
            "Aspirin 81mg", "Lisinopril 10mg", "Atorvastatin 20mg",
            "Metformin 500mg", "Omeprazole 20mg", "Warfarin 5mg"
        ]
        
        self.symptoms = [
            "mild confusion", "fatigue", "joint pain", 
            "dizziness", "nausea", "headache", "insomnia"
        ]
        
        self.activities = [
            "walked to garden", "read newspaper", "prepared tea",
            "watched television", "did laundry", "called family"
        ]
        
        self.appointment_types = [
            "GP Checkup", "Cardiology", "Physiotherapy", 
            "Memory Clinic", "Pharmacy Review", "Blood Test"
        ]
    
    def generate_person(self, person_id: Optional[str] = None) -> Dict[str, Any]:
        """Generate a realistic person record."""
        if person_id is None:
            person_id = f"p{random.randint(1000, 9999)}"
        
        return {
            "id": person_id,
            "name": random.choice(self.person_names),
            "date_of_birth": f"{random.randint(1930, 1950)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
            "gender": random.choice(["Male", "Female"]),
            "created_at": self._random_timestamp(past_days=365).isoformat(),
            "updated_at": self._random_timestamp(past_days=30).isoformat()
        }
    
    def generate_caregiver(self, person_id: str, caregiver_id: Optional[str] = None) -> Dict[str, Any]:
        """Generate a realistic caregiver record."""
        if caregiver_id is None:
            caregiver_id = f"cg{random.randint(1000, 9999)}"
        
        return {
            "id": caregiver_id,
            "name": random.choice(self.caregiver_names),
            "relationship": random.choice(["daughter", "son", "spouse", "friend", "neighbor"]),
            "email": f"{self.caregiver_names[0].lower().replace(' ', '.')}@example.com",
            "phone": f"+1-{random.randint(200,999)}-{random.randint(200,999)}-{random.randint(1000,9999)}",
            "person_id": person_id,
            "created_at": self._random_timestamp(past_days=365).isoformat(),
            "updated_at": self._random_timestamp(past_days=30).isoformat()
        }
    
    def generate_evidence(self, 
                         person_id: str,
                         evidence_id: Optional[str] = None,
                         scenario_type: CareScenarioType = CareScenarioType.DAILY_ROUTINE) -> Dict[str, Any]:
        """Generate realistic evidence based on scenario type."""
        if evidence_id is None:
            evidence_id = f"e{random.randint(1000, 9999)}"
        
        # Generate content based on scenario type
        if scenario_type == CareScenarioType.MEDICATION_CHANGE:
            source_text = f"Administered {random.choice(self.medications)} as prescribed"
            evidence_type = "MEDICATION"
        elif scenario_type == CareScenarioType.HEALTH_DECLINE:
            symptom = random.choice(self.symptoms)
            source_text = f"Observed {symptom} during afternoon check"
            evidence_type = "SYMPTOM"
        elif scenario_type == CareScenarioType.BEHAVIORAL_CHANGE:
            source_text = f"Noted unusual behavior: {random.choice(['increased agitation', 'withdrawal', 'repetitive questioning'])}"
            evidence_type = "BEHAVIOR"
        elif scenario_type == CareScenarioType.APPOINTMENT_SCHEDULE:
            appt_type = random.choice(self.appointment_types)
            source_text = f"Attended {appt_type} appointment at 2:00 PM"
            evidence_type = "ACTIVITY"
        else:  # DAILY_ROUTINE or others
            activity = random.choice(self.activities)
            source_text = f"{activity.capitalize()} in the morning"
            evidence_type = "OBSERVATION"
        
        return {
            "id": evidence_id,
            "subject_id": person_id,
            "type": evidence_type,
            "source_text": source_text,
            "uploaded_by_caregiver_id": f"cg{random.randint(1000, 9999)}",
            "source_type": random.choice(["MANUAL_CAREGIVER_INPUT", "DEVICE", "NURSE_ENTRY"]),
            "status": random.choice(["RECORDED", "PROCESSED", "VALIDATED"]),
            "created_at": self._random_timestamp(past_days=7).isoformat(),
            "confidence_overall": round(random.uniform(0.7, 0.95), 2)
        }
    
    def generate_care_event(self,
                           person_id: str,
                           event_id: Optional[str] = None,
                           scenario_type: CareScenarioType = CareScenarioType.DAILY_ROUTINE) -> Dict[str, Any]:
        """Generate realistic care events based on scenario type."""
        if event_id is None:
            event_id = f"ev{random.randint(1000, 9999)}"
        
        # Map scenario types to care event types
        scenario_mapping = {
            CareScenarioType.DAILY_ROUTINE: ("OBSERVATION_COMPLETED", "LOW"),
            CareScenarioType.MEDICATION_CHANGE: ("MEDICATION_ADMINISTERED", "MODERATE"),
            CareScenarioType.HEALTH_DECLINE: ("SYMPTOM_OBSERVED", random.choice(["MODERATE", "HIGH"])),
            CareScenarioType.EMERGENCY_EVENT: ("EMERGENCY_RESPONSE", "CRITICAL"),
            CareScenarioType.FAMILY_VISIT: ("SOCIAL_INTERACTION", "LOW"),
            CareScenarioType.APPOINTMENT_SCHEDULE: ("APPOINTMENT_COMPLETED", "MODERATE"),
            CareScenarioType.BEHAVIORAL_CHANGE: ("BEHAVIOR_CHANGE_NOTED", "MODERATE"),
            CareScenarioType.RECOVERY_PERIOD: ("SYMPTOM_IMPROVED", "LOW")
        }
        
        event_type, default_severity = scenario_mapping.get(scenario_type, ("GENERAL_EVENT", "LOW"))
        
        # Generate timestamps
        event_time = self._random_timestamp(past_days=2)
        created_at = event_time + timedelta(minutes=random.randint(0, 60))
        
        return {
            "id": event_id,
            "person_id": person_id,
            "event_type": event_type,
            "event_subtype": None,
            "title": f"{scenario_type.value.replace('_', ' ').title()} Event",
            "original_expression": self._generate_original_expression(scenario_type, person_id),
            "event_time": event_time.isoformat(),
            "created_at": created_at.isoformat(),
            "status": random.choice(["ACTIVE", "RESOLVED", "VALIDATED"]),
            "severity": default_severity,
            "confidence_overall": round(random.uniform(0.6, 0.98), 2),
            "evidence_ids": [f"e{random.randint(1000, 9999)}" for _ in range(random.randint(1, 3))],
            "subject_id": person_id  # Usually the person for most events
        }
    
    def generate_care_scenario(self,
                              scenario_type: CareScenarioType,
                              duration_days: int = 7,
                              events_per_day: int = 3) -> Dict[str, Any]:
        """Generate a complete care scenario with person, caregiver, evidence, and events."""
        # Create person and caregiver
        person = self.generate_person()
        caregiver = self.generate_caregiver(person["id"])
        
        # Generate evidence and events over the time period
        evidence_list = []
        event_list = []
        
        start_date = datetime.now() - timedelta(days=duration_days)
        
        for day in range(duration_days):
            day_start = start_date + timedelta(days=day)
            
            # Generate multiple evidence/events per day
            for _ in range(events_per_day):
                # Offset within the day
                offset_hours = random.randint(0, 23)
                offset_minutes = random.randint(0, 59)
                event_time = day_start + timedelta(hours=offset_hours, minutes=offset_minutes)
                
                # Generate evidence
                evidence = self.generate_evidence(
                    person_id=person["id"],
                    scenario_type=scenario_type
                )
                # Adjust evidence timestamp
                evidence["created_at"] = event_time.isoformat()
                evidence_list.append(evidence)
                
                # Generate care event
                care_event = self.generate_care_event(
                    person_id=person["id"],
                    scenario_type=scenario_type
                )
                # Adjust care event timestamp
                care_event["event_time"] = event_time.isoformat()
                care_event["created_at"] = (event_time + timedelta(minutes=5)).isoformat()
                event_list.append(care_event)
        
        return {
            "scenario_type": scenario_type.value,
            "duration_days": duration_days,
            "person": person,
            "primary_caregiver": caregiver,
            "evidence_list": evidence_list,
            "event_list": event_list,
            "generation_timestamp": datetime.now().isoformat(),
            "total_evidence": len(evidence_list),
            "total_events": len(event_list)
        }
    
    def generate_adversarial_scenario(self) -> Dict[str, Any]:
        """Generate adversarial test scenarios designed to stress-test the system."""
        scenario_types = list(CareScenarioType)
        scenario_type = random.choice(scenario_types)
        
        # Generate a base scenario
        base_scenario = self.generate_care_scenario(scenario_type, duration_days=1, events_per_day=5)
        
        # Add adversarial elements
        adversarial_elements = []
        
        # 1. Conflicting timestamps
        if base_scenario["event_list"]:
            event = random.choice(base_scenario["event_list"])
            # Make event_time before created_at (temporally impossible)
            event_time = datetime.fromisoformat(event["event_time"].replace("Z", "+00:00"))
            created_time = event_time - timedelta(minutes=10)
            event["created_at"] = created_time.isoformat() + "Z"
            adversarial_elements.append("reversed_timestamps")
        
        # 2. Invalid confidence scores
        if base_scenario["evidence_list"]:
            evidence = random.choice(base_scenario["evidence_list"])
            evidence["confidence_overall"] = random.choice([-0.1, 1.5, 2.0])  # Outside valid range
            adversarial_elements.append("invalid_confidence")
        
        # 3. Missing required fields
        if base_scenario["event_list"] and len(base_scenario["event_list"]) > 2:
            event = base_scenario["event_list"][2]
            del event["title"]  # Remove required field
            adversarial_elements.append("missing_required_field")
        
        # 4. Invalid ID formats
        if base_scenario["person"]:
            base_scenario["person"]["id"] = ""  # Empty ID
            adversarial_elements.append("empty_id")
        
        base_scenario["adversarial_elements"] = adversarial_elements
        base_scenario["scenario_type"] = "adversarial_" + base_scenario["scenario_type"]
        
        return base_scenario
    
    def _random_timestamp(self, past_days: int = 30) -> datetime:
        """Generate a random timestamp within the past N days."""
        days_ago = random.randint(0, past_days)
        hours_ago = random.randint(0, 23)
        minutes_ago = random.randint(0, 59)
        seconds_ago = random.randint(0, 59)
        
        return datetime.now() - timedelta(
            days=days_ago, 
            hours=hours_ago, 
            minutes=minutes_ago, 
            seconds=seconds_ago
        )
    
    def _generate_original_expression(self, scenario_type: CareScenarioType, person_id: str) -> str:
        """Generate realistic original expression text for care events."""
        person_name = "the person"  # In real implementation, would lookup from person_id
        
        expressions = {
            CareScenarioType.DAILY_ROUTINE: [
                f"{person_name} completed morning routine independently",
                f"{person_name} enjoyed breakfast and reading",
                f"{person_name} took a short walk in the garden"
            ],
            CareScenarioType.MEDICATION_CHANGE: [
                f"Administered {random.choice(self.medications)} to {person_name} as prescribed",
                f"{person_name} took medication with water after breakfast",
                f"Medication adherence observed for {person_name}"
            ],
            CareScenarioType.HEALTH_DECLINE: [
                f"Observed {random.choice(self.symptoms)} in {person_name} during evening check",
                f"{person_name} reported increased {random.choice(self.symptoms)} today",
                f"Noted worsening of {random.choice(self.symptoms)} symptoms"
            ],
            CareScenarioType.BEHAVIORAL_CHANGE: [
                f"Noted {random.choice(['increased agitation', 'withdrawal', 'repetitive questioning'])} in {person_name}",
                f"{person_name} displayed unusual behavior patterns today",
                f"Caregiver observed changes in {person_name}'s typical behavior"
            ],
            CareScenarioType.APPOINTMENT_SCHEDULE: [
                f"{person_name} attended {random.choice(self.appointment_types)} appointment",
                f"Successfully completed {random.choice(self.appointment_types)} visit",
                f"{person_name} returned from medical appointment with positive outcomes"
            ]
        }
        
        default_expressions = [
            f"Regular check-in with {person_name}",
            f"Observed {person_name} during routine care",
            f"Standard monitoring activities completed for {person_name}"
        ]
        
        return random.choice(
            expressions.get(scenario_type, default_expressions)
        )


# Convenience functions for easy usage
def create_simulation_seed(seed: int) -> CareSimulationEnvironment:
    """Create a simulation environment with a specific seed for reproducibility."""
    return CareSimulationEnvironment(seed=seed)


def generate_random_scenario(scenario_type: CareScenarioType) -> Dict[str, Any]:
    """Generate a random care scenario of the specified type."""
    env = CareSimulationEnvironment()
    return env.generate_care_scenario(scenario_type)


def generate_adversarial_test_case() -> Dict[str, Any]:
    """Generate an adversarial test case for stress testing."""
    env = CareSimulationEnvironment()
    return env.generate_adversarial_scenario()


# Export public interface
__all__ = [
    "CareSimulationEnvironment",
    "CareScenarioType",
    "create_simulation_seed",
    "generate_random_scenario",
    "generate_adversarial_test_case"
]