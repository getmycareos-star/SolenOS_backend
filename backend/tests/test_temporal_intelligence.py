from datetime import datetime, timedelta, timezone
from app.services.temporal_intelligence import (
    create_temporal_fact,
    update_temporal_fact,
    supersede_temporal_fact,
    create_temporal_relation,
    create_temporal_reference_frame,
    resolve_relative_time,
    infer_temporal_relation,
    create_temporal_contradiction,
    update_temporal_contradiction,
    get_temporal_view_at_point,
    get_subject_temporal_view,
    calculate_duration,
    classify_temporal_status,
    check_temporal_conflict,
    utc_now,
    _ensure_utc,
)
from app.core.temporal_enums import (
    TemporalPrecision,
    TemporalMode,
    TemporalStatus,
    TemporalRelationType,
    TemporalResolutionStatus,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.database import Base
from app.models.care import Person, Caregiver
from app.models.temporal import TemporalFact as TemporalFactModel
from app.schemas.temporal import (
    TemporalFactCreate,
    TemporalFactUpdate,
    TemporalRelationCreate,
    TemporalContradictionCreate,
    TemporalContradictionUpdate,
)


SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def setup_db():
    Base.metadata.create_all(bind=engine)


def teardown_db():
    Base.metadata.drop_all(bind=engine)


def get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_ensure_utc_naive():
    naive = datetime(2026, 1, 1, 12, 0, 0)
    result = _ensure_utc(naive)
    assert result.tzinfo is timezone.utc
    assert result.hour == 12


def test_ensure_utc_aware():
    aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    result = _ensure_utc(aware)
    assert result.tzinfo is timezone.utc
    assert result == aware


def test_ensure_utc_none():
    assert _ensure_utc(None) is None


def test_utc_now_is_timezone_aware():
    now = utc_now()
    assert now.tzinfo is timezone.utc


def test_create_temporal_fact_point():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-1", name="Test Person")
        caregiver = Caregiver(
            id="caregiver-1",
            name="Test Caregiver",
            email="test@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-1",
                subject_type="event",
                subject_id="evt-1",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc),
                precision=TemporalPrecision.DAY,
                time_provenance="caregiver_entered",
                evidence_ids=["ev-1"],
                created_by_caregiver_id="caregiver-1",
            ),
        )
        assert fact.id is not None
        assert fact.asserted_point is not None
        assert fact.precision == TemporalPrecision.DAY
    finally:
        teardown_db()


def test_create_temporal_fact_interval():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-2", name="Test Person 2")
        caregiver = Caregiver(
            id="caregiver-2",
            name="Test Caregiver 2",
            email="test2@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-2",
                subject_type="hospitalization",
                subject_id="hosp-1",
                temporal_mode=TemporalMode.EVENT,
                asserted_start=datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc),
                asserted_end=datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc),
                precision=TemporalPrecision.DAY,
                time_provenance="extracted_discharge",
                evidence_ids=["ev-2"],
                created_by_caregiver_id="caregiver-2",
            ),
        )
        assert fact.id is not None
        assert fact.asserted_start is not None
        assert fact.asserted_end is not None
    finally:
        teardown_db()


def test_update_temporal_fact():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-3", name="Test Person 3")
        caregiver = Caregiver(
            id="caregiver-3",
            name="Test Caregiver 3",
            email="test3@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-3",
                subject_type="event",
                subject_id="evt-3",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc),
                precision=TemporalPrecision.DAY,
                created_by_caregiver_id="caregiver-3",
            ),
        )

        updated = update_temporal_fact(
            db,
            fact.id,
            TemporalFactUpdate(
                asserted_point=datetime(2026, 3, 5, 12, 0, 0, tzinfo=timezone.utc),
                precision=TemporalPrecision.DAY,
                time_provenance="corrected",
            ),
        )
        assert updated.asserted_point.day == 5
        assert updated.precision == TemporalPrecision.DAY
        assert updated.time_provenance == "corrected"
    finally:
        teardown_db()


def test_supersede_temporal_fact():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-4", name="Test Person 4")
        caregiver = Caregiver(
            id="caregiver-4",
            name="Test Caregiver 4",
            email="test4@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact1 = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-4",
                subject_type="event",
                subject_id="evt-4",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc),
                created_by_caregiver_id="caregiver-4",
            ),
        )

        fact2 = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-4",
                subject_type="event",
                subject_id="evt-4",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 5, 12, 0, 0, tzinfo=timezone.utc),
                created_by_caregiver_id="caregiver-4",
            ),
        )

        supersede_temporal_fact(db, fact1.id, fact2.id)
        db.refresh(fact1)
        assert fact1.status == TemporalStatus.SUPERSEDED
        assert fact1.superseded_by_fact_id == fact2.id
    finally:
        teardown_db()


