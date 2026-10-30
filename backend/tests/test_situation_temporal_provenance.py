"""Regressions for typed situation signal IDs and source observation time."""
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.change_enums import ChangeDirection, ChangeType, SituationState, SituationType
from app.models.care import Person, Caregiver, Evidence, CareEvent
from app.models.change import Change, Situation
from app.services.situation_formation import (
    _form_change_situation,
    _group_related_signals,
    update_situations,
)


def make_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_change_situation_resolves_evidence_ids_to_observation_time(monkeypatch):
    db = make_db()
    try:
        person = Person(id="p-time", name="Person")
        caregiver = Caregiver(
            id="c-time", name="Caregiver", relationship="Family", person_id="p-time"
        )
        observed = datetime(2026, 10, 4, 9, tzinfo=timezone.utc)
        evidence = Evidence(
            id="evidence-id", person_id="p-time", type="note",
            source_text="Needed help walking.", uploaded_by_caregiver_id="c-time",
            occurred_at=observed,
        )
        event = CareEvent(
            id="different-event-id", person_id="p-time", event_type="observation",
            status="recorded", occurred_at=datetime(2026, 10, 29, 12, tzinfo=timezone.utc),
            title="Walking", evidence_ids=["evidence-id"],
            created_by_caregiver_id="c-time",
        )
        change = Change(
            id="change-id", person_id="p-time", subject_type="mobility",
            subject_id="mobility", change_type=ChangeType.INCREASE,
            change_direction=ChangeDirection.INCREASING,
            evidence_ids=["evidence-id"], is_active=True,
        )
        db.add_all([person, caregiver, evidence, event, change])
        db.flush()
        monkeypatch.setattr(
            "app.services.situation_formation._build_situation_context",
            lambda *args, **kwargs: {"type": "situation_understanding"},
        )
        monkeypatch.setattr(
            "app.services.situation_formation._derive_alternatives",
            lambda *args, **kwargs: [],
        )
        situation = _form_change_situation(
            db, "p-time", [change], [],
            datetime(2026, 10, 30, 12, tzinfo=timezone.utc),
        )
        assert situation.signal_ids == ["evidence-id"]
        assert situation.temporal_start == observed
        actual_end = situation.temporal_end
        if actual_end.tzinfo is None:
            actual_end = actual_end.replace(tzinfo=timezone.utc)
        assert actual_end == observed
    finally:
        db.close()


def test_change_situation_update_appends_evidence_ids_and_event_observation_time():
    db = make_db()
    try:
        person = Person(id="p-update", name="Person")
        caregiver = Caregiver(
            id="c-update", name="Caregiver", relationship="Family", person_id="p-update"
        )
        start = datetime(2026, 10, 1, 8, tzinfo=timezone.utc)
        observed = datetime(2026, 10, 3, 10, tzinfo=timezone.utc)
        situation = Situation(
            id="s-update", person_id="p-update", situation_type=SituationType.UNKNOWN,
            state=SituationState.ACTIVE, title="Mobility", description="Change",
            signal_ids=["old-evidence"], signal_types=["observation"],
            temporal_start=start, temporal_end=start,
            context={"type": "situation_understanding"}, is_active=True,
        )
        event = CareEvent(
            id="new-event", person_id="p-update", event_type="observation",
            status="recorded", occurred_at=observed, title="New observation",
            evidence_ids=["new-evidence"], created_by_caregiver_id="c-update",
        )
        db.add_all([person, caregiver, situation, event])
        db.commit()
        assert update_situations(db, "p-update", event) == [situation]
        assert situation.signal_ids == ["old-evidence", "new-evidence"]
        assert "new-event" not in situation.signal_ids
        actual_end = situation.temporal_end
        if actual_end.tzinfo is None:
            actual_end = actual_end.replace(tzinfo=timezone.utc)
        assert actual_end == observed
    finally:
        db.close()


def test_pattern_situation_update_preserves_event_id_signal_type():
    db = make_db()
    try:
        person = Person(id="p-pattern", name="Person")
        caregiver = Caregiver(
            id="c-pattern", name="Caregiver", relationship="Family", person_id="p-pattern"
        )
        start = datetime(2026, 10, 1, 8, tzinfo=timezone.utc)
        observed = datetime(2026, 10, 3, 10, tzinfo=timezone.utc)
        situation = Situation(
            id="s-pattern", person_id="p-pattern", situation_type=SituationType.UNKNOWN,
            state=SituationState.ACTIVE, title="Frequency", description="Pattern",
            signal_ids=["old-event"], signal_types=["observation"],
            temporal_start=start, temporal_end=start,
            context={"type": "frequency_situation_understanding"}, is_active=True,
        )
        event = CareEvent(
            id="new-pattern-event", person_id="p-pattern", event_type="observation",
            status="recorded", occurred_at=observed, title="New observation",
            evidence_ids=["new-evidence"], created_by_caregiver_id="c-pattern",
        )
        db.add_all([person, caregiver, situation, event])
        db.commit()
        assert update_situations(db, "p-pattern", event) == [situation]
        assert situation.signal_ids == ["old-event", "new-pattern-event"]
        assert "new-evidence" not in situation.signal_ids
        actual_end = situation.temporal_end
        if actual_end.tzinfo is None:
            actual_end = actual_end.replace(tzinfo=timezone.utc)
        assert actual_end == observed
    finally:
        db.close()


