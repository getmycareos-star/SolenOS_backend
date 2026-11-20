"""Verify every persisted original document binary against its stored checksum.

Run from the backend project environment with DATABASE_URL configured. When
DOCUMENT_STORAGE_BACKEND=s3, this reads each S3 object through the same
authorized storage adapter used by API routes.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.models.collaboration import EvidenceUpload
from app.services.document_binary_storage import read_document_binary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Optional maximum number of rows to verify")
    args = parser.parse_args()

    db = SessionLocal()
    checked = 0
    failures = []
    try:
        query = db.query(EvidenceUpload).order_by(
            EvidenceUpload.created_at.asc(),
            EvidenceUpload.evidence_id.asc(),
        )
        if args.limit > 0:
            query = query.limit(args.limit)
        for upload in query.yield_per(100):
            checked += 1
            try:
                read_document_binary(upload)
            except Exception as exc:
                failures.append({
                    "evidence_id": str(upload.evidence_id),
                    "storage_backend": upload.storage_backend or "postgres",
                    "error": str(exc)[:300],
                })
    finally:
        db.close()

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checked": checked,
        "failed": len(failures),
        "failures": failures,
        "ok": not failures,
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
