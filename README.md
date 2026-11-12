# SolenOS

A trust-first Living Care Record for family caregivers.

SolenOS preserves care context over time: what happened, what changed, what evidence supports it, what remains uncertain, and what needs follow-through.

## Architecture

- **Frontend:** Next.js App Router + React + TypeScript in [SolenOS-landing](https://github.com/getmycareos-star/SolenOS-landing).
- **Frontend hosting:** Netlify only; the frontend uses the Netlify Next.js runtime.
- **Backend:** FastAPI + SQLAlchemy in this repository, deployed separately.
- **Production database:** PostgreSQL, managed through Alembic migrations. SQLite is for local development only; production configuration must reject SQLite.
- **API boundary:** The frontend calls the backend through its same-origin `/api/v1` proxy. The browser must never connect directly to the database.
- **Identity:** Anonymous access uses server-issued bearer sessions. Caller-supplied person or caregiver IDs are not credentials and must not override server-derived ownership.
- **Care-data boundary:** The Living Care Record is the authoritative longitudinal record. Captures and extracted documents remain evidence or review candidates until the appropriate review/reconciliation workflow accepts them. Community peer experiences must not silently write authoritative care facts.
- **Optional AI runtime:** Core care-data workflows must remain usable when an optional model service is unavailable. Model readiness is not a substitute for evidence, authorization, persistence, or review.

The frontend's hosting and build configuration is maintained in [SolenOS-landing/netlify.toml](https://github.com/getmycareos-star/SolenOS-landing/blob/main/netlify.toml). Do not replace its Next.js build with Vite or a `dist` output.

## Local backend development

Run commands from the repository's `backend` directory and follow the dependency manifest and Alembic configuration in that directory. Use a local-only development database for local runs. Never copy production database credentials or production care records into local test fixtures.

Before considering a change complete, run the backend test suite and startup checks available in `.github/workflows/backend-validation.yml`. A passing local test suite does not prove that Railway is running the same commit, that production migrations are current, or that the live PostgreSQL schema is correct; those require separate deployment/readiness evidence.

## Optional browser push notifications

The API supports caregiver-scoped Web Push subscriptions. Configure these backend environment variables before enabling the browser control:

- `VAPID_PUBLIC_KEY`: URL-safe base64 VAPID public key.
- `VAPID_PRIVATE_KEY`: matching VAPID private key in PEM form; preserve newlines or encode them as `\\n`.
- `VAPID_SUBJECT`: a contact URI such as `mailto:support@solenosai.net`.
- `SECRET_KEY`: the stable production application secret; it also derives the at-rest encryption key for stored browser subscriptions. Rotating it requires caregivers to register their browser subscriptions again.

The push worker stores provider acceptance, retries transient failures, and revokes expired endpoints. A provider-accepted receipt does **not** prove that a device displayed the notification. Push payloads intentionally contain no care details; the caregiver opens the authenticated app to review the notification feed.

Android native push is a separate Firebase Cloud Messaging integration. It requires a real Firebase Android app configuration and service credentials; Web Push does not deliver to a Capacitor Android WebView.

## Product and safety principles

1. **Trust over feature count:** prioritize continuity, accuracy, explainability, and reduced caregiver cognitive load.
2. **Evidence over unsupported inference:** retain source, time, ownership, and review status.
3. **Uncertainty stays visible:** distinguish known, uncertain, missing, and needing clarification.
4. **No silent promotion:** model output, extracted text, and peer experiences do not become verified care facts without the required workflow.
5. **Server-side authorization:** enforce session scope and resource ownership on every protected route.
6. **Failure isolation:** an optional model or secondary service failure must not corrupt or block core care-record access.
7. **Honest readiness claims:** never claim production readiness, backup recoverability, or regulatory compliance without direct evidence and appropriate review.
