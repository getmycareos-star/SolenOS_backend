from datetime import datetime, timezone, timedelta
import hashlib
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, SessionLocal as DefaultSessionLocal
import app.main as main_module
from app.main import app
from app.models.anonymous_session import AnonymousSession
from app.models.caregiver_access import CaregiverAccessGrant
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
    main_module.SessionLocal = DefaultSessionLocal


TEST_TOKEN = "test-only-caregiver-intelligence-session-token"


def get_test_client(caregiver_id=None):
    from app.core.database import get_db
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestingSessionLocal
    db = TestingSessionLocal()
    try:
        query = db.query(Caregiver).filter(Caregiver.person_id == "person-1")
        caregiver = query.filter(Caregiver.id == caregiver_id).first() if caregiver_id else query.filter(Caregiver.id == "caregiver-sarah").first()
        if caregiver is None:
            caregiver = query.order_by(Caregiver.id.asc()).first()
        if caregiver is None:
            raise AssertionError("test fixture must create a caregiver before creating an authenticated session")
        grant = db.query(CaregiverAccessGrant).filter_by(person_id="person-1", caregiver_id=caregiver.id).first()
        if grant is None:
            db.add(CaregiverAccessGrant(
                person_id="person-1",
                caregiver_id=caregiver.id,
                role="primary" if caregiver.id == "caregiver-sarah" else "family",
                access_scope="full_record",
                status="active",
                can_manage_members=caregiver.id == "caregiver-sarah",
                permissions=["view_lcr", "add_evidence", "correct_records", "manage_responsibilities", "manage_reminders", "complete_assigned_reminders", "manage_handoffs", "share_summaries", "share_observations", "view_documents", "view_communications"],
                granted_by_caregiver_id="caregiver-sarah",
            ))
            db.flush()
        token = f"{TEST_TOKEN}-{caregiver.id}"
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        existing = db.query(AnonymousSession).filter(AnonymousSession.token_hash == token_hash).first()
        if existing is None:
            db.add(AnonymousSession(
                id=f"test-session-{caregiver.id}",
                person_id="person-1",
                caregiver_id=caregiver.id,
                token_hash=token_hash,
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
            ))
        # Persist the grant even when the deterministic bearer session already
        # exists. The old conditional commit left newly-added grants transient
        # for reused sessions, causing false 403s in authorization-aware tests.
        db.commit()
    finally:
        db.close()
    return TestClient(app, headers={"Authorization": f"Bearer {token}"})


def create_test_profile(caregiver_id, name):
    actor_client = get_test_client(caregiver_id)
    response = actor_client.post(
        "/api/v1/caregiver-profiles",
        json={
            "person_id": "person-1",
            "name": name,
            "email": f"{name.lower()}@example.com",
            "relationship": "Family",
            "participation_category": "family",
        },
    )
    assert response.status_code == 200, response.text
    return actor_client, response.json()["id"]


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



