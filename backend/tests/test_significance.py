from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app
from app.models.care import Person, Caregiver, CareEvent, Evidence
from app.models.change import Change
from app.models.significance import SignificanceAssessment

from app.core.change_enums import SignificanceVerdict, EvidenceStatus

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


def _seed_person():
    db = TestingSessionLocal()
    person = Person(id="p-1", name="Test Person")
    caregiver = Caregiver(
        id="cg-1", name="Main Caregiver", email="cg1@example.com",
        relationship="Family", person_id=person.id,
    )
    db.add_all([person, caregiver])
    db.commit()
    return db, person, caregiver


def _add_event(db, person, caregiver, event_type, title, days_ago, evidence_text=None,
               caregiver_obj=None):
    occurred_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    ev_ids = []
    if evidence_text:
        ev = Evidence(
            person_id=person.id, type="note", source_text=evidence_text,
            uploaded_by_caregiver_id=caregiver_obj.id if caregiver_obj else caregiver.id,
        )
        db.add(ev)
        db.flush()
        ev_ids.append(ev.id)
    event = CareEvent(
        person_id=person.id, event_type=event_type, status="recorded",
        occurred_at=occurred_at, title=title,
        evidence_ids=ev_ids,
        created_by_caregiver_id=caregiver_obj.id if caregiver_obj else caregiver.id,
    )
    db.add(event)
    db.commit()
    return event


def test_three_falls_is_meaningful_attention_candidate():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db, person, caregiver = _seed_person()
        now = datetime.now(timezone.utc)
        # No falls in the preceding ~8 months; three falls within the last 6 weeks.
        for i in range(3):
            _add_event(db, person, caregiver, "fall", f"Fall {i+1}", days_ago=7 + i * 14)

        from app.services.situation_formation import detect_situations
        situations = detect_situations(db, person.id, now)
        assert len(situations) >= 1
        sit = situations[0]
        assert sit.attention_candidate is True
        assert sit.follow_up_candidate is True
        assert sit.context["significance_verdict"] == SignificanceVerdict.MEANINGFUL_CANDIDATE.value
        # evidence_status stays reported (caregiver report), not upgraded to confirmed
        assert sit.context["evidence_status"] == EvidenceStatus.REPORTED.value
        # baseline is sparse -> uncertainty must be surfaced, not hidden
        assert any("caregiver" in c.lower() for c in (sit.context.get("possible_context") or []))
        # attention candidate's change must carry a significance assessment
        change = db.query(Change).filter(Change.id.in_(sit.change_ids or [])).first()
        assert change is not None
        assert change.significance_verdict == SignificanceVerdict.MEANINGFUL_CANDIDATE
        assert change.significance_assessment_id is not None
        db.close()
    finally:
        teardown_db()


def test_single_tired_today_is_not_meaningful_no_alert():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db, person, caregiver = _seed_person()
        now = datetime.now(timezone.utc)
        _add_event(db, person, caregiver, "fatigue", "Mom seemed tired today", days_ago=0)

        from app.services.change_detection import detect_event_changes
        changes = detect_event_changes(db, person.id, "fatigue", None, now)
        assert len(changes) == 1
        assert changes[0].change_type.value == "onset"
        # A single unverified report must NOT be a meaningful-change candidate
        assert changes[0].significance_verdict == SignificanceVerdict.INSUFFICIENT_EVIDENCE
        assert changes[0].attention_candidate is False

        from app.services.situation_formation import detect_situations
        situations = detect_situations(db, person.id, now)
        # No MEANINGFUL change -> no situation of meaningful deterioration
        meaningful = [s for s in situations if s.attention_candidate]
        assert meaningful == []
        db.close()
    finally:
        teardown_db()


def test_ambiguous_input_does_not_invent_specificity():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db, person, caregiver = _seed_person()
        now = datetime.now(timezone.utc)
        _add_event(db, person, caregiver, "altered_mental_status",
                   "Dad wasn't himself today", days_ago=0)

        from app.services.change_detection import detect_event_changes
        changes = detect_event_changes(db, person.id, "altered_mental_status", None, now)
        assert len(changes) == 1
        # Ambiguous observation is detected as a change (honest), but verdict must
        # not be inflated: single unverified report -> insufficient evidence
        assert changes[0].significance_verdict == SignificanceVerdict.INSUFFICIENT_EVIDENCE
        # No diagnosis/cause is attributed
        assert "dementia" not in (changes[0].explanation or "").lower()
        assert "progress" not in (changes[0].explanation or "").lower()
        db.close()
    finally:
        teardown_db()


def test_contradictory_observations_preserved_not_collapsed():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db, person, caregiver = _seed_person()
        cg_b = Caregiver(
            id="cg-2", name="Second Caregiver", email="cg2@example.com",
            relationship="Friend", person_id=person.id,
        )
        db.add(cg_b)
        db.commit()
        now = datetime.now(timezone.utc)
        _add_event(db, person, caregiver, "confusion", "Dad was unusually confused",
                   days_ago=1, evidence_text="Dad was unusually confused today.",
                   caregiver_obj=caregiver)
        _add_event(db, person, cg_b, "confusion", "Dad seemed completely normal",
                   days_ago=1, evidence_text="He seemed completely normal today.",
                   caregiver_obj=cg_b)

        from app.services.change_detection import detect_event_changes
        changes = detect_event_changes(db, person.id, "confusion", None, now)
        assert len(changes) == 1
        change = changes[0]
        # The contradiction is surfaced, not silently resolved
        sig = db.query(SignificanceAssessment).filter(
            SignificanceAssessment.id == change.significance_assessment_id
        ).first()
        assert sig is not None
        assert sig.evidence_status == EvidenceStatus.CONTRADICTORY
        assert sig.verdict == SignificanceVerdict.INSUFFICIENT_EVIDENCE
        assert sig.attention_candidate is False
        # Reporters remain attributable (two distinct caregivers)
        assert len(sig.reporters or []) == 2
        db.close()
    finally:
        teardown_db()


