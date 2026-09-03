from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app
from app.models.care import Person, Caregiver, Evidence
from app.models.action import Action as ActionModel
from app.services.action_extraction import ActionExtractionEngine
from app.services.action_normalization import ActionNormalizationService
from app.services.action_lifecycle import ActionLifecycleService, ActionIdentityService
from app.schemas.action import ActionCandidate

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
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def test_action_supersession():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        action = ActionModel(
            id="act1", person_id="p1", status="active", normalized_action="Continue medication X",
            source_evidence_id="ev1", source_passage="Continue medication X", original_text="Continue medication X",
            created_by_caregiver_id="c1",
        )
        db.add(action)
        db.commit()

        lifecycle = ActionLifecycleService()
        result = lifecycle.supersede_action(action, "new_action_id", db)
        assert result.status == "superseded"
        assert result.superseded_by_action_id == "new_action_id"
        db.close()
    finally:
        teardown_db()


def test_action_cancellation():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        action = ActionModel(
            id="act1", person_id="p1", status="active", normalized_action="Schedule appointment",
            source_evidence_id="ev1", source_passage="Schedule appointment", original_text="Schedule appointment",
            created_by_caregiver_id="c1",
        )
        db.add(action)
        db.commit()

        lifecycle = ActionLifecycleService()
        result = lifecycle.cancel_action(action, db)
        assert result.status == "cancelled"
        assert result.cancelled_at is not None
        db.close()
    finally:
        teardown_db()


def test_action_expiration():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        action = ActionModel(
            id="act1", person_id="p1", status="active", normalized_action="Schedule appointment",
            source_evidence_id="ev1", source_passage="Schedule appointment", original_text="Schedule appointment",
            deadline=datetime.now(timezone.utc) - timedelta(days=1), created_by_caregiver_id="c1",
        )
        db.add(action)
        db.commit()

        lifecycle = ActionLifecycleService()
        assert lifecycle.check_expiration(action) is True
        db.close()
    finally:
        teardown_db()


def test_action_status_transitions():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        action = ActionModel(
            id="act1", person_id="p1", status="detected", normalized_action="Schedule appointment",
            source_evidence_id="ev1", source_passage="Schedule appointment", original_text="Schedule appointment",
            created_by_caregiver_id="c1",
        )
        db.add(action)
        db.commit()

        lifecycle = ActionLifecycleService()
        result = lifecycle.transition_status(action, "pending_confirmation", db)
        assert result.status == "pending_confirmation"

        result = lifecycle.transition_status(result, "active", db)
        assert result.status == "active"
        db.close()
    finally:
        teardown_db()


def test_invalid_status_transition_raises():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        action = ActionModel(
            id="act1", person_id="p1", status="completed", normalized_action="Schedule appointment",
            source_evidence_id="ev1", source_passage="Schedule appointment", original_text="Schedule appointment",
            created_by_caregiver_id="c1",
        )
        db.add(action)
        db.commit()

        lifecycle = ActionLifecycleService()
        try:
            lifecycle.transition_status(action, "active", db)
            assert False, "Should have raised ValueError"
        except ValueError:
            pass
        db.close()
    finally:
        teardown_db()


def test_duplicate_prevention():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        action_a = ActionModel(
            id="a1", person_id="p1", action_type="schedule", action_object="cardiology follow-up",
            normalized_action="Schedule cardiology follow-up", is_explicit=True,
            status="active", source_evidence_id="ev1", source_passage="Schedule cardiology follow-up",
            original_text="Schedule cardiology follow-up", created_by_caregiver_id="c1",
        )
        action_b = ActionModel(
            id="b1", person_id="p1", action_type="schedule", action_object="cardiology follow-up",
            normalized_action="Schedule cardiology follow-up", is_explicit=True,
            status="active", source_evidence_id="ev2", source_passage="Schedule cardiology follow-up",
            original_text="Schedule cardiology follow-up", created_by_caregiver_id="c1",
        )
        db.add_all([action_a, action_b])
        db.commit()

        identity = ActionIdentityService()
        result = identity.is_duplicate(action_a, action_b)
        assert result["is_duplicate"] is True
        assert result["match_type"] == "exact"
        db.close()
    finally:
        teardown_db()


