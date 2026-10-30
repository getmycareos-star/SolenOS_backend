"""10,000 PostgreSQL-backed longitudinal care-record cases.

The corpus contains 1,000 isolated synthetic care histories with ten independently
labelled event records each. It exercises durable relational persistence, attribution,
five concurrent care domains, event-time preservation, source lineage, person scoping,
and fresh-session reconstruction. Existing PostgreSQL contract tests separately cover
contradiction transitions, correction lineage, open questions, authorization, and
concurrent writers. This is synthetic engineering evidence, not clinical validation.
"""
from collections import Counter, defaultdict
import json
from datetime import datetime, timedelta, timezone
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.care import CareEvent, Caregiver, Person, Evidence, Correction
from app.models.care_continuity import CareAssertion
from app.models.uncertainty import Contradiction, OpenQuestion

DATABASE_URL = os.environ.get("SOLENOS_BENCHMARK_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgresql+psycopg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")),
    reason="requires the disposable PostgreSQL benchmark database",
)

HISTORY_COUNT = 1_000
CASES_PER_HISTORY = 10
EXPECTED_CASE_COUNT = HISTORY_COUNT * CASES_PER_HISTORY
DOMAINS = ("medication", "mobility", "nutrition", "sleep", "appointment")
FAMILIES = (
    "conflicting_reports",
    "late_arriving_evidence",
    "backdated_observation",
    "duplicate_source_lineage",
    "independent_reporters",
    "unresolved_thread",
    "incomplete_history",
    "stale_observation",
    "planned_not_completed",
    "correction_followup",
    "time_zone_boundary",
    "repeated_occurrence",
    "cross_domain_near_match",
    "similar_subject_language",
    "missing_optional_context",
    "delayed_recording",
    "partial_document",
    "caregiver_transition",
    "concurrent_care_domains",
    "historical_reconstruction",
)