def test_documentation_density_spike_not_treated_as_real_event():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db, person, caregiver = _seed_person()
        now = datetime.now(timezone.utc)
        # Two documentation events by the SAME reporter within minutes (a
        # documentation cadence spike, not two distinct real events), on a
        # non-functional, low-signal subject, with a sparse baseline.
        _add_event(db, person, caregiver, "appetite", "Appetite lower today", days_ago=0)
        _add_event(db, person, caregiver, "appetite", "Appetite lower today", days_ago=0)

        from app.services.change_detection import detect_event_changes
        changes = detect_event_changes(db, person.id, "appetite", None, now)
        assert len(changes) == 1
        change = changes[0]
        sig = db.query(SignificanceAssessment).filter(
            SignificanceAssessment.id == change.significance_assessment_id
        ).first()
        assert sig is not None
        assert sig.evidence_status == EvidenceStatus.REPORTED
        # Single reporter => low evidence confidence, no corroboration.
        assert sig.evidence_confidence < 0.6
        # NOT an alert: two same-time reports of a low-signal observation from
        # one reporter do not earn attention, even if flagged for follow-up.
        assert sig.attention_candidate is False
        db.close()
    finally:
        teardown_db()


def test_gradual_decline_detected_via_trend():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db, person, caregiver = _seed_person()
        now = datetime.now(timezone.utc)
        from app.core.change_enums import ChangeType, ChangeDirection
        # Six monthly increasing-difficulty fall observations, each a single event,
        # with monotonically increasing magnitude so a TREND pattern can be built.
        magnitudes = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        for i, m in enumerate(magnitudes):
            change = Change(
                person_id=person.id, subject_type="fall", subject_id="",
                change_type=ChangeType.INCREASE, change_direction=ChangeDirection.INCREASING,
                previous_state=str(m - 1), current_state=str(m),
                previous_count=int(m - 1), current_count=int(m),
                baseline_id=None, comparison_window_start=now - timedelta(days=30),
                comparison_window_end=now, confidence=0.8, evidence_ids=[],
                magnitude=m,
                explanation=f"difficulty increased to {m}",
                created_at=now - timedelta(days=(len(magnitudes) - i) * 30),
            )
            db.add(change)
        db.commit()
        from app.services.pattern_detection import detect_patterns
        patterns = detect_patterns(db, person.id, "fall", None, now)
        types = [p.pattern_type.value for p in patterns]
        assert "trend" in types
        trend = [p for p in patterns if p.pattern_type.value == "trend"][0]
        assert trend.direction.value == "increasing"
        db.close()
    finally:
        teardown_db()


def test_cold_start_no_baseline_no_false_positive():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db, person, caregiver = _seed_person()
        # A person with a single observation and no usable historical baseline.
        _add_event(db, person, caregiver, "fall", "Fall", days_ago=1)
        from app.services.baseline import build_baseline
        baseline = build_baseline(db, person.id, "fall", None, datetime.now(timezone.utc))
        assert baseline is not None
        assert baseline.confidence.value == "low"
        from app.services.change_detection import detect_event_changes
        changes = detect_event_changes(db, person.id, "fall", None, datetime.now(timezone.utc))
        assert len(changes) >= 1
        # Single fall is novel + functional-impact; still a MEANINGFUL_CANDIDATE
        # (first fall matters), with appropriately reduced confidence.
        assert changes[0].significance_verdict == SignificanceVerdict.MEANINGFUL_CANDIDATE
        sig = db.query(SignificanceAssessment).filter(
            SignificanceAssessment.id == changes[0].significance_assessment_id
        ).first()
        assert sig.significance_confidence < 0.9
        assert sig.evidence_status == EvidenceStatus.REPORTED
        db.close()
    finally:
        teardown_db()


def test_api_situation_response_exposes_attention_and_significance():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db, person, caregiver = _seed_person()
        for i in range(3):
            _add_event(db, person, caregiver, "fall", f"Fall {i+1}", days_ago=7 + i * 14)
        person_id = person.id
        db.close()

        client = TestClient(app)
        response = client.get(
            "/api/v1/change/situations/detect",
            params={"person_id": person_id},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        top = data[0]
        assert "attention_candidate" in top
        assert "follow_up_candidate" in top
        assert "context" in top
        assert top["context"]["significance_verdict"] == "meaningful_candidate"
        assert top["attention_candidate"] is True
        for c in (top.get("changes") or []):
            assert c["significance_verdict"] is not None
        # No causal / diagnostic language leaks into the system's own output
        blob = (top.get("description") or "") + " " + (top.get("explanation") or "")
        assert "diagnos" not in blob.lower()
        assert "caused by" not in blob.lower()
    finally:
        teardown_db()


def test_observation_preserved_verbatim_from_inference():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db, person, caregiver = _seed_person()
        raw = "Dad seemed much more confused today."
        now = datetime.now(timezone.utc)
        _add_event(db, person, caregiver, "confusion", raw, days_ago=0,
                   evidence_text=raw)
        ev = db.query(Evidence).filter(Evidence.person_id == person.id).first()
        assert ev.source_text == raw  # original wording preserved
        from app.services.change_detection import detect_event_changes
        changes = detect_event_changes(db, person.id, "confusion", None, now)
        # Observation (title/source_text) stays distinct from the inference (explanation)
        ch = changes[0]
        assert ch.explanation is not None
        assert "confusion" in ch.explanation.lower()
        db.close()
    finally:
        teardown_db()