def test_near_duplicate_detection():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        action_a = ActionModel(
            id="a1", person_id="p1", action_type="schedule", action_object="cardiology follow-up",
            normalized_action="Schedule cardiology follow-up", is_explicit=True,
            status="active", source_evidence_id="ev1", source_passage="Schedule cardiology follow-up",
            original_text="Schedule cardiology follow-up", created_by_caregiver_id="c1",
        )
        action_b = ActionModel(
            id="b1", person_id="p1", action_type="schedule", action_object="cardiology follow-up",
            normalized_action="Schedule cardiology follow-up", is_explicit=True,
            status="active", source_evidence_id="ev2", source_passage="Schedule cardiology follow-up",
            original_text="Schedule cardiology follow-up", created_by_caregiver_id="c1",
        )
        db.add_all([action_a, action_b])
        db.commit()

        identity = ActionIdentityService()
        result = identity.is_duplicate(action_a, action_b)
        assert result["is_duplicate"] is True
        db.close()
    finally:
        teardown_db()


def test_completion_evidence_strength():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="person-1", name="Test Person")
        caregiver = Caregiver(
            id="caregiver-1",
            name="Test Caregiver",
            email="test1@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        action = ActionModel(
            id="act1", person_id=person.id, status="active", normalized_action="Schedule appointment",
            source_evidence_id="ev1", source_passage="Schedule appointment", original_text="Schedule appointment",
            created_by_caregiver_id=caregiver.id,
        )
        db.add(action)
        db.commit()

        strong_evidence = Evidence(
            id="ev1", person_id=person.id, type="appointment_record",
            source_text="Appointment completed", uploaded_by_caregiver_id=caregiver.id,
        )
        db.add(strong_evidence)
        db.commit()

        lifecycle = ActionLifecycleService()
        result = lifecycle.verify_completion_evidence(action, ["ev1"], db)
        assert result["verified"] is True
        assert result["confidence"] >= 0.5

        weak_evidence = Evidence(
            id="ev2", person_id=person.id, type="patient_report",
            source_text="I think I went", uploaded_by_caregiver_id=caregiver.id,
        )
        db.add(weak_evidence)
        db.commit()
        result = lifecycle.verify_completion_evidence(action, ["ev2"], db)
        assert result["confidence"] < 0.7
        db.close()
    finally:
        teardown_db()


def test_self_reported_vs_documented_completion():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="person-1", name="Test Person")
        caregiver = Caregiver(
            id="caregiver-1",
            name="Test Caregiver",
            email="test2@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        action = ActionModel(
            id="act1", person_id=person.id, status="active", normalized_action="Schedule appointment",
            source_evidence_id="ev1", source_passage="Schedule appointment", original_text="Schedule appointment",
            created_by_caregiver_id=caregiver.id,
        )
        db.add(action)
        db.commit()

        self_report = Evidence(
            id="ev1", person_id=person.id, type="caregiver_report",
            source_text="I scheduled the appointment", uploaded_by_caregiver_id=caregiver.id,
        )
        db.add(self_report)
        db.commit()

        lifecycle = ActionLifecycleService()
        result = lifecycle.verify_completion_evidence(action, ["ev1"], db)
        assert result["verified"] is True
        assert result["confidence"] == 0.6
        db.close()
    finally:
        teardown_db()


def test_no_completion_evidence_means_unknown():
    action = ActionModel(
        id="act1", person_id="p1", status="active", normalized_action="Schedule appointment",
        source_evidence_id="ev1", source_passage="Schedule appointment", original_text="Schedule appointment",
        created_by_caregiver_id="c1",
    )
    assert action.status != "completed"
    assert action.status != "expired"


