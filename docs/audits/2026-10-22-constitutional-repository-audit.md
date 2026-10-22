# SolenOS Constitutional Repository Audit
Date: 2026-10-22
Scope: public default branches of `getmycareos-star/SolenOS-landing` and `getmycareos-star/SolenOS_backend`.
Method: GitHub code search and direct reads of named source files. No local checkout, build, test execution, Railway access, database access, or live HTTP request was available during this audit.

## Executive result

This is a source-level audit, not a production sign-off. The repository evidence supports several high-priority findings, but does not justify claiming that 20 distinct problems have been verified or fixed. No end-to-end tests were executed in this audit. Findings below distinguish directly observed source facts from risks requiring runtime verification.

## Prioritised findings

### P0 — Companion calls a route not found in the inspected backend source
- Frontend: `src/pages/Companion.tsx` calls `postJSON('/conversation/message', ...)`.
- API client: `src/api/client.ts` prefixes most requests with `/api/v1`, so the request resolves to `/api/v1/conversation/message`.
- Backend: searched indexed default-branch source for conversation-message route and inspected the API route search results; no matching route was found. Existing route examples include `backend/app/api/v1.py`, `backend/app/api/safety.py`, and `backend/app/api/causal.py`.
- Impact: Companion is not demonstrated to have a complete request path and is likely to fail unless the route is registered in unindexed or otherwise uninspected code.
- Status: **Verified frontend call; backend absence strongly indicated but requires route-table/live HTTP confirmation.**
- Required verification: enumerate `app.routes` in the deployed app and send an authenticated/authorized test request; do not fabricate a reply or mock the route as a fix.

### P0 — Server-side authentication and authorization are not demonstrated
- Frontend: `src/api/client.ts` explicitly says backend session endpoints are not implemented; `checkAuthState()` returns `{ authenticated: false, sessionInfo: null }`.
- Backend: code search for `Depends current_user` and authentication/session/token handlers returned no results. This is not exhaustive proof that every sensitive route lacks authorization.
- Impact: caregiver-supplied identifiers must not be treated as authorization. If sensitive routes trust client-provided `person_id` or `caregiver_id`, cross-person disclosure or mutation is possible.
- Status: **High-risk architecture gap; route-by-route authorization not fully verified.**
- Required verification: inspect every sensitive endpoint and test that user A cannot read, mutate, search, export, or infer user B's records. Anonymous use needs a server-issued, scoped identity/capability—not a client-chosen identifier alone.

### P0 — Durable care key is not equivalent to authenticated identity
- Frontend: `src/lib/care-identity/durable-care-key.ts` mints a per-browser `care_...` key and rejects `default_caregiver` and `sess_...` values in several helpers.
- Impact: random browser-local IDs reduce accidental shared-key collisions, but they do not authenticate a person, prove ownership, or authorize server access. A client-visible identifier can be copied or forged unless the backend binds it to a protected session/capability.
- Status: **Verified design distinction; exploitability depends on backend route enforcement.**
- Required fix: preserve no-forced-registration onboarding while using server-controlled anonymous sessions and explicit ownership/consent boundaries.

### P1 — Production secret fallback remains in source, but a guard exists
- Backend: `backend/app/core/config.py` defaults `secret_key` to `change-me-in-production`; `validate_production_configuration()` raises in production for that value or a key shorter than 32 characters.
- Tests: `backend/tests/test_production_config.py` covers rejection of SQLite and a default/short secret, plus acceptance of PostgreSQL and a long secret.
- Impact: the guard is a meaningful safeguard; this source alone does not establish whether Railway has a valid secret set or whether the deployed service starts.
- Status: **Guard verified in source; deployed environment unverified.**
- Required verification: check production environment configuration and live startup logs without exposing the secret. Do not print or commit secret values.

### P1 — Readiness checks database connectivity, not full persistence
- Backend: `backend/app/main.py` `/health` returns process liveness; `/ready` executes `SELECT 1`.
- Impact: this does not prove schema compatibility, record creation, retrieval, refresh persistence, authorization isolation, or migrations.
- Status: **Verified endpoint semantics from source.**
- Required verification: run a disposable end-to-end create → retrieve → restart/refresh → retrieve → delete workflow against a test database and confirm isolation.

### P1 — Community UI depends on backend truth and must not be accepted from UI appearance
- Frontend: `src/pages/Community.tsx` fetches and renders posts, author labels, reactions, stage labels, and community-experience matches through API calls.
- Code search did not find `SAMPLE_POSTS` in the current default branch. A repository script and production-safety test explicitly guard against examples such as fabricated names, `142 caregivers`, and `4.2 helpfulness`.
- Impact: those checks are positive evidence, but do not prove the backend is returning only real, correctly attributed records.
- Status: **No sample-post constant found by search; live community provenance unverified.**
- Required verification: inspect backend fixtures/seeds and production responses; prove demo/test data is excluded from production.

### P1 — API routing differs between development and production
- Frontend: `src/api/client.ts` uses same-origin `/api/v1`; `vite.config.ts` proxies `/api` to `http://localhost:8000`; `netlify.toml` redirects `/api/*` to the Railway backend.
- Backend: `backend/app/main.py` registers the v1 router under `/api/v1` and a separate situation endpoint under `/api/situation`.
- Impact: path rewrites and environment-specific routing must be verified for each workflow. Static configuration does not prove the deployed Netlify/Railway path works.
- Status: **Configuration observed; live route behavior unverified.**
- Required verification: test the deployed frontend through the Netlify proxy, including both `/api/v1/*` and `/api/situation`.

### P2 — API documentation still describes a shared default caregiver
- Frontend docs: `docs/11-api-reference/README.md` documents `POST /api/observations` with an optional `caregiver_id` and says the default is `default_caregiver`.
- Frontend identity helper: `src/lib/care-identity/durable-care-key.ts` says shared placeholder IDs must be rejected.
- Impact: contradictory documentation can lead future code or clients to reintroduce unsafe identity behavior.
- Status: **Verified documentation inconsistency.**
- Required fix: update the API reference to describe the actual current endpoint contract and fail-closed identity requirement; do not imply a route exists unless it does.

## Ranked remediation sequence

1. Establish route-by-route authorization and cross-person isolation tests before exposing real care data.
2. Confirm a secure anonymous-session design that supports accountless use without trusting arbitrary client identifiers.
3. Resolve the Companion endpoint mismatch by implementing the real backend workflow or explicitly disabling the unavailable feature; never return synthetic success.
4. Run deployed route and persistence checks against a disposable test record.
5. Verify production environment settings and secret handling without exposing credentials.
6. Verify production seed/fixture exclusion and community author provenance.
7. Correct stale API documentation and add contract tests for every documented route.
8. Run frontend typecheck/build/tests and backend test suite from a clean checkout; capture exact commands and results.

## Not verified / blocked

- Live Railway health or readiness response, because no live request was executed.
- Actual production `SECRET_KEY`, `DATABASE_URL`, CORS behavior, database contents, schema state, and service logs.
- Clean frontend build, TypeScript check, frontend tests, backend tests, and migration execution.
- Full endpoint inventory and exhaustive per-route authorization audit.
- Real create/read/refresh persistence, offline reconciliation, exports, deletion, professional multi-patient access, and AI evidence traceability.
- Any claim that 20 problems have been fixed or that end-to-end behavior passes.

## Acceptance rule

Do not declare SolenOS production-ready until the test evidence demonstrates: authorized identity binding; cross-person access denied; no fabricated production records; correct route contracts; real persistence; failure states surfaced honestly; and reproducible clean builds/tests. A green build or a successful `/ready` response is insufficient.
