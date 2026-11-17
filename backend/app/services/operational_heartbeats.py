"""Operational heartbeat persistence for application background workers."""
from datetime import datetime, timezone

from sqlalchemy import text

from app.core.database import SessionLocal


def record_worker_heartbeat(
    worker_name: str,
    *,
    outcome: str,
    processed_count: int = 0,
    error_type: str | None = None,
) -> None:
    if not worker_name or len(worker_name) > 100:
        raise ValueError("worker_name must contain 1-100 characters")
    if outcome not in {"success", "error"}:
        raise ValueError("outcome must be success or error")
    if not isinstance(processed_count, int) or isinstance(processed_count, bool) or processed_count < 0:
        raise ValueError("processed_count must be a non-negative integer")

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.execute(
            text("""
                INSERT INTO operational_worker_heartbeats (
                    worker_name, last_started_at, last_completed_at, last_success_at,
                    last_outcome, last_error_type, last_processed_count, updated_at
                ) VALUES (
                    :worker_name, :now, :now,
                    CASE WHEN :outcome = 'success' THEN :now ELSE NULL END,
                    :outcome, :error_type, :processed_count, :now
                )
                ON CONFLICT (worker_name) DO UPDATE SET
                    last_started_at = excluded.last_started_at,
                    last_completed_at = excluded.last_completed_at,
                    last_success_at = CASE
                        WHEN excluded.last_outcome = 'success'
                        THEN excluded.last_completed_at
                        ELSE operational_worker_heartbeats.last_success_at
                    END,
                    last_outcome = excluded.last_outcome,
                    last_error_type = excluded.last_error_type,
                    last_processed_count = excluded.last_processed_count,
                    updated_at = excluded.updated_at
            """),
            {
                "worker_name": worker_name,
                "now": now,
                "outcome": outcome,
                "error_type": error_type[:120] if error_type else None,
                "processed_count": processed_count,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
