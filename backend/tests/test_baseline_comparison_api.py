from datetime import datetime, timedelta, timezone
import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
import app.main as main_module
from app.main import app
from app.models.anonymous_session import AnonymousSession
from app.models.care import CareEvent, Caregiver, Person, Evidence, Correction
from app.models.change import Baseline, Change
from app.core.change_enums import BaselineConfidence, BaselineType, ChangeType


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _session(db, person, caregiver):
    token = f"baseline-comparison-{person.id}"
    db.add(AnonymousSession(
        id=f"session-{person.id}",
        person_id=person.id,
        caregiver_id=caregiver.id,
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    ))
    return token


def test_baseline_comparison_returns_only_same_person_linked_source_records():
    Base.metadata.create_all(bind=engine)
    main_module.SessionLocal = TestingSessionLocal
    app.dependency_overrides[get_db] = override_get_db
    db = TestingSessionLocal()
    try:
        person = Person(id="baseline-person-a", name="Person A")
        caregiver = Caregiver(
            id="baseline-caregiver-a", name="Caregiver A",
            email="baseline-a@example.com", relationship="Family", person_id=person.id,
        )
        other_person = Person(id="baseline-person-b", name="Person B")
        other_caregiver = Caregiver(
            id="baseline-caregiver-b", name="Caregiver B",
            email="baseline-b@example.com", relationship="Family", person_id=other_person.id,
        )
        db.add_all([person, caregiver, other_person, other_caregiver])
        db.flush()
        token = _session(db, person, caregiver)
        other_token = _session(db, other_person, other_caregiver)
        source = Evidence(
            id="baseline-source-a", person_id=person.id, type="caregiver_note",
            source_text="The caregiver reports a usual bedtime around 9 pm.",
            uploaded_by_caregiver_id=caregiver.id, reported_by_label="Family caregiver",
            source_type="caregiver_report",
        )
        event = CareEvent(
            id="baseline-event-a", person_id=person.id, event_type="sleep",
            status="recorded", occurred_at=datetime.now(timezone.utc) - timedelta(days=90),
            title="Usual sleep observation", description="Reported usual bedtime",
            created_by_caregiver_id=caregiver.id, evidence_ids=["baseline-source-a"],
        )
        other_event = CareEvent(
            id="baseline-event-b", person_id=other_person.id, event_type="sleep",
            status="recorded", occurred_at=datetime.now(timezone.utc) - timedelta(days=90),
            title="Other person's private observation",
            created_by_caregiver_id=other_caregiver.id,
        )
        current_event = CareEvent(
            id="current-mobility-event", person_id=person.id, event_type="mobility",
            status="recorded", occurred_at=datetime.now(timezone.utc) - timedelta(days=2),
            title="Recent mobility observation", created_by_caregiver_id=caregiver.id,
        )
        db.add_all([source, event, other_event, current_event])
        db.flush()
        db.add(Change(
            id="current-mobility-change", person_id=person.id, subject_type="mobility",
            subject_id="", change_type=ChangeType.INCREASE,
            previous_state="Earlier mobility record", current_state="More support recorded",
            evidence_ids=[current_event.id], confirmation_status="awaiting_confirmation",
            is_active=True, attention_candidate=True,
        ))
        db.flush()
        baseline = Baseline(
            id="baseline-a", person_id=person.id, subject_type="sleep", subject_id="",
            baseline_type=BaselineType.PERSONAL_HISTORICAL,
            window_start=datetime.now(timezone.utc) - timedelta(days=120),
            window_end=datetime.now(timezone.utc) - timedelta(days=60),
            event_count=1, confidence=BaselineConfidence.LOW, is_stable=False,
            coverage_status="unknown", history_completeness="complete",
            analysis_version="test", documentation_bias_flag=True,
            evidence_ids=[event.id], is_active=True,
        )
        db.add(baseline)
        db.flush()
        prior_baseline = Baseline(
            id="baseline-prior-a", person_id=person.id, subject_type="sleep", subject_id="",
            baseline_type=BaselineType.PERSONAL_HISTORICAL,
            window_start=datetime.now(timezone.utc) - timedelta(days=180),
            window_end=datetime.now(timezone.utc) - timedelta(days=120),
            event_count=1, confidence=BaselineConfidence.LOW, is_stable=False,
            coverage_status="unknown", history_completeness="complete",
            analysis_version="legacy-test", documentation_bias_flag=True,
            evidence_ids=[event.id], is_active=False,
            superseded_by_baseline_id="baseline-a",
            created_at=datetime.now(timezone.utc) - timedelta(days=10),
        )
        db.add(prior_baseline)
        db.add(Correction(
            id="baseline-correction-a", person_id=person.id, target_type="evidence",
            target_id="baseline-source-a",
            original_text="usual bedtime around 8 pm",
            corrected_text="usual bedtime around 9 pm",
            reason="Caregiver corrected the original time", caregiver_id=caregiver.id,
        ))
        db.commit()

        client = TestClient(app)
        response = client.get(
            f"/api/v1/change/baselines/{person.id}/comparison",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["person_id"] == person.id
        assert payload["read_only"] is True
        assert {item["id"] for item in payload["baselines"]} == {"baseline-a", "baseline-prior-a"}
        prior = next(item for item in payload["baselines"] if item["id"] == "baseline-prior-a")
        assert prior["is_active"] is False
        assert prior["superseded_by_baseline_id"] == "baseline-a"
        assert "current-mobility-change" in {item["id"] for item in payload["changes"]}
        mobility_change = next(item for item in payload["changes"] if item["id"] == "current-mobility-change")
        assert mobility_change["attention_candidate"] is True
        assert "current-mobility-event" in {item["id"] for item in payload["events"]}
        assert [item["id"] for item in payload["events"] if item["id"] == "baseline-event-a"] == ["baseline-event-a"]
        assert payload["events"][0]["created_by_caregiver_id"] == caregiver.id
        assert [item["id"] for item in payload["source_evidence"]] == ["baseline-source-a"]
        assert payload["source_evidence"][0]["source_text"].startswith("The caregiver reports")
        assert payload["source_evidence"][0]["reported_by_label"] == "Family caregiver"
        assert [item["id"] for item in payload["corrections"]] == ["baseline-correction-a"]
        assert payload["corrections"][0]["corrected_text"] == "usual bedtime around 9 pm"
        assert "baseline-event-b" not in {item["id"] for item in payload["events"]}

        forbidden = client.get(
            f"/change/baselines/{person.id}/comparison",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert forbidden.status_code == 403
    finally:
        db.close()
        app.dependency_overrides.clear()
        main_module.SessionLocal = DefaultSessionLocal
        Base.metadata.drop_all(bind=engine)
