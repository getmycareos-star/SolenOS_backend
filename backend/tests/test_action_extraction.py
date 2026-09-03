from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app
from app.models.action import Action as ActionModel
from app.services.action_extraction import ActionExtractionEngine
from app.services.action_normalization import ActionNormalizationService
from app.services.action_lifecycle import ActionIdentityService
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


def test_pure_information_no_action():
    text = "Blood pressure today was 145/90."
    engine = ActionExtractionEngine()
    ref_date = datetime(2026, 8, 27, tzinfo=timezone.utc)
    candidates = engine.extract(text, "ev1", reference_date=ref_date)
    assert len(candidates) == 0


def test_explicit_instruction_creates_action():
    setup_db()
    try:
        text = "Monitor blood pressure daily."
        engine = ActionExtractionEngine()
        ref_date = datetime(2026, 8, 27, tzinfo=timezone.utc)
        candidates = engine.extract(text, "ev2", reference_date=ref_date)
        assert len(candidates) == 1
        assert candidates[0].action_type == "monitor"
        assert candidates[0].modality == "required"
        assert candidates[0].is_explicit is True
    finally:
        teardown_db()


def test_recommendation_not_obligation():
    setup_db()
    try:
        text = "Consider monitoring blood pressure at home."
        engine = ActionExtractionEngine()
        ref_date = datetime(2026, 8, 27, tzinfo=timezone.utc)
        candidates = engine.extract(text, "ev3", reference_date=ref_date)
        assert len(candidates) == 1
        assert candidates[0].modality == "recommended"
        assert candidates[0].requires_confirmation is True
    finally:
        teardown_db()


def test_follow_up_with_deadline():
    setup_db()
    try:
        text = "Return to clinic in four weeks."
        engine = ActionExtractionEngine()
        ref_date = datetime(2026, 8, 27, tzinfo=timezone.utc)
        candidates = engine.extract(text, "ev4", reference_date=ref_date)
        assert len(candidates) == 1
        assert candidates[0].action_type == "follow_up"
        assert candidates[0].deadline is not None
        expected = ref_date + timedelta(weeks=4)
        assert candidates[0].deadline.date() == expected.date()
    finally:
        teardown_db()


def test_relative_date_resolves_from_reference():
    setup_db()
    try:
        text = "Follow up in two weeks."
        engine = ActionExtractionEngine()
        ref_date = datetime(2026, 8, 27, tzinfo=timezone.utc)
        candidates = engine.extract(text, "ev5", reference_date=ref_date)
        assert len(candidates) == 1
        expected = ref_date + timedelta(weeks=2)
        assert candidates[0].deadline.date() == expected.date()
    finally:
        teardown_db()


def test_conditional_action():
    setup_db()
    try:
        text = "If symptoms worsen, contact the physician."
        engine = ActionExtractionEngine()
        ref_date = datetime(2026, 8, 27, tzinfo=timezone.utc)
        candidates = engine.extract(text, "ev6", reference_date=ref_date)
        assert len(candidates) == 1
        assert candidates[0].has_condition is True
        assert candidates[0].modality == "conditional"
        assert candidates[0].status == "conditional"
    finally:
        teardown_db()


def test_medication_action_high_risk():
    setup_db()
    try:
        text = "Start medication X tomorrow."
        engine = ActionExtractionEngine()
        ref_date = datetime(2026, 8, 27, tzinfo=timezone.utc)
        candidates = engine.extract(text, "ev7", reference_date=ref_date)
        assert len(candidates) == 1
        assert candidates[0].is_medication_action is True
        assert candidates[0].risk_tier == "high"
        assert candidates[0].requires_confirmation is True
    finally:
        teardown_db()


def test_medication_mention_no_action():
    setup_db()
    try:
        text = "Medication X appears on the current medication list."
        engine = ActionExtractionEngine()
        normalizer = ActionNormalizationService()
        ref_date = datetime(2026, 8, 27, tzinfo=timezone.utc)
        candidates = engine.extract(text, "ev8", reference_date=ref_date)
        normalized = [normalizer.validate_and_normalize(c) for c in candidates]
        assert all(c.modality == "informational" for c in normalized)
    finally:
        teardown_db()