def test_create_temporal_relation():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-5", name="Test Person 5")
        caregiver = Caregiver(
            id="caregiver-5",
            name="Test Caregiver 5",
            email="test5@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact_a = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-5",
                subject_type="event",
                subject_id="evt-a",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
                created_by_caregiver_id="caregiver-5",
            ),
        )

        fact_b = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-5",
                subject_type="event",
                subject_id="evt-b",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 5, 12, 0, 0, tzinfo=timezone.utc),
                created_by_caregiver_id="caregiver-5",
            ),
        )

        relation = create_temporal_relation(
            db,
            TemporalRelationCreate(
                person_id="person-5",
                fact_a_id=fact_a.id,
                fact_b_id=fact_b.id,
                relation_type=TemporalRelationType.BEFORE,
                confidence=0.9,
            ),
        )
        assert relation.id is not None
        assert relation.relation_type == TemporalRelationType.BEFORE
    finally:
        teardown_db()


def test_create_temporal_reference_frame():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-6", name="Test Person 6")
        caregiver = Caregiver(
            id="caregiver-6",
            name="Test Caregiver 6",
            email="test6@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        frame = create_temporal_reference_frame(
            db,
            person_id="person-6",
            name="Discharge date",
            reference_time=datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
            is_resolved=True,
        )
        assert frame.id is not None
        assert frame.name == "Discharge date"
        assert frame.is_resolved is True
    finally:
        teardown_db()


def test_resolve_relative_time_resolved():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-7", name="Test Person 7")
        caregiver = Caregiver(
            id="caregiver-7",
            name="Test Caregiver 7",
            email="test7@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        frame = create_temporal_reference_frame(
            db,
            person_id="person-7",
            name="Discharge",
            reference_time=datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
            is_resolved=True,
        )

        result = resolve_relative_time(db, "person-7", "3 days after discharge", frame.id)
        assert result.is_resolved is True
        assert result.resolved_point is not None
        assert result.resolved_point.day == 4
    finally:
        teardown_db()


def test_resolve_relative_time_unresolved():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-8", name="Test Person 8")
        caregiver = Caregiver(
            id="caregiver-8",
            name="Test Caregiver 8",
            email="test8@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        result = resolve_relative_time(db, "person-8", "3 days after discharge", None)
        assert result.is_resolved is False
        assert result.resolved_point is None
        assert result.confidence == 0.0
    finally:
        teardown_db()


def test_infer_temporal_relation_point_point():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-9", name="Test Person 9")
        caregiver = Caregiver(
            id="caregiver-9",
            name="Test Caregiver 9",
            email="test9@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact_a = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-9",
                subject_type="event",
                subject_id="evt-a",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
                created_by_caregiver_id="caregiver-9",
            ),
        )

        fact_b = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-9",
                subject_type="event",
                subject_id="evt-b",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 5, 12, 0, 0, tzinfo=timezone.utc),
                created_by_caregiver_id="caregiver-9",
            ),
        )

        result = infer_temporal_relation(db, fact_a.id, fact_b.id)
        assert result is not None
        assert result["relation"] == TemporalRelationType.BEFORE.value
        assert result["is_derived"] is True
        assert result["derivation_method"] == "temporal_calculation"
    finally:
        teardown_db()


def test_infer_temporal_relation_interval_interval():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-10", name="Test Person 10")
        caregiver = Caregiver(
            id="caregiver-10",
            name="Test Caregiver 10",
            email="test10@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact_a = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-10",
                subject_type="hospitalization",
                subject_id="hosp-a",
                temporal_mode=TemporalMode.EVENT,
                asserted_start=datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
                asserted_end=datetime(2026, 3, 5, 12, 0, 0, tzinfo=timezone.utc),
                created_by_caregiver_id="caregiver-10",
            ),
        )

        fact_b = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-10",
                subject_type="hospitalization",
                subject_id="hosp-b",
                temporal_mode=TemporalMode.EVENT,
                asserted_start=datetime(2026, 3, 3, 12, 0, 0, tzinfo=timezone.utc),
                asserted_end=datetime(2026, 3, 7, 12, 0, 0, tzinfo=timezone.utc),
                created_by_caregiver_id="caregiver-10",
            ),
        )

        result = infer_temporal_relation(db, fact_a.id, fact_b.id)
        assert result is not None
        assert result["relation"] == TemporalRelationType.OVERLAPS.value
    finally:
        teardown_db()


