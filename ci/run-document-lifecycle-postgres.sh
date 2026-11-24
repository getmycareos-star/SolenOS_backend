#!/usr/bin/env bash
set -Eeuo pipefail

cd /workspace/backend
python -m alembic upgrade head

cd /workspace
python -m pytest \
  backend/tests/test_unified_care_spine_capture.py \
  backend/tests/test_document_lifecycle_postgres.py \
  backend/tests/test_document_extraction_page_integrity.py \
  backend/tests/test_document_intelligence_postgres.py \
  backend/tests/test_unified_lcr_retrieval.py \
  backend/tests/test_daily_care_review_postgres.py \
  backend/tests/test_handoff_postgres_integration.py \
  backend/tests/test_appointment_workspace_postgres.py \
  backend/tests/test_companion_care_update_postgres.py \
  -q -s

# The transparency provider must be exercised against the migrated PostgreSQL schema,
# not only the SQLite unit-test fallback.
python -m pytest backend/tests/test_transparency_evidence.py -q -s

cd /workspace/backend
python scripts/postgres_anonymous_e2e.py
