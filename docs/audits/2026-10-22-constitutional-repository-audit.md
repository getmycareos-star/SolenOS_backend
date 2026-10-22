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
- Backend: direct inspection of `backend/app/main.py` and `backend/app/api/v1.py`, plus code search, found no registered conversation-message route. This remains a source-level finding; deployed route behavior has not been probed.
- Impact: Companion is not demonstrated to have a complete request path and is likely to fail.
- Status: **Source-level route gap confirmed; live HTTP confirmation remains outstanding.**
- Follow-up frontend change: failure handling now distinguishes a 404 from an unknown server outcome and warns users to check the care record before retrying when receipt is uncertain. It does not implement the missing backend workflow.
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

### P1 — Production secret default removed; deployed environment remains unverified
- Backend: `backend/app/core/config.py` now defaults `secret_key` to an empty string; production validation rejects empty or shorter-than-32-character keys and rejects the legacy placeholder.
- Tests: `backend/tests/test_production_config.py` includes a test that expects production configuration to reject an empty key.
- Impact: a missing key should fail production configuration rather than silently use a known default. This does not establish whether Railway has a valid secret set or whether the deployed service starts.
- Status: **Source change and regression-test source verified; test execution and deployed environment unverified.**
- Required verification: check production environment configuration and live startup logs without exposing the secret. Do not print or commit secret values.

### P1 — Readiness now checks ORM schema presence, but not full persistence
- Backend: `backend/app/main.py` `/health` returns process liveness. `/ready` now executes `SELECT 1` and compares the database's table names against all ORM-managed tables in `Base.metadata`; missing tables cause HTTP 503.
- Impact: this catches missing ORM tables, but does not prove column compatibility, successful writes, retrieval after restart, authorization isolation, or safe migrations.
- Status: **Readiness source change verified; live response and actual persistence unverified.**
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


## Follow-up implementation activity (2026-10-22)

The following source changes were committed after the initial inspection:

- Backend configuration: removed the known placeholder signing-key default and added an empty-key rejection test. Commits: `a481312e26733fb91d0b28f94a57e09e2e8ac491`, `8cc1eb90093d9bc4da97f81e793f4b65c183d961`.
- Backend readiness: checks that all ORM-managed tables exist before returning ready. Commit: `bf1effde971ea93c38c79c640c4a0e491555d7f6`.
- Backend CI: added a GitHub Actions workflow to install the backend and run `pytest backend/tests -q`. Commit: `0a6e6572252c6c24b3e99b0ed36c7c12010914dd`.
- Frontend Companion: removed the unsupported claim that replies are backed by local evidence and made missing-endpoint failures explicit. Commit: `64f5b297244cc076db742500d89657e9d94df9d3`.
- Frontend CI: added a workflow for `npm ci`, TypeScript checking, Jest tests, and a Vite production build. Commit: `b68053524d407a0ee7e7954848bf8c40abf2fe6c`.
- API reference: removed the shared `default_caregiver` fallback recommendation. Commit: `cf5b8ee45d78ae21bfdddfd9cba519ea5b7da16f`.

These are repository/source changes only. Neither CI workflow has a confirmed run result in this report; no build, test suite, live API request, or end-to-end persistence test is claimed as passing. The missing Companion backend workflow and server-side identity/authorization remain unresolved high-priority issues.


## Follow-up implementation activity (continued)

- Companion error semantics: updated `src/pages/Companion.tsx` so a generic server/network failure no longer claims the message definitely was not sent. It advises checking the care record before retrying to avoid duplicate submissions. Commit: `382cbe404fe9109ce39b7073b834fa83987024c1`.
- Readiness regression test: added `test_readiness_rejects_incomplete_schema` in `backend/tests/test_daily_capture.py`, constructing an in-memory database with only the persons table and expecting `/ready` to return 503. Commit: `2670e0b96ddf6c142db7d42b481472869fc18201`.

The new test has been committed but not executed in this environment. The backend route files inspected in this follow-up still do not demonstrate server-issued anonymous sessions or authenticated authorization for every sensitive endpoint. Relationship checks on some write routes are integrity checks only and must not be described as authentication. The production identity-isolation gap remains open.


## 2026-10-22 follow-up — anonymous session authorization

Implemented in source:
- Added `POST /api/v1/sessions/anonymous`: creates a care recipient, an email-free caregiver identity, and a 365-day server-issued opaque bearer capability with token rotation through a refresh endpoint. The database stores only a SHA-256 token digest.
- Added a global guard for `/api/v1/*` care APIs. Except for the bootstrap route and explicitly non-personal time helpers, requests require a valid bearer session and a target person matching the session's server-stored scope. Child-resource routes are resolved through ORM ownership when possible; unresolvable/unscoped requests fail closed.
- Caller-provided caregiver attribution must match the caregiver bound to the session.
- Made caregiver email optional and added a PostgreSQL startup compatibility migration for existing `caregivers.email NOT NULL` schemas.
- Added tests for anonymous bootstrap, token storage, missing-token denial, cross-person read denial, and caregiver-attribution spoofing.