def test_detect_temporal_conflicts():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-11", name="Test Person 11")
        caregiver = Caregiver(
            id="caregiver-11",
            name="Test Caregiver 11",
            email="test11@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact_a = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-11",
                subject_type="hospitalization",
                subject_id="hosp-1",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc),
                created_by_caregiver_id="caregiver-11",
            ),
        )

        fact_b = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-11",
                subject_type="hospitalization",
                subject_id="hosp-1",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 6, 12, 0, 0, tzinfo=timezone.utc),
                created_by_caregiver_id="caregiver-11",
            ),
        )

        conflict = check_temporal_conflict(db, fact_a.id, fact_b.id)
        assert conflict is not None
        assert conflict["conflict_type"] == "competing_points"
    finally:
        teardown_db()


def test_create_temporal_contradiction():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-12", name="Test Person 12")
        caregiver = Caregiver(
            id="caregiver-12",
            name="Test Caregiver 12",
            email="test12@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact_a = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-12",
                subject_type="hospitalization",
                subject_id="hosp-1",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc),
                created_by_caregiver_id="caregiver-12",
            ),
        )

        fact_b = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-12",
                subject_type="hospitalization",
                subject_id="hosp-1",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 6, 12, 0, 0, tzinfo=timezone.utc),
                created_by_caregiver_id="caregiver-12",
            ),
        )

        contradiction = create_temporal_contradiction(
            db,
            TemporalContradictionCreate(
                person_id="person-12",
                subject_type="hospitalization",
                subject_id="hosp-1",
                fact_a_id=fact_a.id,
                fact_b_id=fact_b.id,
                description="Source A says March 4, Source B says March 6",
                evidence_ids=["ev-a", "ev-b"],
            ),
        )
        assert contradiction.id is not None
        assert contradiction.resolution_status == TemporalResolutionStatus.UNRESOLVED
    finally:
        teardown_db()


def test_update_temporal_contradiction():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-13", name="Test Person 13")
        caregiver = Caregiver(
            id="caregiver-13",
            name="Test Caregiver 13",
            email="test13@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact_a = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-13",
                subject_type="hospitalization",
                subject_id="hosp-1",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc),
                created_by_caregiver_id="caregiver-13",
            ),
        )

        fact_b = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-13",
                subject_type="hospitalization",
                subject_id="hosp-1",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 6, 12, 0, 0, tzinfo=timezone.utc),
                created_by_caregiver_id="caregiver-13",
            ),
        )

        contradiction = create_temporal_contradiction(
            db,
            TemporalContradictionCreate(
                person_id="person-13",
                subject_type="hospitalization",
                subject_id="hosp-1",
                fact_a_id=fact_a.id,
                fact_b_id=fact_b.id,
                description="Competing dates",
            ),
        )

        updated = update_temporal_contradiction(
            db,
            contradiction.id,
            TemporalContradictionUpdate(
                resolution_status=TemporalResolutionStatus.RESOLVED_A,
                resolution_notes="Source A is more reliable",
            ),
        )
        assert updated.resolution_status == TemporalResolutionStatus.RESOLVED_A
        assert updated.resolved_at is not None
    finally:
        teardown_db()


def test_get_temporal_view_at_point():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-14", name="Test Person 14")
        caregiver = Caregiver(
            id="caregiver-14",
            name="Test Caregiver 14",
            email="test14@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-14",
                subject_type="event",
                subject_id="evt-1",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc),
                created_by_caregiver_id="caregiver-14",
            ),
        )

        view = get_temporal_view_at_point(db, "person-14", datetime(2026, 3, 5, 12, 0, 0, tzinfo=timezone.utc))
        assert view.person_id == "person-14"
        assert len(view.historical_facts) == 1
    finally:
        teardown_db()