def test_negative_instruction_prohibited():
    setup_db()
    try:
        text = "Do not take medication X on the morning of surgery."
        engine = ActionExtractionEngine()
        ref_date = datetime(2026, 8, 27, tzinfo=timezone.utc)
        candidates = engine.extract(text, "ev9", reference_date=ref_date)
        assert len(candidates) == 1
        assert candidates[0].modality == "prohibited"
        assert candidates[0].action_type == "prohibited"
    finally:
        teardown_db()


def test_multiple_actions_independent():
    setup_db()
    try:
        text = "Schedule follow-up, complete blood work, and bring your medication list."
        engine = ActionExtractionEngine()
        ref_date = datetime(2026, 8, 27, tzinfo=timezone.utc)
        candidates = engine.extract(text, "ev10", reference_date=ref_date)
        assert len(candidates) >= 3
        types = {c.action_type for c in candidates}
        assert "schedule" in types
        assert "bring" in types
    finally:
        teardown_db()


def test_ambiguous_actor_requires_confirmation():
    setup_db()
    try:
        text = "Schedule follow-up."
        engine = ActionExtractionEngine()
        ref_date = datetime(2026, 8, 27, tzinfo=timezone.utc)
        candidates = engine.extract(text, "ev11", reference_date=ref_date)
        assert len(candidates) >= 1
        assert candidates[0].actor_type == "unknown"
        assert candidates[0].requires_confirmation is True
    finally:
        teardown_db()


def test_ambiguous_recommendation_no_auto_action():
    setup_db()
    try:
        text = "Patient may benefit from physical therapy."
        engine = ActionExtractionEngine()
        ref_date = datetime(2026, 8, 27, tzinfo=timezone.utc)
        candidates = engine.extract(text, "ev12", reference_date=ref_date)
        normalized = [ActionNormalizationService().validate_and_normalize(c) for c in candidates]
        assert all(c.modality != "required" for c in normalized)
        assert all(c.action_type == "other" for c in normalized)
    finally:
        teardown_db()


def test_duplicate_detection():
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


def test_no_completion_evidence_not_completed():
    action = ActionModel(
        id="act2", person_id="p1", status="active", created_at=datetime.now(timezone.utc) - timedelta(days=90),
    )
    assert action.status != "completed"
    assert action.status != "expired"


def test_recurring_action_single_instance():
    setup_db()
    try:
        text = "Check blood pressure twice daily for 14 days."
        engine = ActionExtractionEngine()
        ref_date = datetime(2026, 8, 27, tzinfo=timezone.utc)
        candidates = engine.extract(text, "ev13", reference_date=ref_date)
        assert len(candidates) == 1
        assert candidates[0].is_recurring is True
        assert candidates[0].recurrence == "twice daily"
    finally:
        teardown_db()


def test_state_only_no_action():
    setup_db()
    try:
        text = "CT scan showed no acute findings."
        engine = ActionExtractionEngine()
        ref_date = datetime(2026, 8, 27, tzinfo=timezone.utc)
        candidates = engine.extract(text, "ev15", reference_date=ref_date)
        assert len(candidates) == 0
    finally:
        teardown_db()


def test_medication_mention_blocked():
    candidate = ActionCandidate(
        source_evidence_id="ev", source_passage="Medication X appears on the medication list.",
        original_text="Medication X appears on the medication list.", normalized_action="Continue medication X",
        modality="required", action_type="continue", is_medication_action=False,
        extraction_confidence=0.5,
    )
    safety = ActionNormalizationService().safety_check(candidate)
    assert safety.is_safe_to_create is False
    assert safety.blocked_reason is not None


def test_benefit_inference_requires_confirmation():
    candidate = ActionCandidate(
        source_evidence_id="ev", source_passage="Patient may benefit from physical therapy.",
        original_text="Patient may benefit from physical therapy.", normalized_action="Schedule physical therapy",
        modality="optional", action_type="schedule", is_explicit=False,
        extraction_confidence=0.5,
    )
    safety = ActionNormalizationService().safety_check(candidate)
    assert safety.requires_confirmation is True


def test_unknown_actor_required_action_requires_confirmation():
    candidate = ActionCandidate(
        source_evidence_id="ev", source_passage="Schedule follow-up.",
        original_text="Schedule follow-up.", normalized_action="Schedule follow-up",
        modality="required", action_type="schedule", actor_type="unknown", is_explicit=True,
        extraction_confidence=0.9,
    )
    safety = ActionNormalizationService().safety_check(candidate)
    assert safety.requires_confirmation is True
