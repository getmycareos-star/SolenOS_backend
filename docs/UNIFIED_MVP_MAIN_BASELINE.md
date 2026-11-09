# SolenOS Unified MVP — Main-Branch Architecture Baseline

**Snapshot:** 2026-11-09. This records observed code, not a proposed architecture. Community and Qwen are outside scope.

## Repositories inspected
- Frontend main: `getmycareos-star/SolenOS-landing` at `0b81502b37ee564e59532fd6e69d0e78e90f54cb`
- Backend main: `getmycareos-star/SolenOS_backend` at `5c60d6a877d4358e208991cb60440a93f613a5a0`

## Active UI → API → service → persistence → retrieval map

| Operation | Frontend | API and backend owner | Persisted owner | Retrieval |
|---|---|---|---|---|
| Quick Daily Care | `daily-care/page.tsx` → `QuickDailyCheckIn.tsx` | `POST /api/v1/daily/log` → `api/v1.py:create_structured_daily_log` → `_save_daily_capture_event` | `CareEvent` + linked `Evidence` | `GET /api/v1/care-continuity/current-care`; Companion LCR retrieval |
| Structured Daily Care | Same page → `StructuredDailyCapture.tsx` | Same API and persistence service | `CareEvent` + linked `Evidence`; medication/vitals/scales remain in event detail JSON | Same LCR and Companion reads |
| LCR | `workspace/page.tsx` → `LCRCurrentUnderstanding.tsx` | `GET /api/v1/care-continuity/current-care` → `care_continuity.py:current_care_projection` → `build_lcr_context` | Read projection over multiple source tables | Same bounded projection; per-source limits are exposed |
| LCR review/integrity | LCR review, evidence, claim and conflict panels | `/api/v1/care-continuity/*`, `/api/v1/registered-actions/*` | Evidence, assertions, facts, reviews, revisions and action records | Scoped care-continuity and registered-action reads |
| Companion | `AICompanion.tsx` | `POST /api/v1/conversation/message` → `collaboration.py:companion_message` → `unified_lcr_retrieval.py` | Conversation session/messages; AI proposals in `AIOutputLCRProposal` | Shared LCR retrieval; proposal review/history via `companion_review.py` |
| Documents | `workspace/documents/page.tsx` | `POST /api/v1/upload` → `collaboration.py:upload_evidence`; extraction via `document_lifecycle.py`; intelligence via `document_intelligence.py` | `Evidence`, persisted original upload, extraction analyses and review/proposal records | `GET /api/v1/evidence/{person_id}`, download and document-analysis routes; LCR/Companion evidence context |
| Reminders | `workspace/reminders/page.tsx` | `/api/v1/reminders/*` → `reminders.py` | `CareReminder` | Person-scoped list and status routes |
| Appointments | `workspace/appointments/page.tsx` → `AppointmentWorkspace.tsx` | `/api/v1/appointment-workspaces/*` → `appointment_workspace.py` | Appointment workspace records, revisions and proposals | Workspace, preparation brief and proposal history |
| Handoff preparation | Companion → `CareHandoffSharing.tsx` | `POST /api/v1/handoff-drafts/prepare` → `handoff_drafts.py` | `HandoffDraft` and source snapshots/fingerprints | Draft read/revision/approval; handoff delivery routes |
| Open loops | LCR/open-loop UI | `/api/v1/open-loops/*` → `open_loops.py` | Open-loop, occurrence and append-only history records | Scoped list/detail/history routes |

## Existing backend data owners

- `CareEvent` and `Evidence`: `app/models/care.py`.
- LCR assertions, evidence observations, claim reviews/assessments, revisions and generic continuity resources: `app/models/care_continuity.py` plus migration-created tables.
- Daily Care write service: `_save_daily_capture_event` in `app/api/v1.py`.
- LCR projection: `app/services/lcr_context.py:build_lcr_context`.
- Companion retrieval: `app/services/unified_lcr_retrieval.py`.
- Reminders, appointments, open loops, handoffs and conversations retain separate model/lifecycle owners.

## Verified gaps and legacy paths

1. There is more than one ingestion implementation. Active Daily Care uses `_save_daily_capture_event`. The older `POST /api/situation` route in `main.py` uses `process_situation_input`. The current workspace home mounts `LCRCurrentUnderstanding`, not `CognitiveWorkspace`; that older capture component is not imported by the current home route. The care-continuity page still uses the situation read to refresh its older situation-thread view.
2. `POST /api/v1/events` directly constructs `CareEvent` instead of using the Daily Care service. It is now session-scoped, but remains a second write implementation.
3. `build_lcr_context` is a multi-source projection, not one normalized event store. Write ownership and reconciliation remain distributed.
4. Structured medication/vitals/scales are stored inside the Daily Care event detail rather than separate typed records.
5. Documents use separate upload, extraction and intelligence steps. Original uploaded bytes remain the source; extraction is not automatically a verified LCR fact.
6. Reminders, open loops, appointments, handoff drafts and handoffs have separate persistence/lifecycle entities. Their source links must be verified per workflow.
7. Legacy `v1.py` observation/reasoning and person/caregiver read routes were not found in current frontend source searches. Session checks have been added to person/caregiver reads, correction history, observation and reasoning endpoints. Other legacy routes still need classification/security review before being considered safely obsolete.
8. `app/api/actions.py` contains action CRUD handlers but is not included by `app/main.py`; it is not part of the active HTTP contract.
9. `src/api/client.ts` is the normal session-aware `/api/v1` client. The care-continuity page has direct fetches for its situation and relationship routes, with explicit bearer headers.

## Session-scope fixes committed on backend main

- Event creation; insight create/list; correction create/list.
- Observation create/list/trends; reasoning-memory create/list; reasoning summary; learning-event list.
- Person and caregiver reads.
- Companion care-update review reads.

Regression tests were added for anonymous and cross-person access. **CI is pending; no test pass is claimed here.**

## Phase 1 status: partial

Still required to close Phase 1:
- Enumerate all mounted care-data routes and classify active, legacy-supported or obsolete.
- Verify the mounted route contracts against deployed OpenAPI.
- Trace document binary storage, extraction analyses and evidence-claim relationships end-to-end.
- Trace handoff receipts and outcomes to their exact models/source references.
- Verify each `SOURCE_SPECS` source against its table and UI consumer.
- Verify deployed frontend/backend SHAs and live workflow calls.

This baseline adds no product capability.