def test_get_subject_temporal_view():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-15", name="Test Person 15")
        caregiver = Caregiver(
            id="caregiver-15",
            name="Test Caregiver 15",
            email="test15@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-15",
                subject_type="hospitalization",
                subject_id="hosp-1",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc),
                created_by_caregiver_id="caregiver-15",
            ),
        )

        create_temporal_relation(
            db,
            TemporalRelationCreate(
                person_id="person-15",
                fact_a_id=fact.id,
                fact_b_id=fact.id,
                relation_type=TemporalRelationType.SIMULTANEOUS,
            ),
        )

        view = get_subject_temporal_view(db, "person-15", "hospitalization", "hosp-1")
        assert view.person_id == "person-15"
        assert len(view.facts) == 1
        assert len(view.relations) == 1
    finally:
        teardown_db()


def test_calculate_duration_interval():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-16", name="Test Person 16")
        caregiver = Caregiver(
            id="caregiver-16",
            name="Test Caregiver 16",
            email="test16@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-16",
                subject_type="hospitalization",
                subject_id="hosp-1",
                temporal_mode=TemporalMode.EVENT,
                asserted_start=datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc),
                asserted_end=datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc),
                created_by_caregiver_id="caregiver-16",
            ),
        )

        duration = calculate_duration(db, fact.id)
        assert duration is not None
        assert duration["duration_days"] == 5.0
        assert duration["is_exact"] is True
    finally:
        teardown_db()


def test_calculate_duration_point():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-17", name="Test Person 17")
        caregiver = Caregiver(
            id="caregiver-17",
            name="Test Caregiver 17",
            email="test17@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-17",
                subject_type="event",
                subject_id="evt-1",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc),
                created_by_caregiver_id="caregiver-17",
            ),
        )

        duration = calculate_duration(db, fact.id)
        assert duration is not None
        assert duration["duration_days"] == 0.0
        assert "Point event" in duration["note"]
    finally:
        teardown_db()


def test_classify_temporal_status_active():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-18", name="Test Person 18")
        caregiver = Caregiver(
            id="caregiver-18",
            name="Test Caregiver 18",
            email="test18@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        now = utc_now()
        fact = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-18",
                subject_type="state",
                subject_id="state-1",
                temporal_mode=TemporalMode.STATE,
                asserted_start=now - timedelta(days=1),
                asserted_end=now + timedelta(days=1),
                created_by_caregiver_id="caregiver-18",
            ),
        )

        status = classify_temporal_status(fact, now)
        assert status == "active"
    finally:
        teardown_db()


def test_classify_temporal_status_future():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-19", name="Test Person 19")
        caregiver = Caregiver(
            id="caregiver-19",
            name="Test Caregiver 19",
            email="test19@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        now = utc_now()
        fact = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-19",
                subject_type="appointment",
                subject_id="appt-1",
                temporal_mode=TemporalMode.SCHEDULED,
                asserted_point=now + timedelta(days=1),
                created_by_caregiver_id="caregiver-19",
            ),
        )

        status = classify_temporal_status(fact, now)
        assert status == "future"
    finally:
        teardown_db()


def test_case_a_document_date_vs_event_date():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-a", name="Test Person A")
        caregiver = Caregiver(
            id="caregiver-a",
            name="Test Caregiver A",
            email="testa@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-a",
                subject_type="hospitalization",
                subject_id="hosp-1",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc),
                document_time=datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc),
                time_provenance="extracted_document",
                evidence_ids=["ev-1"],
                created_by_caregiver_id="caregiver-a",
            ),
        )

        assert fact.asserted_point.month == 3
        assert fact.document_time.month == 6
        assert fact.asserted_point != fact.document_time
    finally:
        teardown_db()


def test_case_b_approximate_date():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-b", name="Test Person B")
        caregiver = Caregiver(
            id="caregiver-b",
            name="Test Caregiver B",
            email="testb@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-b",
                subject_type="symptom",
                subject_id="sym-1",
                temporal_mode=TemporalMode.EVENT,
                precision=TemporalPrecision.MONTH,
                is_approximate=True,
                lower_bound=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
                upper_bound=datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc),
                source_assertion="around June",
                time_provenance="caregiver_entered",
                evidence_ids=["ev-1"],
                created_by_caregiver_id="caregiver-b",
            ),
        )

        assert fact.precision == TemporalPrecision.MONTH
        assert fact.is_approximate is True
        assert fact.lower_bound is not None
        assert fact.upper_bound is not None
        assert fact.asserted_point is None
    finally:
        teardown_db()


