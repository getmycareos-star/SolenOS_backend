"""
Test script for Person Responsibility foundational workflow models.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.database import Base
from app.models.identity import Person, Account
from app.models.person_relationship import PersonRelationship
from app.models.person_role import PersonRole
from app.models.person_responsibility import PersonResponsibility
from app.models.person_availability import PersonAvailability
from app.models.information_contribution import InformationContribution
from app.core.handoff_enums import ResponsibilityStatus, ResponsibilityType
from app.core.caregiver_enums import CaregiverAvailabilityStatus


def test_person_responsibility_models():
    """Test that the new person responsibility models work correctly."""
    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    
    try:
        # Create test accounts
        account1 = Account(
            id="acc1",
            email="test1@example.com",
            password_hash="hashed_password",
            is_active=True,
            is_verified=True
        )
        account2 = Account(
            id="acc2",
            email="test2@example.com",
            password_hash="hashed_password",
            is_active=True,
            is_verified=True
        )
        db.add_all([account1, account2])
        db.commit()
        
        # Create test person
        person = Person(
            id="person1",
            name="John Doe",
            person_type="CARE_RECIPIENT",
            is_active=True
        )
        db.add(person)
        db.commit()
        
        # Test PersonRelationship
        relationship = PersonRelationship(
            person_id=person.id,
            relationship_type="daughter",
            relationship_description="Biological daughter",
            is_principal=1,
            relationship_source="Self-reported",
            created_by_account_id=account1.id
        )
        db.add(relationship)
        db.commit()
        db.refresh(relationship)
        
        assert relationship.id is not None
        assert relationship.person_id == person.id
        assert relationship.relationship_type == "daughter"
        assert relationship.is_principal == 1
        assert relationship.created_by_account_id == account1.id
        
        # Test PersonRole
        role = PersonRole(
            person_id=person.id,
            role_type="family caregiver",
            custom_role_name="Primary caregiver",
            role_description="Provides daily care and support",
            is_principal=1,
            role_source="Self-reported",
            permissions=["read_and_act"],
            effective_start=datetime.utcnow(),
            is_active=1,
            created_by_account_id=account1.id
        )
        db.add(role)
        db.commit()
        db.refresh(role)
        
        assert role.id is not None
        assert role.person_id == person.id
        assert role.role_type == "family caregiver"
        assert role.custom_role_name == "Primary caregiver"
        assert role.is_principal == 1
        assert role.permissions == ["read_and_act"]
        
        # Test PersonResponsibility
        responsibility = PersonResponsibility(
            person_id=person.id,
            responsibility_type=ResponsibilityType.MEDICATION_MANAGEMENT,
            title="Medication Management",
            description="Manage daily medications and prescriptions",
            owner_account_id=account1.id,
            status=ResponsibilityStatus.ACTIVE,
            expected_timing="daily",
            frequency=1,
            source_evidence_ids=["ev1", "ev2"],
            provenance="Self-reported responsibility",
            is_active=1,
            created_by_account_id=account1.id
        )
        db.add(responsibility)
        db.commit()
        db.refresh(responsibility)
        
        assert responsibility.id is not None
        assert responsibility.person_id == person.id
        assert responsibility.responsibility_type == ResponsibilityType.MEDICATION_MANAGEMENT
        assert responsibility.title == "Medication Management"
        assert responsibility.owner_account_id == account1.id
        assert responsibility.status == ResponsibilityStatus.ACTIVE
        assert responsibility.expected_timing == "daily"
        assert responsibility.frequency == 1
        assert responsibility.source_evidence_ids == ["ev1", "ev2"]
        assert responsibility.provenance == "Self-reported responsibility"
        
        # Test PersonAvailability
        availability = PersonAvailability(
            person_id=person.id,
            availability_status=CaregiverAvailabilityStatus.AVAILABLE,
            availability_description="Available Monday-Friday 9am-5pm",
            effective_start=datetime.utcnow(),
            is_recurring=1,
            recurrence_pattern="weekdays 9am-5pm",
            timezone="UTC",
            preferred_contact_method="phone",
            contact_information={"phone": "555-1234"},
            availability_source="Self-reported",
            is_active=1,
            created_by_account_id=account1.id
        )
        db.add(availability)
        db.commit()
        db.refresh(availability)
        
        assert availability.id is not None
        assert availability.person_id == person.id
        assert availability.availability_status == CaregiverAvailabilityStatus.AVAILABLE
        assert availability.availability_description == "Available Monday-Friday 9am-5pm"
        assert availability.is_recurring == 1
        assert availability.recurrence_pattern == "weekdays 9am-5pm"
        assert availability.timezone == "UTC"
        assert availability.preferred_contact_method == "phone"
        assert availability.contact_information == {"phone": "555-1234"}
        
        # Test InformationContribution
        contribution = InformationContribution(
            person_id=person.id,
            contributor_account_id=account1.id,
            contribution_type="observation",
            contribution_description="Patient took medication without issue this morning",
            evidence_ids=["ev1"],
            contribution_context="Morning medication routine",
            contribution_source="Direct observation",
            is_active=1,
            created_by_account_id=account1.id
        )
        db.add(contribution)
        db.commit()
        db.refresh(contribution)
        
        assert contribution.id is not None
        assert contribution.person_id == person.id
        assert contribution.contributor_account_id == account1.id
        assert contribution.contribution_type == "observation"
        assert contribution.contribution_description == "Patient took medication without issue this morning"
        assert contribution.evidence_ids == ["ev1"]
        assert contribution.contribution_context == "Morning medication routine"
        assert contribution.contribution_source == "Direct observation"
        
        # Test queries
        # Get all relationships for person
        relationships = db.query(PersonRelationship).filter(
            PersonRelationship.person_id == person.id
        ).all()
        assert len(relationships) == 1
        assert relationships[0].id == relationship.id
        
        # Get all roles for person
        roles = db.query(PersonRole).filter(
            PersonRole.person_id == person.id
        ).all()
        assert len(roles) == 1
        assert roles[0].id == role.id
        
        # Get all responsibilities for person
        responsibilities = db.query(PersonResponsibility).filter(
            PersonResponsibility.person_id == person.id
        ).all()
        assert len(responsibilities) == 1
        assert responsibilities[0].id == responsibility.id
        
        # Get all availabilities for person
        availabilities = db.query(PersonAvailability).filter(
            PersonAvailability.person_id == person.id
        ).all()
        assert len(availabilities) == 1
        assert availabilities[0].id == availability.id
        
        # Get all information contributions for person
        contributions = db.query(InformationContribution).filter(
            InformationContribution.person_id == person.id
        ).all()
        assert len(contributions) == 1
        assert contributions[0].id == contribution.id
        
        # Test unassigned responsibility
        unassigned_responsibility = PersonResponsibility(
            person_id=person.id,
            responsibility_type=ResponsibilityType.APPOINTMENTS_AND_TRANSPORT,
            title="Appointment Scheduling",
            description="Schedule and coordinate medical appointments",
            owner_account_id=None,  # Unassigned
            status=ResponsibilityStatus.PROPOSED,
            is_active=1,
            created_by_account_id=account1.id
        )
        db.add(unassigned_responsibility)
        db.commit()
        db.refresh(unassigned_responsibility)
        
        assert unassigned_responsibility.owner_account_id is None
        
        # Query for unassigned responsibilities
        unassigned_resps = db.query(PersonResponsibility).filter(
            PersonResponsibility.person_id == person.id,
            PersonResponsibility.owner_account_id == None
        ).all()
        assert len(unassigned_resps) == 1
        assert unassigned_resps[0].id == unassigned_responsibility.id
        
        print("All tests passed!")
        
    finally:
        db.close()


if __name__ == "__main__":
    from datetime import datetime
    test_person_responsibility_models()