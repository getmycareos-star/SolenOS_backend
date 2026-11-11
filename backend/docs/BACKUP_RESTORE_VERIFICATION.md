# SolenOS PostgreSQL backup and recovery verification

## Purpose

This runbook verifies that a PostgreSQL logical backup can be restored into a separate database and that synthetic caregiver and anonymous-session identity, persisted consent, care evidence, document bytes, care-event links, correction history and LCR revision history remain associated.

A backup file, a successful `pg_dump`, or a successful `pg_restore` command alone is not a recovery pass.

## Safety boundary

- Use two dedicated PostgreSQL databases containing synthetic data only.
- The source database name must contain `test` or `backup`; the restore database name must contain `test`, `restore` or `recovery`.
- Never point either URL at production or a database containing real caregiver records.
- The restore command uses `pg_restore --clean --if-exists`. It drops/recreates objects in the target database. Treat the target as disposable.
- Do not place database URLs, passwords, dump files, or real care data in Git, CI logs, tickets, or this report.
- The runner inserts uniquely named synthetic fixtures into the source database. Use a disposable source database that has already been migrated to the current Alembic head.

## Prerequisites

- PostgreSQL client tools (`pg_dump` and `pg_restore`) installed and compatible with the PostgreSQL server version.
- Python dependencies installed from `backend`.
- Both databases are reachable by the same operator.
- The restore database is empty/disposable and the operator has permission to create/replace schema objects.
- Both databases use the same supported PostgreSQL major version for this first recovery exercise.

## Execute

From `backend`:

```powershell
$env:SOLENOS_BACKUP_SOURCE_DATABASE_URL = "postgresql+psycopg://USER:PASSWORD@HOST:PORT/solenos_backup_test"
$env:SOLENOS_BACKUP_RESTORE_DATABASE_URL = "postgresql+psycopg://USER:PASSWORD@HOST:PORT/solenos_recovery_test"
python scripts/verify_backup_restore.py
```

Use actual credentials only in the local shell/session. Do not commit them or paste them into logs.

The script:
1. Rejects identical databases and production-named databases.
2. Inserts a uniquely identified synthetic caregiver, care recipient, second caregiver/person, observation evidence, care event, binary document, correction and LCR state revision into the source database.
3. Runs a real custom-format `pg_dump`.
4. Restores that dump into the isolated restore database with `pg_restore`.
5. Verifies caregiver/person ownership, evidence provenance/hash, event/evidence association, exact document bytes/size/SHA-256, correction history, LCR revision history and cross-caregiver isolation.
6. Emits a machine-readable PASS/FAIL report with measured backup size, backup/restore durations and end-to-end duration. CI retains the sanitized report as a 30-day workflow artifact; the raw database dump is not uploaded.
7. The dedicated GitHub Actions workflow `Backup and recovery verification` runs the existing real-Uvicorn PostgreSQL anonymous-session E2E suite against the restored database. It first discovers the exact synthetic caregiver fixture from the backup run and reads that caregiver's identity, evidence, care event, LCR projection and document bytes through authenticated APIs; it then covers authenticated reads/writes, token revocation/refresh, cross-caregiver denial and persistence across a Uvicorn restart.

For a local full check, after the backup/restore script passes, point the application at the restored database and run the real HTTP/PostgreSQL E2E suite:

```powershell
$env:DATABASE_URL = $env:SOLENOS_BACKUP_RESTORE_DATABASE_URL
$env:SECRET_KEY = "local-ci-only-secret-key-long-enough-for-test-runtime"
$env:APP_ENV = "test"
python scripts/postgres_anonymous_e2e.py
```

This second test creates additional synthetic records in the restored target and starts a local Uvicorn process. Keep the target disposable. A non-zero exit code or any failed assertion in either command is a failed recovery exercise. Preserve the sanitized report and investigate before retrying. Do not manually edit the restored data to make assertions pass.

## What this check does not prove

- It does not connect to or restore the live production database.
- It does not verify a provider-managed automated backup schedule, retention policy, point-in-time recovery, WAL archiving, or cross-region copies.
- The SQL verifier's cross-caregiver check is a relational ownership assertion; the separate PostgreSQL HTTP E2E suite is required to prove application authorization against the restored database.
- It does not verify object storage outside PostgreSQL. Current `EvidenceUpload` records store binary content in the `evidence_uploads.content` database column, and this runner verifies that binary payload. If any document path uses external object storage, add a separate synthetic object-store backup/restore test before claiming document recovery.
- It does not establish a production RPO or RTO target. The report records observed backup/restore duration only. Define approved targets from the real backup cadence, data-loss tolerance and recovery operations, then test against those targets.

## Release gate and evidence record

Do not mark backup recovery as verified until a fresh run against disposable PostgreSQL databases reports PASS and the operator has reviewed:

- source and restore database names (never credentials);\n- the sanitized JSON report artifact and its PASS/FAIL status;
- backup artifact format and size, stored outside the repository;
- measured backup and restore durations;
- fixture counts and all verification assertions;
- PostgreSQL server/client versions and Alembic revision at the time of the run;
- operator, run timestamp, sanitized logs and any deviations.

Treat the `Backup and recovery verification` workflow as a required main-branch release check; configure branch protection/rulesets to require it before production releases. Production readiness additionally requires a separate, authorized recovery exercise using the actual provider backup mechanism and a production-equivalent isolated target. Never perform that exercise by restoring over production.