def test_case_c_relative_date_resolved():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-c", name="Test Person C")
        caregiver = Caregiver(
            id="caregiver-c",
            name="Test Caregiver C",
            email="testc@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        frame = create_temporal_reference_frame(
            db,
            person_id="person-c",
            name="Discharge",
            reference_time=datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
            is_resolved=True,
        )

        result = resolve_relative_time(db, "person-c", "3 days after discharge", frame.id)
        assert result.is_resolved is True
        assert result.resolved_point.month == 3
        assert result.resolved_point.day == 4
        assert result.derivation_method == "relative_calculation"
    finally:
        teardown_db()


def test_case_d_relative_date_missing_reference():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-d", name="Test Person D")
        caregiver = Caregiver(
            id="caregiver-d",
            name="Test Caregiver D",
            email="testd@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        result = resolve_relative_time(db, "person-d", "3 days after discharge", None)
        assert result.is_resolved is False
        assert result.resolved_point is None
        assert result.confidence == 0.0
        assert result.derivation_method == "missing_reference"
    finally:
        teardown_db()


def test_case_e_conflicting_dates():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-e", name="Test Person E")
        caregiver = Caregiver(
            id="caregiver-e",
            name="Test Caregiver E",
            email="teste@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact_a = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-e",
                subject_type="hospitalization",
                subject_id="hosp-1",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc),
                time_provenance="source_a",
                evidence_ids=["ev-a"],
                created_by_caregiver_id="caregiver-e",
            ),
        )

        fact_b = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-e",
                subject_type="hospitalization",
                subject_id="hosp-1",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 6, 12, 0, 0, tzinfo=timezone.utc),
                time_provenance="source_b",
                evidence_ids=["ev-b"],
                created_by_caregiver_id="caregiver-e",
            ),
        )

        contradiction = create_temporal_contradiction(
            db,
            TemporalContradictionCreate(
                person_id="person-e",
                subject_type="hospitalization",
                subject_id="hosp-1",
                fact_a_id=fact_a.id,
                fact_b_id=fact_b.id,
                description="Source A: March 4. Source B: March 6.",
                evidence_ids=["ev-a", "ev-b"],
            ),
        )

        assert contradiction.resolution_status == TemporalResolutionStatus.UNRESOLVED
        assert fact_a.asserted_point != fact_b.asserted_point
    finally:
        teardown_db()


def test_case_f_historical_vs_current():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-f", name="Test Person F")
        caregiver = Caregiver(
            id="caregiver-f",
            name="Test Caregiver F",
            email="testf@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-f",
                subject_type="diagnosis",
                subject_id="dx-1",
                temporal_mode=TemporalMode.STATE,
                asserted_point=datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
                precision=TemporalPrecision.YEAR,
                time_provenance="documented",
                evidence_ids=["ev-1"],
                created_by_caregiver_id="caregiver-f",
            ),
        )

        view = get_temporal_view_at_point(db, "person-f", datetime(2026, 3, 5, 12, 0, 0, tzinfo=timezone.utc))
        assert len(view.historical_facts) == 1
        assert view.historical_facts[0]["id"] == fact.id
    finally:
        teardown_db()


def test_case_g_ongoing_state():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-g", name="Test Person G")
        caregiver = Caregiver(
            id="caregiver-g",
            name="Test Caregiver G",
            email="testg@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-g",
                subject_type="condition",
                subject_id="cond-1",
                temporal_mode=TemporalMode.STATE,
                asserted_start=datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
                asserted_end=None,
                precision=TemporalPrecision.YEAR,
                time_provenance="caregiver_entered",
                evidence_ids=["ev-1"],
                created_by_caregiver_id="caregiver-g",
            ),
        )

        assert fact.temporal_mode == TemporalMode.STATE
        assert fact.asserted_start is not None
        assert fact.asserted_end is None
        assert fact.precision == TemporalPrecision.YEAR
    finally:
        teardown_db()


def test_case_h_duration_approximate():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-h", name="Test Person H")
        caregiver = Caregiver(
            id="caregiver-h",
            name="Test Caregiver H",
            email="testh@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-h",
                subject_type="symptom",
                subject_id="sym-1",
                temporal_mode=TemporalMode.EVENT,
                asserted_start=datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
                asserted_end=datetime(2026, 3, 22, 12, 0, 0, tzinfo=timezone.utc),
                is_approximate=True,
                precision=TemporalPrecision.DAY,
                time_provenance="caregiver_entered",
                evidence_ids=["ev-1"],
                created_by_caregiver_id="caregiver-h",
            ),
        )

        duration = calculate_duration(db, fact.id)
        assert duration is not None
        assert duration["duration_days"] == 21.0
        assert duration["is_exact"] is False
    finally:
        teardown_db()


