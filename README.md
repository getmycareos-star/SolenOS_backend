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
- **Database**: SQLite for local development; PostgreSQL for production

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


## Production deployment (Railway + Supabase)

Set these variables on the **backend service** in Railway. Do not commit secrets to this repository or expose them in frontend environment variables.

- `DATABASE_URL`: the Supabase PostgreSQL connection URI. SQLAlchemy normalizes `postgresql://` and `postgres://` to the installed Psycopg 3 driver.
- `SECRET_KEY`: a unique random secret of at least 32 characters. Generate a fresh value for production; never use `change-me-in-production`.
- `APP_ENV`: `production`.
- `DEBUG`: `false`.

The backend intentionally refuses to start in production with SQLite, a missing/short placeholder signing secret, or debug mode enabled. If the Supabase password has been shared in a chat, terminal log, or source file, rotate it in Supabase before using the replacement connection URI.

Health checks:
- `GET /health` reports process liveness.
- `GET /ready` executes a database query and reports database readiness. A successful `/health` response alone does not prove that persistence works.

Authentication:
- `POST /api/v1/auth/signup` and `POST /api/v1/auth/login` create accounts and issue 24-hour bearer sessions. No refresh token is issued; users sign in again after expiration.
- `POST /api/v1/auth/logout` revokes issued sessions for the account.
- Protected care APIs require a valid session and active care-circle membership. The backend checks person scope, read/write permission, and caregiver attribution independently of frontend route guards.
- Caregiver invitations are hashed at rest, expire after seven days, are bound to the invited email, and can be accepted only once.

The live Railway service must still pass deployed health, readiness, and API smoke checks after the production environment variables are configured. Passing the repository CI suite does not by itself prove the deployed service is healthy.
