from datetime import datetime, timedelta, timezone
import hashlib
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, SessionLocal as DefaultSessionLocal
import app.main as main_module
from app.main import app
from app.models.care import Person, Caregiver, CareEvent, Evidence
from app.models.anonymous_session import AnonymousSession
from app.models.change import Change
from app.models.observation_coverage import ObservationCoverage
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
    main_module.SessionLocal = TestingSessionLocal


def teardown_db():
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal
    Base.metadata.drop_all(bind=engine)


def _issue_scoped_session(db, person, caregiver):
    token = f"significance-api-token-{person.id}"
    db.add(
        AnonymousSession(
            id=f"session-{person.id}",
            person_id=person.id,
            caregiver_id=caregiver.id,
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
    )
    db.commit()
    return token


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
        assert changes[0].change_type.value == "emergence"
        assert "No comparable prior events" in changes[0].previous_state
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
        event_a = _add_event(
            db, person, caregiver, "confusion", "Dad was unusually confused",
            days_ago=1, evidence_text="Dad was unusually confused today.",
            caregiver_obj=caregiver,
        )
        event_b = _add_event(
            db, person, cg_b, "confusion", "Dad seemed completely normal",
            days_ago=1, evidence_text="He seemed completely normal today.",
            caregiver_obj=cg_b,
        )
        from app.models.uncertainty import Contradiction
        db.add(Contradiction(
            person_id=person.id,
            subject_type="confusion",
            subject_id="",
            field="reported_state",
            description="Persisted conflict between two attributed reports.",
            evidence_ids=list(dict.fromkeys((event_a.evidence_ids or []) + (event_b.evidence_ids or []))),
            matching_candidate_ids=[event_a.id, event_b.id],
            source_assertion_ids=[],
            resolution_status="unresolved",
        ))
        db.commit()

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


def test_professional_claim_in_source_text_does_not_verify_authority():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db, person, caregiver = _seed_person()
        _add_event(
            db,
            person,
            caregiver,
            "mobility",
            "Doctor confirmed mobility is reduced",
            days_ago=0,
            evidence_text="Doctor confirmed mobility is reduced.",
        )

        from app.services.change_detection import detect_event_changes
        changes = detect_event_changes(db, person.id, "mobility", None, datetime.now(timezone.utc))
        assert len(changes) == 1
        assessment = db.query(SignificanceAssessment).filter(
            SignificanceAssessment.id == changes[0].significance_assessment_id
        ).first()
        assert assessment is not None
        assert assessment.evidence_status == EvidenceStatus.REPORTED
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
        # Single reporter => low evidence confidence and zero independent corroboration.
        assert sig.evidence_confidence < 0.6
        corroboration = next(d for d in sig.dimensions if d["name"] == "corroboration")
        assert corroboration["score"] == 0.0
        persistence = next(d for d in sig.dimensions if d["name"] == "persistence")
        assert persistence["score"] == 0.0
        assert persistence["measurement_status"] == "unknown"
        # NOT an alert: two same-time reports of a low-signal observation from
        # one reporter do not earn attention, even if flagged for follow-up.
        assert sig.attention_candidate is False
        db.close()
    finally:
        teardown_db()


def test_gradual_decline_detected_via_trend_requires_persisted_evidence_and_comparable_windows():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db, person, caregiver = _seed_person()
        now = datetime.now(timezone.utc)
        from app.core.change_enums import ChangeType, ChangeDirection
        # Six monthly source events and matching assessments; each assessment has
        # its own timestamped comparison window and a direct evidence link.
        magnitudes = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        for i, magnitude in enumerate(magnitudes):
            event_time = now - timedelta(days=(len(magnitudes) - i) * 30)
            event = _add_event(
                db,
                person,
                caregiver,
                "fall",
                f"Fall occurrence {i + 1}",
                days_ago=(len(magnitudes) - i) * 30,
            )
            db.add(
                Change(
                    person_id=person.id,
                    subject_type="fall",
                    subject_id="",
                    change_type=ChangeType.INCREASE,
                    change_direction=ChangeDirection.INCREASING,
                    previous_state=str(magnitude - 1),
                    current_state=str(magnitude),
                    previous_count=int(magnitude - 1),
                    current_count=int(magnitude),
                    baseline_id=None,
                    comparison_window_start=event_time - timedelta(days=30),
                    comparison_window_end=event_time,
                    confidence=None,
                    evidence_ids=[event.id],
                    magnitude=magnitude,
                    baseline_coverage_status="covered",
                    current_coverage_status="covered",
                    explanation=f"Recorded change magnitude {magnitude}",
                    created_at=event_time,
                )
            )
        db.add(ObservationCoverage(
            id="trend-explicit-coverage",
            person_id=person.id,
            subject_type="fall",
            window_start=now - timedelta(days=220),
            window_end=now,
            coverage_status="covered",
            source_evidence_ids=[],
            rationale="The care domain was explicitly observed throughout the trend comparison span.",
            recorded_by_caregiver_id=caregiver.id,
        ))
        db.commit()
        from app.services.pattern_detection import detect_patterns
        patterns = detect_patterns(db, person.id, "fall", None, now)
        db.commit()
        types = [pattern.pattern_type.value for pattern in patterns]
        assert "trend" in types
        trend = [pattern for pattern in patterns if pattern.pattern_type.value == "trend"][0]
        assert trend.direction.value == "increasing"
        assert trend.evidence_count >= 3
        assert all(pattern.confidence is None for pattern in patterns), "uncalibrated pattern confidence must remain unknown"

        # Without evidence links, a sequence of magnitude values is not enough
        # to create a longitudinal trend.
        unsupported = Change(
            person_id=person.id,
            subject_type="unsupported",
            subject_id="",
            change_type=ChangeType.INCREASE,
            change_direction=ChangeDirection.INCREASING,
            previous_state="1",
            current_state="2",
            previous_count=1,
            current_count=2,
            baseline_id=None,
            comparison_window_start=now - timedelta(days=30),
            comparison_window_end=now,
            confidence=None,
            evidence_ids=[],
            magnitude=1.0,
            explanation="No source evidence",
        )
        db.add(unsupported)
        db.commit()
        no_pattern = detect_patterns(db, person.id, "unsupported", None, now)
        db.commit()
        assert all(pattern.pattern_type.value != "trend" for pattern in no_pattern)
        db.close()
    finally:
        teardown_db()


def test_trend_analysis_never_combines_different_subject_ids():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db, person, caregiver = _seed_person()
        now = datetime.now(timezone.utc)
        from app.core.change_enums import ChangeType, ChangeDirection
        event_ids = []
        for index, (subject_id, magnitude, days_ago) in enumerate(
            (("left", 1.0, 90), ("left", 2.0, 60), ("right", 3.0, 30), ("right", 4.0, 5)),
            start=1,
        ):
            event = _add_event(
                db, person, caregiver, "mobility", f"Mobility observation {index}", days_ago
            )
            event_ids.append(event.id)
            window_end = now - timedelta(days=days_ago)
            db.add(Change(
                person_id=person.id,
                subject_type="mobility",
                subject_id=subject_id,
                change_type=ChangeType.INCREASE,
                change_direction=ChangeDirection.INCREASING,
                previous_state=str(magnitude - 1),
                current_state=str(magnitude),
                previous_count=int(magnitude - 1),
                current_count=int(magnitude),
                baseline_id=None,
                comparison_window_start=window_end - timedelta(days=20),
                comparison_window_end=window_end,
                confidence=None,
                evidence_ids=[event.id],
                magnitude=magnitude,
                explanation="Source-linked assessment",
                created_at=window_end,
            ))
        db.commit()

        from app.services.pattern_detection import detect_patterns
        patterns = detect_patterns(db, person.id, "mobility", None, now)
        db.commit()
        assert all(pattern.pattern_type.value != "trend" for pattern in patterns)
        db.close()
    finally:
        teardown_db()


def test_pattern_source_ids_must_belong_to_requested_person():
    setup_db()
    try:
        db, person, caregiver = _seed_person()
        other = Person(id="p-2", name="Other Person")
        other_caregiver = Caregiver(
            id="cg-2", name="Other Caregiver", email="cg2@example.com",
            relationship="Family", person_id=other.id,
        )
        db.add_all([other, other_caregiver])
        db.commit()
        now = datetime.now(timezone.utc)
        from app.core.change_enums import ChangeType, ChangeDirection
        for index, days_ago in enumerate((90, 60, 30), start=1):
            source_event = _add_event(
                db, other, other_caregiver, "mobility", f"Observation {index}", days_ago
            )
            window_end = now - timedelta(days=days_ago)
            db.add(Change(
                person_id=person.id, subject_type="mobility", subject_id="",
                change_type=ChangeType.INCREASE,
                change_direction=ChangeDirection.INCREASING,
                previous_state=str(index - 1), current_state=str(index),
                previous_count=index - 1, current_count=index,
                baseline_id=None,
                comparison_window_start=window_end - timedelta(days=20),
                comparison_window_end=window_end, confidence=None,
                evidence_ids=[source_event.id], magnitude=float(index),
                explanation="Source event scope test", created_at=window_end,
            ))
        db.commit()
        from app.services.pattern_detection import detect_patterns
        patterns = detect_patterns(db, person.id, "mobility", None, now)
        db.commit()
        assert all(pattern.pattern_type.value != "trend" for pattern in patterns)
        assert all(
            event_id not in {
                row[0] for row in db.query(CareEvent.id).filter(CareEvent.person_id == other.id).all()
            }
            for pattern in patterns for event_id in (pattern.event_ids or [])
        )
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
        token = _issue_scoped_session(db, person, caregiver)
        db.close()

        client = TestClient(app)
        response = client.get(
            "/api/v1/change/situations/detect",
            params={"person_id": person_id},
            headers={"Authorization": f"Bearer {token}"},
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


def test_acceleration_pattern_requires_increasing_deltas_and_full_coverage():
    setup_db()
    try:
        db, person, caregiver = _seed_person()
        now = datetime.now(timezone.utc)
        from app.core.change_enums import ChangeType, ChangeDirection, PatternType
        magnitudes = [1.0, 2.0, 4.0, 8.0]
        for index, magnitude in enumerate(magnitudes):
            event_time = now - timedelta(days=(4 - index) * 20)
            event = CareEvent(
                id=f"acceleration-source-{index}",
                person_id=person.id,
                event_type="mobility",
                status="recorded",
                occurred_at=event_time,
                title=f"Mobility observation {index}",
                created_by_caregiver_id=caregiver.id,
            )
            db.add(event)
            db.flush()
            db.add(Change(
                id=f"acceleration-change-{index}",
                person_id=person.id,
                subject_type="mobility",
                subject_id="",
                change_type=ChangeType.INCREASE,
                change_direction=ChangeDirection.INCREASING,
                previous_state=str(magnitude / 2),
                current_state=str(magnitude),
                previous_count=index,
                current_count=index + 1,
                comparison_window_start=event_time - timedelta(days=10),
                comparison_window_end=event_time,
                confidence=None,
                evidence_ids=[event.id],
                magnitude=magnitude,
                baseline_coverage_status="covered",
                current_coverage_status="covered",
                confirmation_status="detected",
                is_active=(index == 3),
                superseded_by_change_id=(f"acceleration-change-{index + 1}" if index < 3 else None),
                explanation="Source-linked magnitude assessment",
                created_at=event_time,
            ))
        db.add(ObservationCoverage(
            id="acceleration-full-coverage",
            person_id=person.id,
            subject_type="mobility",
            window_start=now - timedelta(days=100),
            window_end=now,
            coverage_status="covered",
            source_evidence_ids=[],
            rationale="The care domain was explicitly observed across the complete comparison span.",
            recorded_by_caregiver_id=caregiver.id,
        ))
        db.commit()

        from app.services.pattern_detection import detect_patterns
        patterns = detect_patterns(db, person.id, "mobility", None, now)
        db.commit()
        acceleration = [item for item in patterns if item.pattern_type == PatternType.ACCELERATION]
        assert len(acceleration) == 1
        assert acceleration[0].evidence_count == 4
        assert acceleration[0].confidence is None
        assert any(item.pattern_type == PatternType.TREND for item in patterns), (
            "Longitudinal patterns must use valid superseded assessments as history."
        )
        assert "not a calibrated forecast" in " ".join(acceleration[0].alternative_interpretations or [])
    finally:
        teardown_db()
