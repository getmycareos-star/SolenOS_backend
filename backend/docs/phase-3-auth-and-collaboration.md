# Phase 3 authentication and collaboration API

## Required service configuration

Set these in the backend service's environment, not in the frontend repository:

- `DATABASE_URL`: managed PostgreSQL connection string. Production configuration rejects SQLite.
- `SECRET_KEY`: unique random secret, at least 32 characters. It signs expiring bearer tokens; do not reuse the Supabase database password.
- `ACCOUNT_ENROLLMENT_KEY`: random enrollment secret of at least 24 characters. Required to register caregiver accounts. Share only with intended caregivers through a private channel.
- `ACCOUNT_ADMIN_KEY`: separate random administrator secret of at least 32 characters. Required to provision professional and SolenOS Team accounts. Never expose it to the browser or commit it to Git.
- `ACCESS_TOKEN_EXPIRE_MINUTES`: optional token lifetime; default is 1440 minutes.

Authentication intentionally fails closed when `SECRET_KEY` or the enrollment secret is missing. A successful deployment should expose `/health` and `/ready` before any browser flow is considered available.

## Authentication routes

- `POST /api/v1/auth/register`: caregiver account enrollment. An existing caregiver record can be linked only when both caregiver ID and email match. Otherwise, the request must include a caregiver name and care-recipient name, and it creates the person, caregiver, and account together. Registration never accepts a role field.
- `POST /api/v1/auth/login`: verifies a PBKDF2-HMAC-SHA256 password hash and issues a signed, expiring bearer token.
- `GET /api/v1/auth/me`: verifies token signature, expiry, account activity, and account linkage.
- `POST /api/v1/auth/provision`: requires the `X-SolenOS-Admin-Key` header and an existing caregiver record whose email matches the requested account. Only `professional` or `team` roles can be provisioned through this route.

The browser must send `Authorization: Bearer <access_token>` on protected API calls. The frontend currently stores the short-lived access token in localStorage; a hardened deployment should evaluate HttpOnly Secure SameSite cookies and a CSRF strategy.

## Central authorization

The FastAPI middleware requires authentication for `/api/v1/*` and `/api/situation`, except for explicit enrollment/login/provision, health/docs, and CORS preflight paths. It checks account activity and signed token claims, binds supplied caregiver IDs to the authenticated caregiver, resolves direct person IDs and known child-resource IDs to a care recipient, and enforces:
- Caregiver accounts: only the care recipient linked to their caregiver record.
- Professional accounts: only care recipients with an active `professional_access_grants` row.
- Team accounts: team dashboard, state-report, metrics, and moderation routes only; they do not gain blanket access to private care records.
- Professional access grants: must be created by an active caregiver account linked to the care recipient and must target an active provisioned professional account.

This is an initial central enforcement layer. Before real production care data is enabled, test every legacy route whose resource ownership cannot be inferred from a path/body person ID, review each route that accepts alternate caregiver-profile identifiers, and complete credential recovery, revocation, rate limiting, and session-storage hardening. Do not interpret a passing unit test as a production security sign-off.

## Collaboration API

- Community posts are persisted but begin in `pending` moderation. Public feed queries return only published public posts. Unverified clients cannot self-assert professional/team author types.
- Reactions are persisted per post and caregiver; one reaction per actor/post is enforced by a unique database constraint.
- Shared-experience matching uses literal keyword overlap against published posts and labels the method. It is not an AI or clinical assessment.
- Companion messages and responses are persisted. Responses summarize existing care events, appointments, and active insights; this endpoint does not call an LLM and must not be marketed as a generative medical assistant. Emergency keyword detection is a conservative warning, not validated clinical triage.
- Team dashboards return database aggregates, and the state report explicitly says those aggregates are not population-level statistics.
- Professional dashboards return only records covered by active grants.

## Deployment verification

The GitHub backend test job validates compilation, startup, route-level tests, persistence, and authorization checks in its test environment. The deployed-smoke job has been failing because the configured Railway domain returns 404 for the expected FastAPI health/OpenAPI paths. Do not claim live persistence, successful browser integration, or production security until Railway is serving this exact backend commit and the smoke tests pass.