def _utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def test_10000_postgres_longitudinal_cases_survive_fresh_session_reconstruction():
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]
    reference = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)

    people = []
    caregivers = []
    events = []
    evidence_rows = []
    duplicate_evidence_rows = []
    assertion_rows = []
    contradiction_rows = []
    correction_rows = []
    question_rows = []
    expected = {}

    try:
        with Session() as db:
            for history_index in range(HISTORY_COUNT):
                compact = f"{suffix}{history_index:04d}"
                person_id = f"m10kp{compact}"
                caregiver_a_id = f"m10ka{compact}"
                caregiver_b_id = f"m10kb{compact}"
                family = FAMILIES[history_index % len(FAMILIES)]
                people.append(Person(id=person_id, name=f"Synthetic history {history_index:04d}"))
                caregivers.extend([
                    Caregiver(
                        id=caregiver_a_id,
                        name=f"Synthetic reporter A {history_index:04d}",
                        relationship="family",
                        person_id=person_id,
                    ),
                    Caregiver(
                        id=caregiver_b_id,
                        name=f"Synthetic reporter B {history_index:04d}",
                        relationship="family",
                        person_id=person_id,
                    ),
                ])

                for event_index in range(CASES_PER_HISTORY):
                    case_id = history_index * CASES_PER_HISTORY + event_index
                    event_id = f"m10ke{suffix}{case_id:05d}"
                    domain = DOMAINS[(event_index + history_index) % len(DOMAINS)]
                    reporter_id = (
                        (caregiver_a_id if event_index < CASES_PER_HISTORY // 2 else caregiver_b_id)
                        if family == "caregiver_transition"
                        else (caregiver_a_id if event_index % 2 == 0 else caregiver_b_id)
                    )
                    # The same source anchor is deliberately reused by paired reports;
                    # separate event IDs and occurrence times must remain reconstructable.
                    source_anchor = f"m10ks{suffix}{case_id:05d}"
                    days_ago = (history_index * 37 + event_index * 11) % 730
                    occurred_at = reference - timedelta(
                        days=days_ago,
                        hours=(event_index * 3) % 24,
                        minutes=(history_index * 7) % 60,
                    )
                    if family == "time_zone_boundary":
                        occurred_at = occurred_at.astimezone(
                            timezone(timedelta(hours=5, minutes=30))
                        )
                    subject_id = (
                        f"near-match-{history_index:04d}"
                        if family == "cross_domain_near_match"
                        else f"{domain}-subject-{history_index:04d}"
                    )
                    evidence_metadata = {}
                    if family == "stale_observation":
                        evidence_metadata.update({
                            "observed_at": occurred_at.isoformat(),
                            "max_age_days": 30,
                        })
                    if family == "partial_document":
                        evidence_metadata.update({
                            "quality": "partial",
                            "extraction_status": "partial",
                            "readable": False,
                        })
                    if family == "incomplete_history":
                        evidence_metadata.update({
                            "quality": "partial",
                            "temporal_scope": "historical",
                        })
                    # Backdated and delayed cases intentionally separate event time
                    # from the database-assigned recorded_at timestamp.
                    if family in {"backdated_observation", "historical_reconstruction"}:
                        occurred_at -= timedelta(days=365)
                    if family == "late_arriving_evidence":
                        occurred_at = reference - timedelta(days=10, hours=event_index)
                    if family == "stale_observation":
                        occurred_at = reference - timedelta(days=500)
                    if family == "delayed_recording":
                        occurred_at = reference - timedelta(days=30, hours=event_index)
                    if family == "time_zone_boundary":
                        occurred_at = (reference - timedelta(days=1)).astimezone(
                            timezone(timedelta(hours=-8))
                        )
                    if family == "stale_observation":
                        evidence_metadata["observed_at"] = occurred_at.isoformat()
                    title = f"{family} {domain} event {case_id:05d}"
                    description = (
                        f"Synthetic source report {case_id:05d}; "
                        f"history={history_index:04d}; domain={domain}; reporter={reporter_id}."
                    )
                    event = CareEvent(
                        id=event_id,
                        person_id=person_id,
                        event_type=domain,
                        status=("planned" if family == "planned_not_completed" else "recorded"),
                        occurred_at=occurred_at,
                        occurred_at_timezone=(
                            "America/Los_Angeles" if family == "time_zone_boundary" else "UTC"
                        ),
                        title=title,
                        description=description,
                        evidence_ids=[source_anchor],
                        time_provenance=(
                            "imported_document"
                            if family == "partial_document" or event_index % 3 == 0
                            else "caregiver_observed"
                        ),
                        tags=[family, f"case:{case_id:05d}"],
                        created_by_caregiver_id=reporter_id,
                    )
                    events.append(event)
                    evidence_rows.append(Evidence(
                        id=source_anchor,
                        person_id=person_id,
                        type=("partial_document" if family == "partial_document" else "caregiver_report"),
                        source_text=(
                            f"Partial extract {case_id:05d}: source context unavailable."
                            if family == "partial_document"
                            else f"Original source report {case_id:05d}: {description}"
                        ),
                        uploaded_by_caregiver_id=caregiver_b_id,
                        original_author=("Reporter A" if reporter_id == caregiver_a_id else "Reporter B"),
                        reported_by_label=("Reporter A" if reporter_id == caregiver_a_id else "Reporter B"),
                        reported_by_caregiver_id=(None if family == "incomplete_history" else reporter_id),
                        collected_by_caregiver_id=caregiver_b_id,
                        collected_at=reference + timedelta(minutes=case_id),
                        occurred_at=occurred_at,
                        occurred_at_timezone=(
                            "America/Los_Angeles" if family == "time_zone_boundary" else "UTC"
                        ),
                        reported_at=(
                            reference - timedelta(days=2)
                            if family == "late_arriving_evidence"
                            else reference - timedelta(days=1)
                            if family == "delayed_recording"
                            else reference + timedelta(minutes=case_id)
                        ),
                        evidence_status=(
                            "stale" if family == "stale_observation"
                            else "partial" if family == "partial_document"
                            else "planned" if family == "planned_not_completed"
                            else "reported"
                        ),
                        source_type=("hospital_document" if family == "partial_document" else "caregiver_report"),
                        original_source_reference=f"synthetic://source/{case_id:05d}",
                        extra_metadata=json.dumps(evidence_metadata, sort_keys=True),
                        source_text_sha256=None,
                    ))
                    assertion_id = f"m10kcl{suffix}{case_id:05d}"
                    assertion_value = (
                        "reported_present" if event_index % 2 == 0 else "reported_absent"
                    )
                    assertion_rows.append(CareAssertion(
                        id=assertion_id,
                        person_id=person_id,
                        subject_type=domain,
                        subject_id=subject_id,
                        field="reported_status",
                        assertion_kind="event",
                        asserted_value=assertion_value,
                        source_event_id=event_id,
                        source_label=("Reporter A" if reporter_id == caregiver_a_id else "Reporter B"),
                        source_text=description,
                        evidence_ids=[source_anchor],
                        assertion_status=("planned" if family == "planned_not_completed" else "reported"),
                        observed_at=occurred_at,
                        created_by_caregiver_id=reporter_id,
                    ))

                    if family == "duplicate_source_lineage":
                        duplicate_id = f"m10kdp{suffix}{case_id:05d}"
                        duplicate_evidence_rows.append(Evidence(
                            id=duplicate_id,
                            person_id=person_id,
                            type="derived_summary",
                            source_text=f"Duplicate import of source report {case_id:05d}.",
                            uploaded_by_caregiver_id=caregiver_b_id,
                            original_author=f"Reporter {event_index % 2}",
                            reported_by_label=f"Reporter {event_index % 2}",
                            reported_by_caregiver_id=reporter_id,
                            collected_by_caregiver_id=caregiver_b_id,
                            parent_evidence_id=source_anchor,
                            evidence_status="derived",
                            source_type="derived_summary",
                            original_source_reference=f"synthetic://source/{case_id:05d}",
                        ))

                    if family in {"conflicting_reports", "independent_reporters"}:
                        conflicting_id = f"m10kcf{suffix}{case_id:05d}"
                        assertion_rows.append(CareAssertion(
                            id=conflicting_id,
                            person_id=person_id,
                            subject_type=domain,
                            subject_id=subject_id,
                            field="reported_status",
                            assertion_kind="event",
                            asserted_value=(
                                "reported_absent" if assertion_value == "reported_present"
                                else "reported_present"
                            ),
                            source_event_id=event_id,
                            source_label="Independent conflicting reporter",
                            source_text=f"Conflicting account for case {case_id:05d}.",
                            evidence_ids=[source_anchor],
                            assertion_status="reported",
                            observed_at=occurred_at,
                            created_by_caregiver_id=(
                                caregiver_b_id if reporter_id == caregiver_a_id else caregiver_a_id
                            ),
                        ))
                        contradiction_rows.append(Contradiction(
                            id=f"m10kco{suffix}{case_id:05d}",
                            person_id=person_id,
                            subject_type=domain,
                            subject_id=subject_id,
                            field="reported_status",
                            description=f"Unresolved conflicting reports for case {case_id:05d}.",
                            matching_candidate_ids=[assertion_id, conflicting_id],
                            evidence_ids=[source_anchor],
                            source_assertion_ids=[assertion_id, conflicting_id],
                            resolution_status="unresolved",
                        ))

                    if family == "correction_followup":
                        correction_rows.extend([
                            Correction(
                                id=f"m10kcr1{suffix}{case_id:05d}",
                                person_id=person_id,
                                target_type="care_assertion",
                                target_id=assertion_id,
                                original_text=assertion_value,
                                corrected_text="Reporter cannot confirm the original account.",
                                caregiver_id=reporter_id,
                            ),
                            Correction(
                                id=f"m10kcr2{suffix}{case_id:05d}",
                                person_id=person_id,
                                target_type="care_assertion",
                                target_id=assertion_id,
                                original_text="Reporter cannot confirm the original account.",
                                corrected_text="The claim remains unverified pending source review.",
                                caregiver_id=caregiver_b_id,
                            ),
                        ])

                    if family == "unresolved_thread":
                        question_rows.append(OpenQuestion(
                            id=f"m10kqu{suffix}{case_id:05d}",
                            person_id=person_id,
                            question=f"Clarify the {domain} report for case {case_id:05d}.",
                            subject_type=domain,
                            subject_id=subject_id,
                            status="open",
                            evidence_ids=[source_anchor],
                            created_by_caregiver_id=reporter_id,
                        ))

                    expected[event_id] = {
                        "person_id": person_id,
                        "caregiver_id": reporter_id,
                        "uploader_id": caregiver_b_id,
                        "event_type": domain,
                        "status": ("planned" if family == "planned_not_completed" else "recorded"),
                        "occurred_at": occurred_at,
                        "evidence_id": source_anchor,
                        "assertion_id": assertion_id,
                        "family": family,
                        "case_id": case_id,
                    }

            assert len(people) == HISTORY_COUNT
            assert len(caregivers) == HISTORY_COUNT * 2
            assert len(events) == EXPECTED_CASE_COUNT
            assert len(expected) == EXPECTED_CASE_COUNT

            db.add_all(people)
            db.flush()
            db.add_all(caregivers)
            db.flush()
            db.add_all(events)
            db.flush()
            db.add_all(evidence_rows)
            db.flush()
            db.add_all(duplicate_evidence_rows)
            db.flush()
            db.add_all(assertion_rows)
            db.add_all(contradiction_rows)
            db.add_all(correction_rows)
            db.add_all(question_rows)
            db.commit()

        # Dispose the writer's connection pool and create a new engine. The next
        # reads cannot succeed from an ORM identity map or a reused DB connection.
        engine.dispose()
        engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

        # New SQLAlchemy session and new database connections.
        with Session() as reader:
            persisted = reader.query(CareEvent).filter(
                CareEvent.id.like(f"m10ke{suffix}%")
            ).all()
            assert len(persisted) == EXPECTED_CASE_COUNT, (
                f"expected {EXPECTED_CASE_COUNT} persisted cases; got {len(persisted)}"
            )

            by_person = defaultdict(list)
            family_counts = Counter()
            for row in persisted:
                wanted = expected[row.id]
                assert row.person_id == wanted["person_id"], f"{row.id}: person scope changed"
                assert row.created_by_caregiver_id == wanted["caregiver_id"], (
                    f"{row.id}: reporter attribution changed"
                )
                assert row.event_type == wanted["event_type"], f"{row.id}: care domain changed"
                assert row.status == wanted["status"], f"{row.id}: planned/completed status changed"
                assert _utc(row.occurred_at) == _utc(wanted["occurred_at"]), (
                    f"{row.id}: event time changed"
                )
                assert row.evidence_ids == [wanted["evidence_id"]], (
                    f"{row.id}: source lineage changed"
                )
                evidence = reader.query(Evidence).filter(
                    Evidence.id == wanted["evidence_id"],
                    Evidence.person_id == wanted["person_id"],
                ).one()
                assert evidence.uploaded_by_caregiver_id == wanted["uploader_id"]
                if wanted["family"] == "incomplete_history":
                    assert evidence.reported_by_caregiver_id is None
                else:
                    assert evidence.reported_by_caregiver_id == wanted["caregiver_id"]
                assert evidence.source_text
                assert evidence.source_text_sha256
                metadata = json.loads(evidence.extra_metadata or "{}")
                if wanted["family"] == "stale_observation":
                    assert metadata.get("max_age_days") == 30
                if wanted["family"] == "partial_document":
                    assert metadata.get("readable") is False
                    assert metadata.get("extraction_status") == "partial"
                if wanted["family"] == "incomplete_history":
                    assert metadata.get("temporal_scope") == "historical"
                claim = reader.query(CareAssertion).filter(
                    CareAssertion.id == wanted["assertion_id"],
                    CareAssertion.person_id == wanted["person_id"],
                ).one()
                assert claim.source_event_id == row.id
                assert claim.evidence_ids == [wanted["evidence_id"]]
                assert claim.created_by_caregiver_id == wanted["caregiver_id"]
                assert claim.source_label
                assert row.recorded_at is not None, f"{row.id}: recorded time missing"
                assert row.recorded_at != row.occurred_at, (
                    f"{row.id}: event time and recorded time were conflated"
                )
                if wanted["family"] == "planned_not_completed":
                    assert row.status == "planned"
                    assert claim.assertion_status == "planned"
                if wanted["family"] == "stale_observation":
                    assert evidence.evidence_status == "stale"
                    assert _utc(evidence.occurred_at) < reference - timedelta(days=365)
                if wanted["family"] == "partial_document":
                    assert evidence.evidence_status == "partial"
                    assert "context unavailable" in evidence.source_text
                if wanted["family"] in {"late_arriving_evidence", "delayed_recording"}:
                    assert _utc(evidence.reported_at) > _utc(evidence.occurred_at)
                if wanted["family"] == "incomplete_history":
                    assert evidence.reported_by_caregiver_id is None
                    assert evidence.uploaded_by_caregiver_id is not None
                if wanted["family"] == "time_zone_boundary":
                    assert evidence.occurred_at_timezone == "America/Los_Angeles"
                assert f"case:{wanted['case_id']:05d}" in (row.tags or [])
                assert wanted["family"] in (row.tags or [])
                by_person[row.person_id].append(row)
                family_counts[wanted["family"]] += 1

            assert len(by_person) == HISTORY_COUNT
            assert reader.query(Evidence).filter(
                Evidence.id.like(f"m10ks{suffix}%")
            ).count() == EXPECTED_CASE_COUNT
            assert reader.query(CareAssertion).filter(
                CareAssertion.id.like(f"m10kcl{suffix}%")
            ).count() == EXPECTED_CASE_COUNT
            conflict_records = reader.query(Contradiction).filter(
                Contradiction.id.like(f"m10kco{suffix}%")
            ).all()
            assert len(conflict_records) == 100 * CASES_PER_HISTORY
            assert all(row.resolution_status == "unresolved" for row in conflict_records)
            assert all(len(row.source_assertion_ids or []) == 2 for row in conflict_records)
            for conflict in conflict_records:
                conflict_claims = reader.query(CareAssertion).filter(
                    CareAssertion.person_id == conflict.person_id,
                    CareAssertion.id.in_(conflict.source_assertion_ids),
                ).all()
                assert len(conflict_claims) == 2
                assert len({claim.asserted_value for claim in conflict_claims}) == 2
            correction_records = reader.query(Correction).filter(
                Correction.id.like(f"m10kcr%{suffix}%")
            ).all()
            assert len(correction_records) == 2 * 50 * CASES_PER_HISTORY
            question_records = reader.query(OpenQuestion).filter(
                OpenQuestion.id.like(f"m10kqu{suffix}%")
            ).all()
            assert len(question_records) == 50 * CASES_PER_HISTORY
            assert all(row.status == "open" for row in question_records)
            duplicate_records = reader.query(Evidence).filter(
                Evidence.id.like(f"m10kdp{suffix}%")
            ).all()
            assert len(duplicate_records) == 50 * CASES_PER_HISTORY
            assert all(row.parent_evidence_id for row in duplicate_records)
            assert set(family_counts) == set(FAMILIES)
            assert sum(family_counts.values()) == EXPECTED_CASE_COUNT
            assert all(count == 50 * CASES_PER_HISTORY for count in family_counts.values())

            # Each history must retain five care domains and both independent
            # reporter identities. No event from another person may enter its scope.
            caregiver_owner = {
                caregiver.id: caregiver.person_id
                for caregiver in reader.query(Caregiver).filter(
                    (Caregiver.id.like(f"m10ka{suffix}%"))
                    | (Caregiver.id.like(f"m10kb{suffix}%"))
                ).all()
            }
            assert len(caregiver_owner) == HISTORY_COUNT * 2
            for person_id, rows in by_person.items():
                assert len(rows) == CASES_PER_HISTORY, f"{person_id}: wrong event count"
                assert {row.event_type for row in rows} == set(DOMAINS)
                assert len({row.created_by_caregiver_id for row in rows}) == 2
                if "repeated_occurrence" in (rows[0].tags or []):
                    counts_by_domain = Counter(row.event_type for row in rows)
                    assert set(counts_by_domain) == set(DOMAINS)
                    assert all(count == 2 for count in counts_by_domain.values())
                if "caregiver_transition" in (rows[0].tags or []):
                    ordered_rows = sorted(rows, key=lambda item: item.id)
                    first_half = {item.created_by_caregiver_id for item in ordered_rows[:5]}
                    second_half = {item.created_by_caregiver_id for item in ordered_rows[5:]}
                    assert len(first_half) == 1 and len(second_half) == 1
                    assert first_half.isdisjoint(second_half)
                assert all(caregiver_owner[row.created_by_caregiver_id] == person_id for row in rows)
                assert all(row.person_id == person_id for row in rows)

            # Query by one selected history and prove adjacent histories do not leak.
            selected_person = f"m10kp{suffix}0500"
            selected = reader.query(CareEvent).filter(
                CareEvent.person_id == selected_person
            ).all()
            assert len(selected) == CASES_PER_HISTORY
            assert all(row.person_id == selected_person for row in selected)

        print(f"MESSY_CARE_POSTGRES_CASES_DECLARED={EXPECTED_CASE_COUNT}")
        print(f"MESSY_CARE_POSTGRES_CASES_PERSISTED={len(persisted)}")
        print(f"MESSY_CARE_POSTGRES_HISTORIES={HISTORY_COUNT}")
        print(f"MESSY_CARE_POSTGRES_FAMILIES={len(FAMILIES)}")
        print("MESSY_CARE_POSTGRES_FRESH_SESSION_RETRIEVAL=PASS")
    finally:
        with Session() as cleanup:
            person_ids = [person.id for person in people]
            if person_ids:
                cleanup.query(OpenQuestion).filter(OpenQuestion.person_id.in_(person_ids)).delete(
                    synchronize_session=False
                )
                cleanup.query(Contradiction).filter(Contradiction.person_id.in_(person_ids)).delete(
                    synchronize_session=False
                )
                cleanup.query(Correction).filter(Correction.person_id.in_(person_ids)).delete(
                    synchronize_session=False
                )
                cleanup.query(CareAssertion).filter(CareAssertion.person_id.in_(person_ids)).delete(
                    synchronize_session=False
                )
                cleanup.query(CareEvent).filter(CareEvent.person_id.in_(person_ids)).delete(
                    synchronize_session=False
                )
                cleanup.query(Evidence).filter(Evidence.person_id.in_(person_ids)).delete(
                    synchronize_session=False
                )
                cleanup.execute(
                    text("DELETE FROM care_state_revisions WHERE person_id = ANY(:person_ids)"),
                    {"person_ids": person_ids},
                )
                cleanup.query(Caregiver).filter(Caregiver.person_id.in_(person_ids)).delete(
                    synchronize_session=False
                )
                cleanup.query(Person).filter(Person.id.in_(person_ids)).delete(
                    synchronize_session=False
                )
                cleanup.commit()
        engine.dispose()