def test_legacy_change_signal_ids_resolve_care_event_time_and_continue_event_namespace(monkeypatch):
    db = make_db()
    try:
        person = Person(id="p-legacy", name="Person")
        caregiver = Caregiver(
            id="c-legacy", name="Caregiver", relationship="Family", person_id="p-legacy"
        )
        observed = datetime(2026, 10, 7, 9, tzinfo=timezone.utc)
        legacy_event = CareEvent(
            id="legacy-event-id", person_id="p-legacy", event_type="observation",
            status="recorded", occurred_at=observed, title="Legacy observation",
            evidence_ids=[], created_by_caregiver_id="c-legacy",
        )
        change = Change(
            id="legacy-change-id", person_id="p-legacy", subject_type="observation",
            subject_id="legacy-event-id", change_type=ChangeType.INCREASE,
            change_direction=ChangeDirection.INCREASING,
            evidence_ids=["legacy-event-id"], is_active=True,
        )
        db.add_all([person, caregiver, legacy_event, change])
        db.flush()
        monkeypatch.setattr(
            "app.services.situation_formation._build_situation_context",
            lambda *args, **kwargs: {"type": "situation_understanding"},
        )
        monkeypatch.setattr(
            "app.services.situation_formation._derive_alternatives",
            lambda *args, **kwargs: [],
        )
        situation = _form_change_situation(
            db, "p-legacy", [change], [],
            datetime(2026, 10, 30, 12, tzinfo=timezone.utc),
        )
        assert situation.temporal_start == observed
        assert situation.temporal_end == observed

        legacy = Situation(
            id="s-legacy", person_id="p-legacy", situation_type=SituationType.UNKNOWN,
            state=SituationState.ACTIVE, title="Legacy", description="Legacy row",
            signal_ids=["legacy-event-id"], signal_types=["observation"],
            temporal_start=observed, temporal_end=observed,
            context={}, is_active=True,
        )
        new_observed = datetime(2026, 10, 9, 10, tzinfo=timezone.utc)
        new_event = CareEvent(
            id="new-legacy-event-id", person_id="p-legacy", event_type="observation",
            status="recorded", occurred_at=new_observed, title="New observation",
            evidence_ids=["new-evidence-id"], created_by_caregiver_id="c-legacy",
        )
        db.add_all([legacy, new_event])
        db.commit()
        updated = update_situations(db, "p-legacy", new_event)
        assert legacy in updated
        assert legacy.signal_ids == ["legacy-event-id", "new-legacy-event-id"]
        assert "new-evidence-id" not in legacy.signal_ids
        actual_end = legacy.temporal_end
        if actual_end.tzinfo is None:
            actual_end = actual_end.replace(tzinfo=timezone.utc)
        assert actual_end == new_observed
    finally:
        db.close()


def test_thread_grouping_never_uses_same_subject_label_as_identity():
    from types import SimpleNamespace

    walking_a = SimpleNamespace(subject_type="mobility", subject_id="walking", evidence_ids=["ev-a"])
    walking_b = SimpleNamespace(subject_type="mobility", subject_id="walking", evidence_ids=["ev-b"])
    transfers = SimpleNamespace(subject_type="mobility", subject_id="transfers", evidence_ids=["ev-c"])
    nutrition = SimpleNamespace(subject_type="nutrition", subject_id="walking", evidence_ids=["ev-a"])

    groups = _group_related_signals([walking_a, walking_b, transfers, nutrition], "evidence_ids")

    # Shared IDs across unrelated domain labels do not collapse domains; all
    # source groups remain separate because source identity is domain-scoped.
    assert len(groups) == 4


def test_thread_grouping_uses_only_explicit_shared_source_ids():
    from types import SimpleNamespace

    first = SimpleNamespace(subject_type="sleep", subject_id="night", event_ids=["event-1"])
    same_subject_source = SimpleNamespace(subject_type="sleep", subject_id="night", event_ids=["event-1"])
    other_subject_same_source = SimpleNamespace(subject_type="sleep", subject_id="overnight", event_ids=["event-1"])
    other_domain_same_source = SimpleNamespace(subject_type="mobility", subject_id="walking", event_ids=["event-1"])
    nearby_but_unrelated = SimpleNamespace(subject_type="sleep", subject_id="night", event_ids=["event-2"])

    groups = _group_related_signals(
        [first, same_subject_source, other_subject_same_source, other_domain_same_source, nearby_but_unrelated],
        "event_ids",
    )

    assert len(groups) == 4
    assert any(len(group) == 2 and first in group and same_subject_source in group for group in groups)
    assert any(group == [other_subject_same_source] for group in groups)
    assert any(group == [other_domain_same_source] for group in groups)
    assert any(group == [nearby_but_unrelated] for group in groups)


def test_thread_grouping_does_not_merge_through_transitive_label_similarity():
    from types import SimpleNamespace

    first = SimpleNamespace(subject_type="mobility", subject_id="walking", evidence_ids=["a"])
    bridge = SimpleNamespace(subject_type="mobility", subject_id="walking", evidence_ids=["a", "b"])
    unrelated = SimpleNamespace(subject_type="mobility", subject_id="walking", evidence_ids=["c"])

    groups = _group_related_signals([first, bridge, unrelated], "evidence_ids")

    assert len(groups) == 2
    assert any(len(group) == 2 and first in group and bridge in group for group in groups)
    assert any(group == [unrelated] for group in groups)
