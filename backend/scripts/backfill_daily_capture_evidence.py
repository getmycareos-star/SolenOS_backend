"""Backfill immutable source Evidence for legacy Daily Care events.

Dry-run is the default. Pass --apply only after reviewing the dry-run summary
and obtaining approval for the target database.
"""
from __future__ import annotations

import argparse
import json

from app.core.database import SessionLocal
from app.services.daily_capture_evidence_backfill import backfill_daily_capture_evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write evidence rows and event links")
    parser.add_argument("--limit", type=int, default=10_000)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        summary = backfill_daily_capture_evidence(db, apply=args.apply, limit=args.limit)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