def test_reused_test_session_persists_a_newly_created_access_grant():
    setup_db()
    try:
        db = TestingSessionLocal()
        create_test_person_and_caregivers(db)
        db.close()

        get_test_client()
        db = TestingSessionLocal()
        db.query(CaregiverAccessGrant).filter_by(
            person_id="person-1", caregiver_id="caregiver-sarah"
        ).delete()
        db.commit()
        db.close()

        client = get_test_client()
        response = client.get("/api/v1/caregiver-profiles/person-1")
        assert response.status_code == 200, response.text
        db = TestingSessionLocal()
        try:
            grant = db.query(CaregiverAccessGrant).filter_by(
                person_id="person-1", caregiver_id="caregiver-sarah"
            ).one()
            assert grant.status == "active"
            assert grant.access_scope == "full_record"
        finally:
            db.close()
    finally:
        teardown_db()


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
        assert response.status_code == 200, response.text
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
        profile_response = client.post(
            "/api/v1/caregiver-profiles",
            json={
                "person_id": "person-1",
                "name": "Sarah",
                "email": "sarah@example.com",
                "relationship": "Daughter",
                "participation_category": "family",
            },
        )
        assert profile_response.status_code == 200, profile_response.text
        profile_id = profile_response.json()["id"]
        response = client.post(
            "/api/v1/caregiver-observations",
            json={
                "caregiver_id": profile_id,
                "person_id": "person-1",
                "observation_content": "Mom seemed more confused today",
                "observation_type": "direct",
                "event_time": datetime.now(timezone.utc).isoformat(),
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["caregiver_id"] == profile_id
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
        actor_clients = {}
        profile_ids = {}
        for cid, name in [("caregiver-sarah", "Sarah"), ("caregiver-john", "John")]:
            actor_clients[cid], profile_ids[cid] = create_test_profile(cid, name)

        actor_clients["caregiver-sarah"].post(
            "/api/v1/caregiver-perspectives",
            json={
                "caregiver_id": profile_ids["caregiver-sarah"],
                "person_id": "person-1",
                "claim_type": "observation",
                "claim_content": "Mom fell yesterday",
            },
        )
        actor_clients["caregiver-john"].post(
            "/api/v1/caregiver-perspectives",
            json={
                "caregiver_id": profile_ids["caregiver-john"],
                "person_id": "person-1",
                "claim_type": "observation",
                "claim_content": "Mom did not fall",
            },
        )
        client.post(
            "/api/v1/perspective-conflicts",
            json={
                "person_id": "person-1",
                "caregiver_a_id": profile_ids["caregiver-sarah"],
                "caregiver_b_id": profile_ids["caregiver-john"],
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
        assert (conflicts[0]["caregiver_a_id"] == profile_ids["caregiver-sarah"] and conflicts[0]["caregiver_b_id"] == profile_ids["caregiver-john"]) or (conflicts[0]["caregiver_a_id"] == profile_ids["caregiver-john"] and conflicts[0]["caregiver_b_id"] == profile_ids["caregiver-sarah"])
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
        profile_response = client.post(
            "/api/v1/caregiver-profiles",
            json={
                "person_id": "person-1",
                "name": "Sarah",
                "email": "sarah@example.com",
                "relationship": "Daughter",
                "participation_category": "family",
            },
        )
        assert profile_response.status_code == 200, profile_response.text
        profile_id = profile_response.json()["id"]
        obs_response = client.post(
            "/api/v1/caregiver-observations",
            json={
                "caregiver_id": profile_id,
                "person_id": "person-1",
                "observation_content": "Mom fell yesterday",
                "observation_type": "direct",
            },
        )
        obs_id = obs_response.json()["id"]
        client.post(f"/api/v1/caregiver-observations/{obs_id}/promote", params={"caregiver_id": profile_id, "person_id": "person-1"})
        response = client.get("/api/v1/caregiver-observations/person-1")
        assert response.status_code == 200
        shared = response.json()
        assert len(shared) == 1
        assert shared[0]["caregiver_id"] == profile_id
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
        profile_response = client.post(
            "/api/v1/caregiver-profiles",
            json={
                "person_id": "person-1",
                "name": "Sarah",
                "email": "sarah@example.com",
                "relationship": "Daughter",
                "participation_category": "family",
            },
        )
        assert profile_response.status_code == 200, profile_response.text
        profile_id = profile_response.json()["id"]
        past = datetime.now(timezone.utc) - timedelta(days=30)
        future = datetime.now(timezone.utc) + timedelta(days=30)
        client.post(
            "/api/v1/caregiver-responsibilities",
            json={
                "caregiver_id": profile_id,
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
        response = client.get(f"/api/v1/caregiver-responsibilities/history/{profile_id}", params={"person_id": "person-1"})
        assert response.status_code == 200
        history = response.json()
        assert len(history) == 2

        # Caller-controlled person IDs must not disclose another care record.
        assert client.get("/api/v1/caregiver-roles/other-person").status_code == 403
        assert client.get("/api/v1/caregiver-responsibilities/other-person").status_code == 403
        assert client.get(
            f"/api/v1/caregiver-responsibilities/history/{profile_id}",
            params={"person_id": "other-person"},
        ).status_code == 403
    finally:
        teardown_db()


def test_no_caregiver_reliability_scores():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        actor_clients = {}
        profile_ids = {}
        for cid, name in [("caregiver-sarah", "Sarah"), ("caregiver-john", "John")]:
            actor_clients[cid], profile_ids[cid] = create_test_profile(cid, name)
        for _ in range(5):
            actor_clients["caregiver-sarah"].post(
                "/api/v1/caregiver-activities",
                json={
                    "caregiver_id": profile_ids["caregiver-sarah"],
                    "person_id": "person-1",
                    "activity_type": "performed_task",
                    "description": "Administered medication",
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        for _ in range(2):
            actor_clients["caregiver-john"].post(
                "/api/v1/caregiver-activities",
                json={
                    "caregiver_id": profile_ids["caregiver-john"],
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
        actor_clients = {}
        profile_ids = {}
        for cid, name in [("caregiver-sarah", "Sarah"), ("caregiver-john", "John")]:
            actor_clients[cid], profile_ids[cid] = create_test_profile(cid, name)
        actor_clients["caregiver-sarah"].post(
            "/api/v1/caregiver-responsibilities",
            json={
                "caregiver_id": profile_ids["caregiver-sarah"],
                "person_id": "person-1",
                "responsibility_type": "medication",
                "effective_start": datetime.now(timezone.utc).isoformat(),
            },
        )
        actor_clients["caregiver-john"].post(
            "/api/v1/caregiver-responsibilities",
            json={
                "caregiver_id": profile_ids["caregiver-john"],
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

        # The UI uses the session-scoped endpoint so a stale browser person ID
        # cannot cause a false 403 or be used to select another care record.
        scoped_response = client.get("/api/v1/multi-caregiver-summary")
        assert scoped_response.status_code == 200, scoped_response.text
        assert scoped_response.json()["person_id"] == "person-1"
        assert len(scoped_response.json()["caregivers"]) == 2

        state_response = client.get("/api/v1/care-network-state")
        assert state_response.status_code == 200, state_response.text
        assert state_response.json()["person_id"] == "person-1"
        assert client.get("/api/v1/caregiver-communications").status_code == 200
        assert client.get("/api/v1/caregiver-contexts").status_code == 200
        assert client.get("/api/v1/caregiver-roles/other-person").status_code == 403
        assert client.get("/api/v1/caregiver-responsibilities/other-person").status_code == 403
        assert client.post(
            "/api/v1/unknown-actor-activity",
            params={
                "person_id": "other-person",
                "activity_type": "performed_task",
                "description": "Should not be recorded outside this care record",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            },
        ).status_code == 403
    finally:
        teardown_db()


def test_care_network_state():
    setup_db()
    try:
        db = TestingSessionLocal()
        person, sarah, john = create_test_person_and_caregivers(db)
        db.close()

        client = get_test_client()
        actor_clients = {}
        profile_ids = {}
        for cid, name in [("caregiver-sarah", "Sarah"), ("caregiver-john", "John")]:
            actor_clients[cid], profile_ids[cid] = create_test_profile(cid, name)
        actor_clients["caregiver-sarah"].post(
            "/api/v1/caregiver-roles",
            json={
                "caregiver_id": profile_ids["caregiver-sarah"],
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
        actor_clients = {}
        profile_ids = {}
        for cid, name in [("caregiver-sarah", "Sarah"), ("caregiver-john", "John")]:
            actor_clients[cid], profile_ids[cid] = create_test_profile(cid, name)
        response = actor_clients["caregiver-sarah"].post(
            "/api/v1/duplicate-actions",
            json={
                "person_id": "person-1",
                "caregiver_a_id": profile_ids["caregiver-sarah"],
                "caregiver_b_id": profile_ids["caregiver-john"],
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

def test_responsibility_gap_cannot_be_created_as_already_resolved():
    setup_db()
    try:
        db = TestingSessionLocal()
        create_test_person_and_caregivers(db)
        db.close()
        client = get_test_client()
        response = client.post(
            "/api/v1/responsibility-gaps",
            json={
                "person_id": "person-1",
                "responsibility_type": "medication",
                "status": "resolved",
                "resolution": "Marked complete without review",
                "resolved_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        assert response.status_code == 422, response.text
    finally:
        teardown_db()


def test_responsibility_gap_last_known_caregiver_must_belong_to_same_person():
    from app.models.caregiver_intelligence import CaregiverProfile as CaregiverProfileModel

    setup_db()
    try:
        db = TestingSessionLocal()
        create_test_person_and_caregivers(db)
        db.add(Person(id="person-2", name="Unrelated person"))
        db.add(Caregiver(id="caregiver-other-person", name="Other", email="other@example.com", relationship="Family", person_id="person-2"))
        db.flush()
        db.add(CaregiverProfileModel(id="profile-other-person", person_id="person-2", name="Other", relationship="Family"))
        db.commit()
        db.close()
        client = get_test_client()
        response = client.post(
            "/api/v1/responsibility-gaps",
            json={
                "person_id": "person-1",
                "responsibility_type": "medication",
                "last_known_caregiver_id": "profile-other-person",
            },
        )
        assert response.status_code == 404, response.text
    finally:
        teardown_db()


def test_perspective_conflict_cannot_be_created_as_resolved():
    setup_db()
    try:
        db = TestingSessionLocal()
        create_test_person_and_caregivers(db)
        db.close()
        _, caregiver_a = create_test_profile("caregiver-sarah", "Sarah")
        client_b, caregiver_b = create_test_profile("caregiver-john", "John")
        response = client_b.post(
            "/api/v1/perspective-conflicts",
            json={
                "person_id": "person-1",
                "caregiver_a_id": caregiver_a,
                "caregiver_b_id": caregiver_b,
                "conflict_type": "observation_conflict",
                "claim_a": "One caregiver reported an event.",
                "claim_b": "Another caregiver reported a different account.",
                "status": "resolved",
                "resolution": "Automatically selected one report.",
                "resolved_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        assert response.status_code == 422, response.text
    finally:
        teardown_db()


def test_perspective_conflict_rejects_caregiver_from_another_person():
    from app.models.caregiver_intelligence import CaregiverProfile as CaregiverProfileModel

    setup_db()
    try:
        db = TestingSessionLocal()
        create_test_person_and_caregivers(db)
        db.add(Person(id="person-2", name="Unrelated person"))
        db.add(Caregiver(id="caregiver-other-person", name="Other", email="other@example.com", relationship="Family", person_id="person-2"))
        db.flush()
        db.add(CaregiverProfileModel(id="profile-other-person", person_id="person-2", name="Other", relationship="Family"))
        db.commit()
        db.close()
        client, caregiver_a = create_test_profile("caregiver-sarah", "Sarah")
        response = client.post(
            "/api/v1/perspective-conflicts",
            json={
                "person_id": "person-1",
                "caregiver_a_id": caregiver_a,
                "caregiver_b_id": "profile-other-person",
                "conflict_type": "observation_conflict",
                "claim_a": "First report.",
                "claim_b": "Second report.",
                "status": "unresolved",
            },
        )
        assert response.status_code == 404, response.text
    finally:
        teardown_db()

def test_private_perspectives_do_not_generate_or_expose_network_conflicts():
    setup_db()
    try:
        db = TestingSessionLocal()
        create_test_person_and_caregivers(db)
        db.close()
        sarah_client, sarah_profile = create_test_profile("caregiver-sarah", "Sarah")
        john_client, john_profile = create_test_profile("caregiver-john", "John")
        for client, profile_id, claim in [
            (sarah_client, sarah_profile, "Mom fell yesterday"),
            (john_client, john_profile, "Mom did not fall"),
        ]:
            response = client.post(
                "/api/v1/caregiver-perspectives",
                json={
                    "caregiver_id": profile_id,
                    "person_id": "person-1",
                    "claim_type": "observation",
                    "claim_content": claim,
                },
            )
            assert response.status_code == 200, response.text
        summary = sarah_client.get("/api/v1/multi-caregiver-summary")
        assert summary.status_code == 200, summary.text
        assert summary.json()["perspective_conflicts"] == []
        direct = sarah_client.get("/api/v1/perspective-conflicts/person-1")
        assert direct.status_code == 200, direct.text
        assert direct.json() == []
        assert "Mom fell yesterday" not in summary.text
        assert "Mom did not fall" not in summary.text
    finally:
        teardown_db()


def test_shared_perspectives_are_detected_only_after_both_caregivers_promote_them():
    setup_db()
    try:
        db = TestingSessionLocal()
        create_test_person_and_caregivers(db)
        db.close()
        sarah_client, sarah_profile = create_test_profile("caregiver-sarah", "Sarah")
        john_client, john_profile = create_test_profile("caregiver-john", "John")
        first = sarah_client.post(
            "/api/v1/caregiver-perspectives",
            json={
                "caregiver_id": sarah_profile,
                "person_id": "person-1",
                "claim_type": "observation",
                "claim_content": "Mom fell yesterday",
            },
        )
        second = john_client.post(
            "/api/v1/caregiver-perspectives",
            json={
                "caregiver_id": john_profile,
                "person_id": "person-1",
                "claim_type": "observation",
                "claim_content": "Mom did not fall",
            },
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert sarah_client.get("/api/v1/multi-caregiver-summary").json()["perspective_conflicts"] == []
        promote_first = sarah_client.post(f"/api/v1/caregiver-perspectives/{first.json()['id']}/promote")
        assert promote_first.status_code == 200, promote_first.text
        assert sarah_client.get("/api/v1/multi-caregiver-summary").json()["perspective_conflicts"] == []
        promote_second = john_client.post(f"/api/v1/caregiver-perspectives/{second.json()['id']}/promote")
        assert promote_second.status_code == 200, promote_second.text
        summary = sarah_client.get("/api/v1/multi-caregiver-summary")
        assert summary.status_code == 200, summary.text
        conflicts = summary.json()["perspective_conflicts"]
        assert len(conflicts) == 1
        assert conflicts[0]["status"] == "conflicting"
        assert {conflicts[0]["claim_a"], conflicts[0]["claim_b"]} == {
            "Mom fell yesterday",
            "Mom did not fall",
        }
    finally:
        teardown_db()

def test_responsibility_gap_review_is_persisted_and_audited():
    from app.models.privacy_delivery import AuditLogEvent

    setup_db()
    try:
        db = TestingSessionLocal()
        create_test_person_and_caregivers(db)
        db.close()
        client = get_test_client()
        created = client.post(
            "/api/v1/responsibility-gaps",
            json={"person_id": "person-1", "responsibility_type": "medication"},
        )
        assert created.status_code == 200, created.text
        gap_id = created.json()["id"]
        reviewed = client.post(
            f"/api/v1/responsibility-gaps/{gap_id}/review",
            json={
                "status": "partially_covered",
                "resolution": "One caregiver covers weekdays; weekends remain unassigned.",
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["status"] == "partially_covered"
        listed = client.get("/api/v1/responsibility-gaps/person-1")
        assert listed.status_code == 200
        assert any(item["id"] == gap_id for item in listed.json())
        db = TestingSessionLocal()
        try:
            event = db.query(AuditLogEvent).filter_by(
                target_type="responsibility_gap",
                target_id=gap_id,
                action="caregiver_responsibility_gap_reviewed",
            ).one()
            assert event.record_metadata["new_status"] == "partially_covered"
        finally:
            db.close()
    finally:
        teardown_db()


def test_shared_perspective_conflict_resolution_preserves_both_reports_and_audits():
    from app.models.privacy_delivery import AuditLogEvent

    setup_db()
    try:
        db = TestingSessionLocal()
        create_test_person_and_caregivers(db)
        db.close()
        sarah_client, sarah_profile = create_test_profile("caregiver-sarah", "Sarah")
        john_client, john_profile = create_test_profile("caregiver-john", "John")
        created = sarah_client.post(
            "/api/v1/perspective-conflicts",
            json={
                "person_id": "person-1",
                "caregiver_a_id": sarah_profile,
                "caregiver_b_id": john_profile,
                "conflict_type": "observation_conflict",
                "claim_a": "Mom fell yesterday",
                "claim_b": "Mom did not fall",
                "status": "unresolved",
            },
        )
        assert created.status_code == 200, created.text
        conflict_id = created.json()["id"]
        reviewed = john_client.post(
            f"/api/v1/perspective-conflicts/{conflict_id}/resolve",
            json={"resolution": "Reports refer to different observation periods; retain both accounts."},
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["status"] == "resolved"
        assert reviewed.json()["claim_a"] == "Mom fell yesterday"
        assert reviewed.json()["claim_b"] == "Mom did not fall"
        assert sarah_client.get("/api/v1/perspective-conflicts/person-1").json() == []
        db = TestingSessionLocal()
        try:
            event = db.query(AuditLogEvent).filter_by(
                target_type="perspective_conflict",
                target_id=conflict_id,
                action="caregiver_perspective_conflict_resolved",
            ).one()
            assert event.record_metadata["new_status"] == "resolved"
        finally:
            db.close()
    finally:
        teardown_db()


def test_duplicate_action_review_is_persisted_and_audited():
    from app.models.privacy_delivery import AuditLogEvent

    setup_db()
    try:
        db = TestingSessionLocal()
        create_test_person_and_caregivers(db)
        db.close()
        sarah_client, sarah_profile = create_test_profile("caregiver-sarah", "Sarah")
        john_client, john_profile = create_test_profile("caregiver-john", "John")
        created = sarah_client.post(
            "/api/v1/duplicate-actions",
            json={
                "person_id": "person-1",
                "caregiver_a_id": sarah_profile,
                "caregiver_b_id": john_profile,
                "action_type": "appointment_booking",
                "action_description_a": "Booked the neurology appointment.",
                "action_description_b": "Booked the same neurology appointment.",
            },
        )
        assert created.status_code == 200, created.text
        duplicate_id = created.json()["id"]
        reviewed = john_client.post(
            f"/api/v1/duplicate-actions/{duplicate_id}/review",
            json={
                "status": "confirmed_duplicate",
                "resolution": "Both reports refer to the same appointment booking.",
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["status"] == "confirmed_duplicate"
        assert reviewed.json()["resolution"] == "Both reports refer to the same appointment booking."
        db = TestingSessionLocal()
        try:
            event = db.query(AuditLogEvent).filter_by(
                target_type="duplicate_action",
                target_id=duplicate_id,
                action="caregiver_duplicate_action_reviewed",
            ).one()
            assert event.record_metadata["new_status"] == "confirmed_duplicate"
        finally:
            db.close()
    finally:
        teardown_db()

def test_session_derived_caregiver_network_reads_use_bearer_scope():
    setup_db()
    try:
        db = TestingSessionLocal()
        create_test_person_and_caregivers(db)
        db.close()
        client = get_test_client()
        summary = client.get("/api/v1/multi-caregiver-summary")
        assert summary.status_code == 200, summary.text
        state = client.get("/api/v1/care-network-state")
        assert state.status_code == 200, state.text
        path_summary = client.get("/api/v1/multi-caregiver-summary/person-1")
        assert path_summary.status_code == 200, path_summary.text
        path_state = client.get("/api/v1/care-network-state/person-1")
        assert path_state.status_code == 200, path_state.text
        cross_record = client.get(
            "/api/v1/multi-caregiver-summary",
            params={"person_id": "another-person"},
        )
        assert cross_record.status_code == 403, cross_record.text
        cross_record_path = client.get("/api/v1/multi-caregiver-summary/another-person")
        assert cross_record_path.status_code == 403, cross_record_path.text
        cross_state_path = client.get("/api/v1/care-network-state/another-person")
        assert cross_state_path.status_code == 403, cross_state_path.text
        cross_state = client.get(
            "/api/v1/care-network-state",
            params={"person_id": "another-person"},
        )
        assert cross_state.status_code == 403, cross_state.text
    finally:
        teardown_db()

