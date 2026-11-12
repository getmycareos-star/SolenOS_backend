# Current medication assertion contract

SolenOS does not create a second medication table. The current medication read is a
projection over the existing `care_assertions` table.

## Identity and fields

A medication is represented by multiple `CareAssertion` rows that share one stable,
unique `subject_id` and use `subject_type = "medication"`.

Supported fields:

- `medication_name` (or `name`): medication identity as reported.
- `medication_status` (or `status`): explicit current status; only the literal
  value `active` is eligible for the current-medication selector.
- `dose` (or `strength`): optional reported dose/strength.

Each assertion must be `assertion_kind = "current_state"`, retain source text,
a source event, or same-person evidence, and have a positive
`review_after_days` value.

## Eligibility

The current-medication projection returns a medication only when:

1. All assertions belong to the authenticated person's care record.
2. The name and explicit active-status assertions have been caregiver-reviewed
   (`assertion_status = "accepted"` or the supported legacy
   `caregiver_reviewed` status).
3. Both assertions remain within their configured review windows.
4. Their epistemic states are reported, observed, or verified—not inferred,
   uncertain, conflicting, or missing.
5. No competing live assertion for the same subject and field has a different
   value. The projection never resolves a conflict by choosing the newest report.

Dose is optional and is included only when it has its own eligible, unambiguous
assertion.

## Review renewal

The review window for an accepted assertion is measured from its latest explicit
caregiver review revision in `care_assertion_revisions.changed_at`, not by
rewriting the original `recorded_at` or observation time. When that window
expires, the medication remains visible in the review queue with an overdue
label. A new explicit review appends another revision and starts a new review
window. The API refuses review when the append-only revision table is missing.

## Interpretation limits

Caregiver-reviewed means a caregiver explicitly checked the report. It is not
independent clinical verification, proof of a prescription, or proof that a
medicine was administered.

Historical medication events, scheduled doses, document-extraction candidates,
and unreviewed reports are not promoted into the current-medication selector.
Medication administration remains a separate append-only Daily Care event.

No migration or new persistence table is required for this contract.


## Caregiver authoring and review endpoints

- `POST /api/v1/care-continuity/medications` accepts a name, optional dose/note,
  and review window. It creates name, explicit `active` status, and optional
  dose assertions under one generated medication `subject_id`. All assertions
  begin with `assertion_status = "reported"` and
  `epistemic_state = "reported"`. The create operation is one transaction.
- `GET /api/v1/care-continuity/medications` returns eligible current records
  separately from `pending_review` caregiver reports.
- `POST /api/v1/care-continuity/medications/{subject_id}/review` records an
  explicit caregiver review of the assertions in one transaction, appends
  assertion revisions, and runs the shared care-continuity reconciliation.
  A review cannot accept conflicting, rejected, superseded, or retracted reports.

The caregiver-facing flow uses a 30-day review window by default. A due date is
not a promise that a notification will be sent. The freshness projection marks
the assertion for review when its window expires.