def test_case_i_recurrence_no_inference():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-i", name="Test Person I")
        caregiver = Caregiver(
            id="caregiver-i",
            name="Test Caregiver I",
            email="testi@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        for i in range(3):
            create_temporal_fact(
                db,
                TemporalFactCreate(
                    person_id="person-i",
                    subject_type="fall",
                    subject_id=f"fall-{i}",
                    temporal_mode=TemporalMode.EVENT,
                    asserted_point=datetime(2026, 3, 1 + i * 7, 12, 0, 0, tzinfo=timezone.utc),
                    created_by_caregiver_id="caregiver-i",
                ),
            )

        facts = db.query(TemporalFactModel).filter(TemporalFactModel.person_id == "person-i").all()
        assert len(facts) == 3
        for f in facts:
            assert f.temporal_mode == TemporalMode.EVENT
    finally:
        teardown_db()


def test_case_j_future_event():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-j", name="Test Person J")
        caregiver = Caregiver(
            id="caregiver-j",
            name="Test Caregiver J",
            email="testj@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-j",
                subject_type="appointment",
                subject_id="appt-1",
                temporal_mode=TemporalMode.SCHEDULED,
                asserted_point=datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc),
                precision=TemporalPrecision.DAY,
                time_provenance="caregiver_entered",
                evidence_ids=["ev-1"],
                created_by_caregiver_id="caregiver-j",
            ),
        )

        now = utc_now()
        view = get_temporal_view_at_point(db, "person-j", now)
        assert len(view.future_facts) == 1
        assert view.future_facts[0]["id"] == fact.id
    finally:
        teardown_db()


def test_case_k_overlap():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-k", name="Test Person K")
        caregiver = Caregiver(
            id="caregiver-k",
            name="Test Caregiver K",
            email="testk@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        fact_a = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-k",
                subject_type="medication",
                subject_id="med-a",
                temporal_mode=TemporalMode.STATE,
                asserted_start=datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
                asserted_end=datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc),
                created_by_caregiver_id="caregiver-k",
            ),
        )

        fact_b = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-k",
                subject_type="medication",
                subject_id="med-b",
                temporal_mode=TemporalMode.STATE,
                asserted_start=datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
                asserted_end=datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc),
                created_by_caregiver_id="caregiver-k",
            ),
        )

        relation = create_temporal_relation(
            db,
            TemporalRelationCreate(
                person_id="person-k",
                fact_a_id=fact_a.id,
                fact_b_id=fact_b.id,
                relation_type=TemporalRelationType.OVERLAPS,
                confidence=1.0,
            ),
        )

        assert relation.relation_type == TemporalRelationType.OVERLAPS
    finally:
        teardown_db()


def test_case_l_partial_dates_and_relative_references():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-l", name="Test Person L")
        caregiver = Caregiver(
            id="caregiver-l",
            name="Test Caregiver L",
            email="testl@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        frame = create_temporal_reference_frame(
            db,
            person_id="person-l",
            name="Surgery",
            reference_time=datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
            is_resolved=True,
        )

        fact1 = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-l",
                subject_type="event",
                subject_id="evt-1",
                temporal_mode=TemporalMode.EVENT,
                asserted_point=datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
                precision=TemporalPrecision.DAY,
                created_by_caregiver_id="caregiver-l",
            ),
        )

        fact2 = create_temporal_fact(
            db,
            TemporalFactCreate(
                person_id="person-l",
                subject_type="event",
                subject_id="evt-2",
                temporal_mode=TemporalMode.EVENT,
                precision=TemporalPrecision.MONTH,
                is_approximate=True,
                lower_bound=datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
                upper_bound=datetime(2026, 3, 31, 12, 0, 0, tzinfo=timezone.utc),
                source_assertion="sometime in March",
                created_by_caregiver_id="caregiver-l",
            ),
        )

        resolved = resolve_relative_time(db, "person-l", "2 weeks after surgery", frame.id)

        view = get_temporal_view_at_point(db, "person-l", datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc))

        assert fact1.asserted_point is not None
        assert fact2.lower_bound is not None
        assert resolved.is_resolved is True
        assert len(view.historical_facts) == 1
        assert len(view.unknown_facts) == 1
    finally:
        teardown_db()