Not yet verified:
- The test suite has not been executed in this environment. Legacy route-behavior tests explicitly bypass the session middleware through a pytest fixture; the dedicated anonymous-session tests keep the middleware enabled and exercise bootstrap, missing-token denial, cross-person denial, attribution spoofing, situation-write binding, and token rotation.
- The frontend now creates the anonymous session on first care capture, stores the bearer capability separately from its client-generated care key, sends the token through the shared API client, and authenticates the primary POST /api/situation capture. The existing GET /api/situation hydration path is still not implemented in backend/app/main.py, and other direct fetch call sites have not all been migrated; end-to-end frontend continuity is therefore not complete.
- The production Railway database migration and live endpoints have not been exercised. SQLite databases created before this change cannot have the email nullability changed by the PostgreSQL migration; they need an explicit SQLite migration before using anonymous caregiver creation.
- The guard is deliberately fail-closed for routes that cannot be tied to a person scope; some legacy routes may require route-specific ownership handling rather than being available immediately.


## Follow-up implementation commits

- Anonymous session model and bootstrap: `755f9f1c904ebf87917fdc49c8d4d40989d7db14`, `029436bb7e46f983048954a26cf5b6683e3e821c`.
- Database registration and scoped enforcement: `072b254917fffaa57dd8376a73e54dbbf073291f`, `3d102f52cb673880abfe56744e3ff520044938d5`, `a0367ae3e7ceae49356d7bed5f1a46b89cae4a72`, `faa6674490734ff3af33fd02ac3bd15a3288a375`, `1c5d67f3301877bf7a83339f60f5702675111e46`, `60e178f912451720c976891679553d2573745ed6`.
- Anonymous identity and schema compatibility: `00f46dc2967865aa303b66bcff360f592d2d7eeb`, `c55c80180304ceae7b84b89d5b792bf2758117d4`, `1dacb996ef11230ee736b4a4ca62aefec6e264f2`, `79c1e091ef159718576e1e8499448eca970aa2f7`, `862db155954f2e6edd9cc3b809486a281bceaea0`, `705f2d92115c6575648c5d655cd227d362c1af62`.
- Security tests and legacy test isolation: `f88e0b4985c90603f5757d4d3300bea7ebc39dc6`, `b4858a41a06963cbf9b57a680ac25d342f6f08c9`, `0c58abc23a18ac81f3fb83407f6c501a570003dd`, `e8854d809c959fde27416f9e166f5c18ea132a90`, `de00e9ac398d2c83d2d5411eaf94e21e24b08277`, `d6e6283ba4a3cc8f5119f4ccb89738cccb840987`.
- Frontend anonymous-session client and first-capture integration: `1fca01141c933cc25d30f0c3e962bcce3e7486c6`, `15566c58381e42af490d77924e65684d5626f097`, `9f23a2209f2bc3c406db981029637d83a945d076`, `0e0cf907e75189329239a2e27cd5192078118b2e`.


Latest follow-up commits after the initial session implementation:
- Backend bootstrap, refresh and situation-scope binding: `0e77b1cf4cdf44773c9da5ef2731684534f01962`, `1c5d67f3301877bf7a83339f60f5702675111e46`, `b0c240dd33c475c074f18011e6be1ce0be55c96f`, `60e178f912451720c976891679553d2573745ed6`, `705f2d92115c6575648c5d655cd227d362c1af62`.
- Auth test isolation and rotation coverage: `b4858a41a06963cbf9b57a680ac25d342f6f08c9`, `0c58abc23a18ac81f3fb83407f6c501a570003dd`, `e8854d809c959fde27416f9e166f5c18ea132a90`, `de00e9ac398d2c83d2d5411eaf94e21e24b08277`, `adc3e18c3a91bf0d3086de15d195bb468126fb9c`.
- Frontend token refresh and care-person ID alignment: `d02bcbe6b12e034f168bf9f78aedc1e1b90a85a7`, `76923200498b089f8e209ca91f221952933dbe75`.


Final source follow-up after the prior report update:
- Preserved the client durable care key separately from server-bound caregiver attribution in situation capture: `de8724a96274a703db0fa775fae3739f055c2796`, `3d5c20c2c499cb3e954366bd232b801daf80d4a7`, `ceca7e7d2350332af26e92a408b49c88b6b27408`, `2234d420f79c57fdb1294ff33562718068245b18`.
- Rejected conflicting person IDs across route, query and request body: `67fb1fff13705be213aa4c50d9f0625aef526b53`.
- Added the test HTTP dependency to the backend CI workflow: `cc614da7ce4335ae529e223baf44ac487025477b`.
- Frontend first-capture bearer integration and session recovery safeguards: `1fca01141c933cc25d30f0c3e962bcce3e7486c6`, `15566c58381e42af490d77924e65684d5626f097`, `9f23a2209f2bc3c406db981029637d83a945d076`, `0e0cf907e75189329239a2e27cd5192078118b2e`, `d02bcbe6b12e034f168bf9f78aedc1e1b90a85a7`, `76923200498b089f8e209ca91f221952933dbe75`.

The source is committed, but CI has not been observed completing and the Railway deployment is not live-verified. Do not treat this as production sign-off until the backend tests run, the SQLite migration limitation is resolved where applicable, and the deployed frontend/backend capture→read→refresh path is executed.
