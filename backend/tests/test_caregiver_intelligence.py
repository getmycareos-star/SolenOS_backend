from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app
from app.models.care import Person, Caregiver

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_db():
    Base.metadata.create_all(bind=engine)


def teardown_db():
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()


def get_test_client():
    from app.core.database import get_db
    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def create_test_person_and_caregivers(db):
    person = Person(id="person-1", name="Test Patient")
    db.add(person)
    db.commit()

    sarah = Caregiver(
        id="caregiver-sarah",
        name="Sarah",
        email="sarah@example.com",
        relationship="Daughter",
        person_id="person-1",
    )
    john = Caregiver(
        id="caregiver-john",
        name="John",
        email="john@example.com",
        relationship="Spouse",
        person_id="person-1",
    )
    db.add_all([sarah, john])
    db.commit()
    return person, sarah, john


def test_create_caregiver_profile():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        response = client.post(
            "/api/v1/caregiver-profiles",
            json={
                "person_id": "person-1",
                "name": "Sarah",
                "email": "sarah@example.com",
                "relationship": "Daughter",
                "participation_category": "family",
                "involvement_status": "active",
                "proximity_category": "local",
                "timezone": "UTC",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Sarah"
        assert data["relationship"] == "Daughter"
        assert data["participation_category"] == "family"
        assert data["involvement_status"] == "active"
    finally:
        teardown_db()


def test_identity_not_inferred_from_relationship():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        client.post(
            "/api/v1/caregiver-profiles",
            json={
                "person_id": "person-1",
                "name": "Sarah",
                "email": "sarah@example.com",
                "relationship": "Daughter",
                "participation_category": "family",
                "involvement_status": "active",
            },
        )
        response = client.get("/api/v1/caregiver-profiles/person-1")
        assert response.status_code == 200
        profiles = response.json()
        assert len(profiles) == 1
        assert profiles[0]["relationship"] == "Daughter"
        assert profiles[0]["is_primary_designated"] is False
    finally:
        teardown_db()


def test_responsibility_not_inferred_from_activity():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        client.post(
            "/api/v1/caregiver-profiles",
            json={
                "person_id": "person-1",
                "name": "Sarah",
                "email": "sarah@example.com",
                "relationship": "Daughter",
                "participation_category": "family",
            },
        )
        client.post(
            "/api/v1/caregiver-activities",
            json={
                "caregiver_id": "caregiver-sarah",
                "person_id": "person-1",
                "activity_type": "performed_task",
                "description": "Administered medication",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        response = client.get("/api/v1/caregiver-responsibilities/person-1")
        assert response.status_code == 200
        assert len(response.json()) == 0
    finally:
        teardown_db()


def test_caregiver_observation_preserves_identity():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        client.post(
            "/api/v1/caregiver-profiles",
            json={
                "person_id": "person-1",
                "name": "Sarah",
                "email": "sarah@example.com",
                "relationship": "Daughter",
                "participation_category": "family",
            },
        )
        response = client.post(
            "/api/v1/caregiver-observations",
            json={
                "caregiver_id": "caregiver-sarah",
                "person_id": "person-1",
                "observation_content": "Mom seemed more confused today",
                "observation_type": "direct",
                "event_time": datetime.now(timezone.utc).isoformat(),
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["caregiver_id"] == "caregiver-sarah"
        assert data["observation_content"] == "Mom seemed more confused today"
        assert data["is_promoted_to_shared"] is False
    finally:
        teardown_db()


def test_conflicting_observations_preserved():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        for cid, name in [("caregiver-sarah", "Sarah"), ("caregiver-john", "John")]:
            client.post(
                "/api/v1/caregiver-profiles",
                json={
                    "person_id": "person-1",
                    "name": name,
                    "email": f"{name.lower()}@example.com",
                    "relationship": "Family",
                    "participation_category": "family",
                },
            )

        client.post(
            "/api/v1/caregiver-perspectives",
            json={
                "caregiver_id": "caregiver-sarah",
                "person_id": "person-1",
                "claim_type": "observation",
                "claim_content": "Mom fell yesterday",
            },
        )
        client.post(
            "/api/v1/caregiver-perspectives",
            json={
                "caregiver_id": "caregiver-john",
                "person_id": "person-1",
                "claim_type": "observation",
                "claim_content": "Mom did not fall",
            },
        )
        client.post(
            "/api/v1/perspective-conflicts",
            json={
                "person_id": "person-1",
                "caregiver_a_id": "caregiver-sarah",
                "caregiver_b_id": "caregiver-john",
                "conflict_type": "observation_conflict",
                "claim_a": "Mom fell yesterday",
                "claim_b": "Mom did not fall",
                "status": "unresolved",
            },
        )
        response = client.get("/api/v1/perspective-conflicts/person-1")
        assert response.status_code == 200
        conflicts = response.json()
        assert len(conflicts) >= 1
        assert (conflicts[0]["caregiver_a_id"] == "caregiver-sarah" and conflicts[0]["caregiver_b_id"] == "caregiver-john") or (conflicts[0]["caregiver_a_id"] == "caregiver-john" and conflicts[0]["caregiver_b_id"] == "caregiver-sarah")
    finally:
        teardown_db()


def test_handoff_not_accepted_by_silence():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        for cid, name in [("caregiver-sarah", "Sarah"), ("caregiver-john", "John")]:
            client.post(
                "/api/v1/caregiver-profiles",
                json={
                    "person_id": "person-1",
                    "name": name,
                    "email": f"{name.lower()}@example.com",
                    "relationship": "Family",
                    "participation_category": "family",
                },
            )

        response = client.post(
            "/api/v1/caregiver-handoffs",
            json={
                "from_caregiver_id": "caregiver-sarah",
                "to_caregiver_id": "caregiver-john",
                "person_id": "person-1",
                "responsibility_type": "appointment",
                "acceptance_status": "unconfirmed",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["acceptance_status"] == "unconfirmed"
        assert data["is_completed"] is False
    finally:
        teardown_db()


def test_responsibility_gap_detection():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        client.post(
            "/api/v1/caregiver-profiles",
            json={
                "person_id": "person-1",
                "name": "Sarah",
                "email": "sarah@example.com",
                "relationship": "Daughter",
                "participation_category": "family",
            },
        )
        client.post(
            "/api/v1/caregiver-responsibilities",
            json={
                "caregiver_id": "caregiver-sarah",
                "person_id": "person-1",
                "responsibility_type": "medication",
                "effective_start": datetime.now(timezone.utc).isoformat(),
            },
        )
        response = client.get("/api/v1/responsibility-gaps/person-1")
        assert response.status_code == 200
        gaps = response.json()
        gap_types = [g["responsibility_type"] for g in gaps]
        assert "appointment" in gap_types
        assert "transportation" in gap_types
    finally:
        teardown_db()


def test_different_observation_contexts_not_conflict():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        for cid, name in [("caregiver-sarah", "Sarah"), ("caregiver-john", "John")]:
            client.post(
                "/api/v1/caregiver-profiles",
                json={
                    "person_id": "person-1",
                    "name": name,
                    "email": f"{name.lower()}@example.com",
                    "relationship": "Family",
                    "participation_category": "family",
                },
            )
        client.post(
            "/api/v1/caregiver-contexts",
            json={
                "caregiver_id": "caregiver-sarah",
                "person_id": "person-1",
                "observation_window_start": "08:00",
                "observation_window_end": "12:00",
                "effective_start": datetime.now(timezone.utc).isoformat(),
            },
        )
        client.post(
            "/api/v1/caregiver-contexts",
            json={
                "caregiver_id": "caregiver-john",
                "person_id": "person-1",
                "observation_window_start": "18:00",
                "observation_window_end": "22:00",
                "effective_start": datetime.now(timezone.utc).isoformat(),
            },
        )
        client.post(
            "/api/v1/caregiver-observations",
            json={
                "caregiver_id": "caregiver-sarah",
                "person_id": "person-1",
                "observation_content": "Mom was alert this morning",
                "observation_type": "direct",
            },
        )
        client.post(
            "/api/v1/caregiver-observations",
            json={
                "caregiver_id": "caregiver-john",
                "person_id": "person-1",
                "observation_content": "Mom was confused this evening",
                "observation_type": "direct",
            },
        )
        response = client.get("/api/v1/perspective-conflicts/person-1")
        assert response.status_code == 200
        conflicts = response.json()
        if conflicts:
            assert conflicts[0]["status"] == "contextually_divergent"
    finally:
        teardown_db()


def test_unknown_actor_activity():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        response = client.post(
            "/api/v1/unknown-actor-activity",
            params={
                "person_id": "person-1",
                "activity_type": "performed_task",
                "description": "Caregiver administered medication",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "shared_account_id": "caregiver-sarah",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["caregiver_id"] == "unknown"
        assert data["is_anonymous"] is True
    finally:
        teardown_db()


def test_private_observation_not_shared():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        client.post(
            "/api/v1/caregiver-profiles",
            json={
                "person_id": "person-1",
                "name": "Sarah",
                "email": "sarah@example.com",
                "relationship": "Daughter",
                "participation_category": "family",
            },
        )
        response = client.post(
            "/api/v1/caregiver-observations",
            json={
                "caregiver_id": "caregiver-sarah",
                "person_id": "person-1",
                "observation_content": "I am worried about John",
                "observation_type": "direct",
                "information_visibility": "private",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["information_visibility"] == "private"
        assert data["is_promoted_to_shared"] is False
    finally:
        teardown_db()


def test_shared_observation_preserves_provenance():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        client.post(
            "/api/v1/caregiver-profiles",
            json={
                "person_id": "person-1",
                "name": "Sarah",
                "email": "sarah@example.com",
                "relationship": "Daughter",
                "participation_category": "family",
            },
        )
        obs_response = client.post(
            "/api/v1/caregiver-observations",
            json={
                "caregiver_id": "caregiver-sarah",
                "person_id": "person-1",
                "observation_content": "Mom fell yesterday",
                "observation_type": "direct",
            },
        )
        obs_id = obs_response.json()["id"]
        client.post(f"/api/v1/caregiver-observations/{obs_id}/promote", params={"caregiver_id": "caregiver-sarah"})
        response = client.get("/api/v1/caregiver-observations/person-1")
        assert response.status_code == 200
        shared = response.json()
        assert len(shared) == 1
        assert shared[0]["caregiver_id"] == "caregiver-sarah"
        assert shared[0]["is_promoted_to_shared"] is True
    finally:
        teardown_db()


def test_responsibility_history_preserved():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        client.post(
            "/api/v1/caregiver-profiles",
            json={
                "person_id": "person-1",
                "name": "Sarah",
                "email": "sarah@example.com",
                "relationship": "Daughter",
                "participation_category": "family",
            },
        )
        past = datetime.now(timezone.utc) - timedelta(days=30)
        future = datetime.now(timezone.utc) + timedelta(days=30)
        client.post(
            "/api/v1/caregiver-responsibilities",
            json={
                "caregiver_id": "caregiver-sarah",
                "person_id": "person-1",
                "responsibility_type": "medication",
                "effective_start": past.isoformat(),
                "effective_end": datetime.now(timezone.utc).isoformat(),
                "status": "historical",
            },
        )
        client.post(
            "/api/v1/caregiver-responsibilities",
            json={
                "caregiver_id": "caregiver-sarah",
                "person_id": "person-1",
                "responsibility_type": "medication",
                "effective_start": datetime.now(timezone.utc).isoformat(),
                "effective_end": future.isoformat(),
                "status": "active",
            },
        )
        response = client.get("/api/v1/caregiver-responsibilities/history/caregiver-sarah", params={"person_id": "person-1"})
        assert response.status_code == 200
        history = response.json()
        assert len(history) == 2
    finally:
        teardown_db()


def test_no_caregiver_reliability_scores():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        for cid, name in [("caregiver-sarah", "Sarah"), ("caregiver-john", "John")]:
            client.post(
                "/api/v1/caregiver-profiles",
                json={
                    "person_id": "person-1",
                    "name": name,
                    "email": f"{name.lower()}@example.com",
                    "relationship": "Family",
                    "participation_category": "family",
                },
            )
        for _ in range(5):
            client.post(
                "/api/v1/caregiver-activities",
                json={
                    "caregiver_id": "caregiver-sarah",
                    "person_id": "person-1",
                    "activity_type": "performed_task",
                    "description": "Administered medication",
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        for _ in range(2):
            client.post(
                "/api/v1/caregiver-activities",
                json={
                    "caregiver_id": "caregiver-john",
                    "person_id": "person-1",
                    "activity_type": "performed_task",
                    "description": "Administered medication",
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        response = client.get("/api/v1/multi-caregiver-summary/person-1")
        assert response.status_code == 200
        data = response.json()
        for caregiver in data["caregivers"]:
            assert "reliability_score" not in caregiver
            assert "trust_score" not in caregiver
            assert "quality_score" not in caregiver
    finally:
        teardown_db()


def test_multi_caregiver_summary():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        for cid, name in [("caregiver-sarah", "Sarah"), ("caregiver-john", "John")]:
            client.post(
                "/api/v1/caregiver-profiles",
                json={
                    "person_id": "person-1",
                    "name": name,
                    "email": f"{name.lower()}@example.com",
                    "relationship": "Family",
                    "participation_category": "family",
                },
            )
        client.post(
            "/api/v1/caregiver-responsibilities",
            json={
                "caregiver_id": "caregiver-sarah",
                "person_id": "person-1",
                "responsibility_type": "medication",
                "effective_start": datetime.now(timezone.utc).isoformat(),
            },
        )
        client.post(
            "/api/v1/caregiver-responsibilities",
            json={
                "caregiver_id": "caregiver-john",
                "person_id": "person-1",
                "responsibility_type": "appointment",
                "effective_start": datetime.now(timezone.utc).isoformat(),
            },
        )
        response = client.get("/api/v1/multi-caregiver-summary/person-1")
        assert response.status_code == 200
        data = response.json()
        assert len(data["caregivers"]) == 2
        assert len(data["active_responsibilities"]) == 2
    finally:
        teardown_db()


def test_care_network_state():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        for cid, name in [("caregiver-sarah", "Sarah"), ("caregiver-john", "John")]:
            client.post(
                "/api/v1/caregiver-profiles",
                json={
                    "person_id": "person-1",
                    "name": name,
                    "email": f"{name.lower()}@example.com",
                    "relationship": "Family",
                    "participation_category": "family",
                },
            )
        client.post(
            "/api/v1/caregiver-roles",
            json={
                "caregiver_id": "caregiver-sarah",
                "person_id": "person-1",
                "role_type": "medication_manager",
                "effective_start": datetime.now(timezone.utc).isoformat(),
            },
        )
        response = client.get("/api/v1/care-network-state/person-1")
        assert response.status_code == 200
        data = response.json()
        assert data["active_caregiver_count"] == 2
    finally:
        teardown_db()


def test_indirect_observation_distinguished():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        client.post(
            "/api/v1/caregiver-profiles",
            json={
                "person_id": "person-1",
                "name": "Sarah",
                "email": "sarah@example.com",
                "relationship": "Daughter",
                "participation_category": "family",
            },
        )
        response = client.post(
            "/api/v1/caregiver-observations",
            json={
                "caregiver_id": "caregiver-sarah",
                "person_id": "person-1",
                "observation_content": "John told me Mom fell",
                "observation_type": "second_hand",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["observation_type"] == "second_hand"
    finally:
        teardown_db()


def test_duplicate_action_detection():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        for cid, name in [("caregiver-sarah", "Sarah"), ("caregiver-john", "John")]:
            client.post(
                "/api/v1/caregiver-profiles",
                json={
                    "person_id": "person-1",
                    "name": name,
                    "email": f"{name.lower()}@example.com",
                    "relationship": "Family",
                    "participation_category": "family",
                },
            )
        response = client.post(
            "/api/v1/duplicate-actions",
            json={
                "person_id": "person-1",
                "caregiver_a_id": "caregiver-sarah",
                "caregiver_b_id": "caregiver-john",
                "action_type": "appointment_scheduling",
                "action_description_a": "Scheduled cardiology appointment",
                "action_description_b": "Scheduled cardiology appointment",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "potential_duplicate"
    finally:
        teardown_db()


def test_non_family_caregiver_supported():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-1", name="Test Patient")
        db.add(person)
        db.commit()
        professional = Caregiver(
            id="caregiver-prof",
            name="Mary",
            email="mary@agency.com",
            relationship="Home Health Aide",
            person_id="person-1",
        )
        db.add(professional)
        db.commit()
        db.close()

        client = get_test_client()
        response = client.post(
            "/api/v1/caregiver-profiles",
            json={
                "person_id": "person-1",
                "name": "Mary",
                "email": "mary@agency.com",
                "relationship": "Home Health Aide",
                "participation_category": "professional",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["participation_category"] == "professional"
    finally:
        teardown_db()


def test_perspective_vs_shared_state():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        client.post(
            "/api/v1/caregiver-profiles",
            json={
                "person_id": "person-1",
                "name": "Sarah",
                "email": "sarah@example.com",
                "relationship": "Daughter",
                "participation_category": "family",
            },
        )
        client.post(
            "/api/v1/caregiver-perspectives",
            json={
                "caregiver_id": "caregiver-sarah",
                "person_id": "person-1",
                "claim_type": "belief",
                "claim_content": "I believe medication X was discontinued",
                "information_visibility": "private",
            },
        )
        response = client.get("/api/v1/caregiver-perspectives/person-1")
        assert response.status_code == 200
        perspectives = response.json()
        assert len(perspectives) == 0
        response = client.get("/api/v1/caregiver-perspectives/person-1", params={"caregiver_id": "caregiver-sarah"})
        assert response.status_code == 200
        perspectives = response.json()
        assert len(perspectives) == 1
        assert perspectives[0]["claim_content"] == "I believe medication X was discontinued"
    finally:
        teardown_db()


def test_handoff_acceptance_requires_evidence():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        for cid, name in [("caregiver-sarah", "Sarah"), ("caregiver-john", "John")]:
            client.post(
                "/api/v1/caregiver-profiles",
                json={
                    "person_id": "person-1",
                    "name": name,
                    "email": f"{name.lower()}@example.com",
                    "relationship": "Family",
                    "participation_category": "family",
                },
            )
        response = client.post(
            "/api/v1/caregiver-handoffs",
            json={
                "from_caregiver_id": "caregiver-sarah",
                "to_caregiver_id": "caregiver-john",
                "person_id": "person-1",
                "responsibility_type": "appointment",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["acceptance_status"] == "unconfirmed"
        assert data["is_completed"] is False
    finally:
        teardown_db()


def test_communication_not_handoff():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        for cid, name in [("caregiver-sarah", "Sarah"), ("caregiver-john", "John")]:
            client.post(
                "/api/v1/caregiver-profiles",
                json={
                    "person_id": "person-1",
                    "name": name,
                    "email": f"{name.lower()}@example.com",
                    "relationship": "Family",
                    "participation_category": "family",
                },
            )
        response = client.post(
            "/api/v1/caregiver-communications",
            json={
                "from_caregiver_id": "caregiver-sarah",
                "to_caregiver_id": "caregiver-john",
                "person_id": "person-1",
                "direction": "to_one",
                "content_summary": "Mom has an appointment tomorrow",
                "visibility": "restricted",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["direction"] == "to_one"
        assert data["content_summary"] == "Mom has an appointment tomorrow"
    finally:
        teardown_db()
