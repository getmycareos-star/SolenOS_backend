"""
End-to-end test for the Person Responsibility foundational workflow.
This test demonstrates the complete workflow for managing people, relationships, roles, responsibilities, availability, and information contributions.
"""

import sys
import os
from datetime import datetime, timedelta

# Add the backend directory to the path
sys.path.insert(0, 'C:\\solenos\\backend')

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.database import Base
from app.models.identity import Person, Account, AccountPersonLink
from app.models.person_relationship import PersonRelationship
from app.models.person_role import PersonRole
from app.models.person_responsibility import PersonResponsibility
from app.models.person_availability import PersonAvailability
from app.models.information_contribution import InformationContribution
from app.models.care_event import CareEvent
from app.models.investor import Investor
from app.core.handoff_enums import ResponsibilityStatus, ResponsibilityType
from app.core.caregiver_enums import CaregiverAvailabilityStatus
from app.core.identity_enums import AccountType, PersonType


def test_complete_workflow():
    """Test the complete workflow for the Person Responsibility foundational workflow."""
    print("Starting complete workflow test...")
    
    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    
    try:
        # ===== SETUP: Create test accounts and person =====
        print("Setting up test data...")
        
        # Create accounts
        account1 = Account(
            id="acc-caregiver-1",
            email="caregiver1@example.com",
            password_hash="hashed_password_1",
            account_type=AccountType.CAREGIVER,  # Required field
            is_active=True,
            is_verified=True
        )
        account2 = Account(
            id="acc-caregiver-2",
            email="caregiver2@example.com",
            password_hash="hashed_password_2",
            account_type=AccountType.CAREGIVER,  # Required field
            is_active=True,
            is_verified=True
        )
        db.add_all([account1, account2])
        db.commit()
        
        # Create person (care recipient)
        person = Person(
            id="person-care-recipient-1",
            name="Jane Doe",
            person_type=PersonType.CARE_RECIPIENT,  # Required field
            is_active=True
        )
        db.add(person)
        db.commit()
        
        # Link accounts to person (authorization)
        link1 = AccountPersonLink(
            account_id=account1.id,
            person_id=person.id,
            link_type="caregiver",
            is_active=True
        )
        link2 = AccountPersonLink(
            account_id=account2.id,
            person_id=person.id,
            link_type="caregiver",
            is_active=True
        )
        db.add_all([link1, link2])
        db.commit()
        
        print(f"Created person {person.name} with ID {person.id}")
        print(f"Created 2 caregiver accounts")
        
        # ===== TEST 1: Person Relationships =====
        print("\nTesting Person Relationships...")
        
        # Create relationships
        daughter_rel = PersonRelationship(
            person_id=person.id,
            relationship_type="daughter",
            relationship_description="Biological daughter and primary caregiver",
            is_primary=1,  # Both models use is_primary
            relationship_source="Self-reported and verified",
            created_by_account_id=account1.id
        )
        
        friend_rel = PersonRelationship(
            person_id=person.id,
            relationship_type="friend",
            relationship_description="Long-time friend who helps occasionally",
            is_primary=0,  # Both models use is_primary
            relationship_source="Self-reported",
            created_by_account_id=account2.id
        )
        
        db.add_all([daughter_rel, friend_rel])
        db.commit()
        
        # Query relationships
        relationships = db.query(PersonRelationship).filter(
            PersonRelationship.person_id == person.id
        ).all()
        
        assert len(relationships) == 2
        assert any(r.relationship_type == "daughter" and r.is_primary == 1 for r in relationships)
        assert any(r.relationship_type == "friend" and r.is_primary == 0 for r in relationships)
        
        print(f"Created and verified {len(relationships)} relationships")
        
        # ===== TEST 2: Person Roles =====
        print("\nTesting Person Roles...")
        
        # Create roles
        primary_role = PersonRole(
            person_id=person.id,
            role_type="family caregiver",
            custom_role_name="Primary Family Caregiver",
            role_description="Provides daily care and makes health decisions",
            is_primary=1,  # Fixed: PersonRole uses is_primary
            role_source="Self-reported with family agreement",
            permissions=["read_and_act", "read_act_and_share"],
            effective_start=datetime.utcnow(),
            is_active=1,
            created_by_account_id=account1.id
        )
        
        financial_role = PersonRole(
            person_id=person.id,
            role_type="financial coordinator",
            custom_role_name="Money Manager",
            role_description="Handles bill payments and insurance claims",
            is_primary=0,  # Fixed: PersonRole uses is_primary
            role_source="Agreed by family",
            permissions=["read_and_act"],
            effective_start=datetime.utcnow(),
            is_active=1,
            created_by_account_id=account2.id
        )
        
        db.add_all([primary_role, financial_role])
        db.commit()
        
        # Query roles
        roles = db.query(PersonRole).filter(PersonRole.person_id == person.id).all()
        
        assert len(roles) == 2
        assert any(r.role_type == "family caregiver" and r.is_primary == 1 for r in roles)
        assert any(r.role_type == "financial coordinator" and r.is_primary == 0 for r in roles)
        assert any("read_act_and_share" in r.permissions for r in roles)
        
        print(f"Created and verified {len(roles)} roles")
        
        # ===== TEST 3: Person Responsibilities =====
        print("\nTesting Person Responsibilities...")
        
        # Create responsibilities
        med_resp = PersonResponsibility(
            person_id=person.id,
            responsibility_type=ResponsibilityType.MEDICATION_MANAGEMENT,
            title="Medication Management",
            description="Manage daily medications, prescriptions, and pharmacy coordination",
            owner_account_id=account1.id,
            status=ResponsibilityStatus.ACTIVE,
            expected_timing="daily",
            frequency=1,
            source_evidence_ids=["med-evidence-001", "med-evidence-002"],
            provenance="Caregiver training certificate and physician instructions",
            is_active=1,
            # Note: PersonResponsibility doesn't have created_by_account_id, so we don't set it
            # It has created_at and updated_at which are set automatically
        )
        
        transport_resp = PersonResponsibility(
            person_id=person.id,
            responsibility_type=ResponsibilityType.APPOINTMENTS_AND_TRANSPORT,
            title="Medical Appointment Coordination",
            description="Schedule, coordinate, and provide transportation to medical appointments",
            owner_account_id=account2.id,
            status=ResponsibilityStatus.ACTIVE,
            expected_timing="as needed",
            frequency=None,
            source_evidence_ids=["transport-evidence-001"],
            provenance="Shared family calendar and vehicle availability",
            is_active=1,
            # Note: PersonResponsibility doesn't have created_by_account_id, so we don't set it
        )
        
        # Create an unassigned responsibility
        unassigned_resp = PersonResponsibility(
            person_id=person.id,
            responsibility_type=ResponsibilityType.FINANCIAL_MANAGEMENT,
            title="Bill Payment Management",
            description="Manage household bills, insurance payments, and financial accounts",
            owner_account_id=None,  # Explicitly unassigned
            status=ResponsibilityStatus.PROPOSED,  # Use PROPOSED for unassigned
            expected_timing="weekly",
            frequency=1,
            provenance="Identified gap in care responsibility matrix",
            is_active=1,
            # Note: PersonResponsibility doesn't have created_by_account_id, so we don't set it
        )
        
        db.add_all([med_resp, transport_resp, unassigned_resp])
        db.commit()
        
        # Query responsibilities after creation
        responsibilities = db.query(PersonResponsibility).filter(
            PersonResponsibility.person_id == person.id
        ).all()

        assert len(responsibilities) == 3
        assert any(r.title == "Medication Management" and r.owner_account_id == account1.id for r in responsibilities)
        assert any(r.title == "Medical Appointment Coordination" and r.owner_account_id == account2.id for r in responsibilities)
        assert any(r.title == "Bill Payment Management" and r.owner_account_id is None for r in responsibilities)
        assert any(r.status == ResponsibilityStatus.PROPOSED and r.owner_account_id is None for r in responsibilities)

        # Test responsibility assignment (reassigning unassigned responsibility)
        # Simulate assigning the financial responsibility to account2
        unassigned_resp.owner_account_id = account2.id
        unassigned_resp.status = ResponsibilityStatus.ACTIVE

        # Track reassignment in history
        reassignment_entry = {
            "previous_owner_id": None,
            "new_owner_id": account2.id,
            "assigned_at": datetime.utcnow().isoformat(),
            "assigned_by": account1.id,
            "reason": "Family agreement to share financial responsibilities"
        }

        history = unassigned_resp.reassignment_history or []
        history.append(reassignment_entry)
        unassigned_resp.reassignment_history = history
        unassigned_resp.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(unassigned_resp)

        # Query responsibilities after reassignment
        responsibilities_after = db.query(PersonResponsibility).filter(
            PersonResponsibility.person_id == person.id
        ).all()

        assert len(responsibilities_after) == 3
        assert any(r.title == "Medication Management" and r.owner_account_id == account1.id and r.status == ResponsibilityStatus.ACTIVE for r in responsibilities_after)
        assert any(r.title == "Medical Appointment Coordination" and r.owner_account_id == account2.id and r.status == ResponsibilityStatus.ACTIVE for r in responsibilities_after)
        assert any(r.title == "Bill Payment Management" and r.owner_account_id == account2.id and r.status == ResponsibilityStatus.ACTIVE for r in responsibilities_after)
        assert unassigned_resp.owner_account_id == account2.id
        assert len(unassigned_resp.reassignment_history) == 1
        assert unassigned_resp.reassignment_history[0]["new_owner_id"] == account2.id
        
        print(f"Created and verified {len(responsibilities)} responsibilities")
        print("Tested responsibility assignment and history tracking")
        
        # ===== TEST 4: Person Availability =====
        print("\nTesting Person Availability...")
        
        # Create availability records
        caregiver1_avail = PersonAvailability(
            person_id=person.id,
            availability_status=CaregiverAvailabilityStatus.AVAILABLE,
            availability_description="Available Monday-Friday 8am-6pm for caregiving duties",
            effective_start=datetime.utcnow(),
            is_recurring=1,
            recurrence_pattern="weekdays 8am-6pm",
            timezone="America/New_York",
            preferred_contact_method="phone",
            contact_information={"phone": "555-1234", "backup": "555-5678"},
            availability_source="Caregiver availability form",
            verified_by_account_id=account1.id,
            verified_at=datetime.utcnow(),
            is_active=1,
            # Note: PersonAvailability doesn't have created_by_account_id, so we don't set it
        )
        
        caregiver2_avail = PersonAvailability(
            person_id=person.id,
            availability_status=CaregiverAvailabilityStatus.LIMITED,
            availability_description="Available weekends and evenings only due to work commitments",
            effective_start=datetime.utcnow(),
            effective_end=datetime.utcnow() + timedelta(days=30),  # Temporary limitation
            is_recurring=1,
            recurrence_pattern="weekends 9am-5pm, weekdays 6pm-10pm",
            timezone="America/New_York",
            preferred_contact_method="text",
            contact_information={"phone": "555-9999"},
            availability_source="Work schedule documentation",
            verified_by_account_id=account2.id,
            verified_at=datetime.utcnow(),
            is_active=1,
            # Note: PersonAvailability doesn't have created_by_account_id, so we don't set it
        )
        
        db.add_all([caregiver1_avail, caregiver2_avail])
        db.commit()
        
        # Query availabilities
        availabilities = db.query(PersonAvailability).filter(
            PersonAvailability.person_id == person.id
        ).all()
        
        assert len(availabilities) == 2
        assert any(a.availability_status == CaregiverAvailabilityStatus.AVAILABLE and a.is_recurring == 1 for a in availabilities)
        assert any(a.availability_status == CaregiverAvailabilityStatus.LIMITED for a in availabilities)
        assert any("America/New_York" in a.timezone for a in availabilities)
        
        print(f"Created and verified {len(availabilities)} availability records")
        
        # ===== TEST 5: Information Contributions =====
        print("\nTesting Information Contributions...")
        
        # Create information contributions
        obs_contrib = InformationContribution(
            person_id=person.id,
            contributor_account_id=account1.id,
            contribution_type="observation",
            contribution_description="Patient took morning medication without issue and reported feeling better",
            evidence_ids=["obs-evidence-001"],
            related_evidence_ids=["med-evidence-001"],
            contribution_context="Morning medication routine observation",
            contribution_source="Direct observation by caregiver",
            contributed_at=datetime.utcnow(),
            is_active=1,
            # Note: InformationContribution uses contributor_account_id instead of created_by_account_id
            # It also has created_at and updated_at which are set automatically
        )
        
        measure_contrib = InformationContribution(
            person_id=person.id,
            contributor_account_id=account2.id,
            contribution_type="measurement",
            contribution_description="Blood pressure reading: 120/80 mmHg, heart rate: 72 bpm",
            evidence_ids=["measure-evidence-001"],
            contribution_context="Weekly vital signs check",
            contribution_source="Home blood pressure monitor reading",
            contributed_at=datetime.utcnow(),
            is_active=1,
            # Note: InformationContribution uses contributor_account_id instead of created_by_account_id
        )
        
        decision_contrib = InformationContribution(
            person_id=person.id,
            contributor_account_id=account1.id,
            contribution_type="decision",
            contribution_description="Decided to reschedule Friday's physical therapy appointment due to weather",
            evidence_ids=["decision-evidence-001"],
            contribution_context="Care coordination discussion",
            contribution_source="Joint decision by caregivers",
            contributed_at=datetime.utcnow(),
            is_active=1,
            # Note: InformationContribution uses contributor_account_id instead of created_by_account_id
        )
        
        db.add_all([obs_contrib, measure_contrib, decision_contrib])
        db.commit()
        
        # Query information contributions
        contributions = db.query(InformationContribution).filter(
            InformationContribution.person_id == person.id
        ).order_by(InformationContribution.contributed_at.desc()).all()
        
        assert len(contributions) == 3
        assert any(c.contribution_type == "observation" and "medication" in c.contribution_description.lower() for c in contributions)
        assert any(c.contribution_type == "measurement" and "blood pressure" in c.contribution_description.lower() for c in contributions)
        assert any(c.contribution_type == "decision" and "appointment" in c.contribution_description.lower() for c in contributions)
        assert any(c.contributor_account_id == account1.id for c in contributions)
        assert any(c.contributor_account_id == account2.id for c in contributions)
        
        print(f"Created and verified {len(contributions)} information contributions")
        
        # ===== TEST 6: Cross-Model Queries and Relationships =====
        print("\nTesting Cross-Model Queries...")
        
        # Get person with all related data
        person_from_db = db.query(Person).filter(Person.id == person.id).first()
        assert person_from_db is not None
        assert person_from_db.name == "Jane Doe"
        
        # Verify relationships
        rels = db.query(PersonRelationship).filter(PersonRelationship.person_id == person.id).all()
        assert len(rels) == 2
        
        # Verify roles
        roles = db.query(PersonRole).filter(PersonRole.person_id == person.id).all()
        assert len(roles) == 2
        
        # Verify responsibilities
        resps = db.query(PersonResponsibility).filter(PersonResponsibility.person_id == person.id).all()
        assert len(resps) == 3
        
        # Verify availabilities
        avails = db.query(PersonAvailability).filter(PersonAvailability.person_id == person.id).all()
        assert len(avails) == 2
        
        # Verify contributions
        contribs = db.query(InformationContribution).filter(InformationContribution.person_id == person.id).all()
        assert len(contribs) == 3
        
        print("All cross-model relationships verified")
        
        # ===== TEST 7: Special Query Endpoints (Simulation) =====
        print("\nTesting Special Query Capabilities...")
        
        # Unassigned responsibilities (before reassignment)
        # Reset the unassigned responsibility to test this scenario
        unassigned_resp_test = PersonResponsibility(
            person_id=person.id,
            responsibility_type=ResponsibilityType.FINANCIAL_MANAGEMENT,
            title="Expense Tracking",
            description="Track daily expenses and maintain budget",
            owner_account_id=None,  # Explicitly unassigned
            status=ResponsibilityStatus.PROPOSED,
            is_active=1,
            # Note: PersonResponsibility doesn't have created_by_account_id, so we don't set it
        )
        db.add(unassigned_resp_test)
        db.commit()
        
        unassigned_resps = db.query(PersonResponsibility).filter(
            PersonResponsibility.person_id == person.id,
            PersonResponsibility.owner_account_id == None,
            PersonResponsibility.is_active == 1
        ).all()
        
        assert len(unassigned_resps) >= 1  # At least the one we just created
        assert any(r.title == "Expense Tracking" for r in unassigned_resps)
        
        # Active responsibilities
        active_resps = db.query(PersonResponsibility).filter(
            PersonResponsibility.person_id == person.id,
            PersonResponsibility.status == ResponsibilityStatus.ACTIVE,
            PersonResponsibility.is_active == 1
        ).all()
        
        assert len(active_resps) >= 2  # Med management and transport coord
        
        print("Special query capabilities verified")
        
        print("\nAll tests passed! The Person Responsibility foundational workflow is working correctly.")
        print(f"\nSummary:")
        print(f"   - Person: {person_from_db.name}")
        print(f"   - Relationships: {len(rels)}")
        print(f"   - Roles: {len(roles)}")
        print(f"   - Responsibilities: {len(resps)}")
        print(f"   - Availability Records: {len(avails)}")
        print(f"   - Information Contributions: {len(contribs)}")
        
        return True
        
    except Exception as e:
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()


if __name__ == "__main__":
    success = test_complete_workflow()
    sys.exit(0 if success else 1)
