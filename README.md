# SolenOS

A trust-first living care record.

## Philosophy

SolenOS is built on the belief that in healthcare, trust is the feature. Every product decision should strengthen:
- Trust
- Evidence
- Transparency
- Uncertainty handling
- Continuity
- Accuracy
- Explainability
- Reliability

See `docs/philosophy.md` for the full product philosophy.

## Architecture

- **Backend**: FastAPI + SQLAlchemy (Python)
- **Frontend**: React + Vite (TypeScript)
- **Database**: PostgreSQL for production (Railway/Supabase); SQLite is for local development and tests only

## Getting Started

### Backend
```bash
cd backend
pip install -e .
uvicorn app.main:app --reload
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

## Principles

1. **Trust Over Features** - Every capability must strengthen trust, continuity, evidence, transparency, or cognitive load reduction.
2. **Evidence Over Intelligence** - Every insight links back to its source.
3. **Transparency Over Hidden Reasoning** - Show known, possible, and unknown clearly.
4. **Uncertainty Is a Feature** - Never hide what is not known.
5. **Calm Over Clever** - Reduce caregiver anxiety, don't impress.
6. **Build for the Worst Day** - Hospital discharges, late-night confusion, exhaustion.


## Production configuration

Set these in the Railway backend service Variables panel. Do not commit secret values or expose them to the frontend.

- `DATABASE_URL`: Supabase PostgreSQL connection string. The backend normalizes the URL to its installed driver and enforces TLS when running in Railway.
- `SECRET_KEY`: a unique random secret of at least 32 characters; never use `change-me-in-production`.
- `CORS_ORIGINS`: comma-separated frontend origins; include `https://solenosai.netlify.app` for the production frontend.

Railway runs `alembic upgrade head` before starting Uvicorn. The backend health endpoint is `/health`; readiness includes database connectivity and schema validation. Uploaded caregiver photos are stored as private database-backed evidence records and are served only through the authenticated download route, not from a public filesystem path.

Rotate the Supabase database password that was exposed in the conversation, then update Railway's `DATABASE_URL` with the new connection string before deployment.