def test_medication_safety_strict():
    normalizer = ActionNormalizationService()

    candidate = ActionCandidate(
        source_evidence_id="ev", source_passage="Medication X appears on the current medication list.",
        original_text="Medication X appears on the current medication list.", normalized_action="Continue medication X",
        modality="required", action_type="continue", is_medication_action=False,
        extraction_confidence=0.9,
    )
    safety = normalizer.safety_check(candidate)
    assert safety.is_safe_to_create is False
    assert "medication mention" in safety.blocked_reason.lower()

    candidate2 = ActionCandidate(
        source_evidence_id="ev", source_passage="Patient has diabetes.",
        original_text="Patient has diabetes.", normalized_action="Schedule endocrinology appointment",
        modality="required", action_type="schedule", is_explicit=False,
        extraction_confidence=0.3,
    )
    safety2 = normalizer.safety_check(candidate2)
    assert safety2.is_safe_to_create is False


def test_conditional_action_not_flattened():
    text = "If fever develops, contact the physician."
    engine = ActionExtractionEngine()
    ref_date = datetime(2026, 8, 27, tzinfo=timezone.utc)
    candidates = engine.extract(text, "ev", reference_date=ref_date)
    assert len(candidates) == 1
    assert candidates[0].has_condition is True
    assert candidates[0].modality == "conditional"
    assert candidates[0].status == "conditional"
    assert candidates[0].condition_text is not None


def test_recurring_action_single_instance():
    text = "Check blood pressure twice daily for 14 days."
    engine = ActionExtractionEngine()
    ref_date = datetime(2026, 8, 27, tzinfo=timezone.utc)
    candidates = engine.extract(text, "ev", reference_date=ref_date)
    assert len(candidates) == 1
    assert candidates[0].is_recurring is True
    assert candidates[0].recurrence == "twice daily"


def test_stale_action_not_marked_failed():
    action = ActionModel(
        id="act1", person_id="p1", status="active", normalized_action="Schedule appointment",
        source_evidence_id="ev1", source_passage="Schedule appointment", original_text="Schedule appointment",
        deadline=datetime.now(timezone.utc) - timedelta(days=90), created_by_caregiver_id="c1",
    )
    assert action.status != "completed"
    assert action.status != "expired"


def test_action_identity_evolution():
    identity = ActionIdentityService()
    action_a = ActionModel(
        id="a1", person_id="p1", action_type="schedule", action_object="cardiology follow-up",
        normalized_action="Schedule cardiology follow-up", is_explicit=True,
    )
    action_b = ActionModel(
        id="b1", person_id="p1", action_type="schedule", action_object="cardiology follow-up",
        normalized_action="Schedule cardiology follow-up", is_explicit=True,
    )
    result = identity.is_duplicate(action_a, action_b)
    assert result["is_duplicate"] is True
    assert result["match_type"] == "exact"


def test_source_meaning_preservation():
    normalizer = ActionNormalizationService()

    candidate = ActionCandidate(
        source_evidence_id="ev", source_passage="Consider scheduling follow-up.",
        original_text="Consider scheduling follow-up.", normalized_action="Schedule follow-up",
        modality="required", action_type="schedule", is_explicit=False,
        extraction_confidence=0.5,
    )
    normalized = normalizer.validate_and_normalize(candidate)
    assert "Recommended" in normalized.normalized_action or "recommended" in normalized.normalized_action.lower()


def test_actor_not_silently_assigned():
    text = "Schedule follow-up appointment."
    engine = ActionExtractionEngine()
    ref_date = datetime(2026, 8, 27, tzinfo=timezone.utc)
    candidates = engine.extract(text, "ev", reference_date=ref_date)
    assert len(candidates) >= 1
    assert candidates[0].actor_type == "unknown"
    assert candidates[0].requires_confirmation is True


def test_document_date_vs_action_date():
    text = "Follow-up appointment on September 14. Schedule within two weeks of discharge."
    engine = ActionExtractionEngine()
    ref_date = datetime(2026, 8, 27, tzinfo=timezone.utc)
    candidates = engine.extract(text, "ev", reference_date=ref_date)
    assert len(candidates) >= 1
    for c in candidates:
        if c.deadline:
            assert c.deadline.year == 2026
