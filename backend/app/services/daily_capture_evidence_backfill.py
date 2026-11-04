"""Safe, idempotent backfill of source Evidence for legacy Daily Care events.

The default mode is dry-run. Callers must explicitly pass apply=True to write.
Original CareEvent descriptions are copied byte-for-byte into source_text; no
care facts, timestamps, or reporter identities are inferred.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.care import CareEvent, Caregiver, Evidence

DAILY_CAPTURE_EVENT_TYPES = {"structured_daily_capture", "daily_care_entry"}


def backfill_daily_capture_evidence(
    db: Session,
    *,
    apply: bool = False,
    limit: int = 10_000,
) -> dict[str, Any]:
    if limit < 1 or limit > 100_000:
        raise ValueError("limit must be between 1 and 100000")

    events = (
        db.query(CareEvent)
        .filter(CareEvent.event_type.in_(DAILY_CAPTURE_EVENT_TYPES))
        .order_by(CareEvent.recorded_at.asc(), CareEvent.id.asc())
        .limit(limit)
        .all()
    )
    summary: dict[str, Any] = {
        "mode": "apply" if apply else "dry_run",
        "scanned": len(events),
        "would_create": 0,
        "created": 0,
        "already_linked": 0,
        "would_relink_existing": 0,
        "relinked_existing": 0,
        "skipped_missing_source_text": 0,
        "skipped_unlinked_caregiver": 0,
        "skipped_invalid_existing_links": 0,
        "event_ids": [],
    }

    try:
        for event in events:
            linked_ids = [str(value) for value in (event.evidence_ids or []) if value]
            if linked_ids:
                authorized_count = (
                    db.query(Evidence.id)
                    .filter(
                        Evidence.person_id == event.person_id,
                        Evidence.id.in_(linked_ids),
                    )
                    .count()
                )
                if authorized_count == len(set(linked_ids)):
                    summary["already_linked"] += 1
                else:
                    summary["skipped_invalid_existing_links"] += 1
                continue

            caregiver = (
                db.query(Caregiver)
                .filter(
                    Caregiver.id == event.created_by_caregiver_id,
                    Caregiver.person_id == event.person_id,
                )
                .first()
            )
            if caregiver is None:
                summary["skipped_unlinked_caregiver"] += 1
                continue

            # Never manufacture source wording from a title or derived summary.
            original_source_text = event.description
            if not isinstance(original_source_text, str) or not original_source_text.strip():
                summary["skipped_missing_source_text"] += 1
                continue

            ingestion_key = f"legacy-daily-capture:{event.id}"[:128]
            existing_evidence = (
                db.query(Evidence)
                .filter(
                    Evidence.person_id == event.person_id,
                    Evidence.ingestion_key == ingestion_key,
                )
                .first()
            )
            if existing_evidence is not None:
                summary["would_relink_existing"] += 1
                if apply:
                    event.evidence_ids = [existing_evidence.id]
                    summary["relinked_existing"] += 1
                    summary["event_ids"].append(event.id)
                continue

            summary["would_create"] += 1
            if not apply:
                continue

            evidence = Evidence(
                person_id=event.person_id,
                type="note",
                evidence_kind="caregiver_report",
                epistemic_status="caregiver_reported",
                source_text=original_source_text,
                uploaded_by_caregiver_id=event.created_by_caregiver_id,
                reported_by_caregiver_id=event.created_by_caregiver_id,
                collected_by_caregiver_id=event.created_by_caregiver_id,
                reported_by_label="Caregiver",
                source_type="caregiver_report",
                origin_channel="legacy_daily_capture_backfill",
                collection_method="legacy_event_backfill",
                time_provenance=event.time_provenance or "server_recorded_at",
                occurred_at=event.occurred_at,
                collected_at=event.recorded_at,
                ingestion_key=ingestion_key,
            )
            db.add(evidence)
            db.flush()
            event.evidence_ids = [evidence.id]
            summary["created"] += 1
            summary["event_ids"].append(event.id)

        if apply:
            db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        raise

    return summary
