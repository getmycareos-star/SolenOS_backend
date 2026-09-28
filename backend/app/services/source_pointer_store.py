"""In-memory downgrade log for the source-pointer trust layer.

Persistence reality: DARE runtime persistence is currently in-memory. This
store holds the downgrade history for the running process. When a Postgres
write path is added for the dare_claim_downgrades table (migration
076_source_pointer_trust.sql), hook `persist_downgrade` to INSERT there.

Ported from backend-fix/src/lib/data-acquisition-resilience/source-pointer-store.ts
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from uuid import uuid4


_downgrade_log: List[Dict[str, Any]] = []


def make_downgrade_record(
    claim_id: str,
    evidence_id: Optional[str],
    original_confidence: Optional[float],
    final_confidence: Optional[float],
    original_status: str,
    final_status: str,
    reason: str,
    source_span_before: Optional[str],
    source_span_start_offset: Optional[int],
    source_span_end_offset: Optional[int],
) -> Dict[str, Any]:
    """Build a downgrade record dict. Does NOT add to the log."""
    return {
        "id": f"downgrade_{uuid4()}",
        "claim_id": claim_id,
        "evidence_id": evidence_id,
        "original_confidence": original_confidence,
        "final_confidence": final_confidence,
        "original_status": original_status,
        "final_status": final_status,
        "reason": reason,
        "source_span_before": source_span_before,
        "source_span_start_offset": source_span_start_offset,
        "source_span_end_offset": source_span_end_offset,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def record_downgrade(record: Dict[str, Any]) -> None:
    """Append a downgrade record to the in-memory log."""
    _downgrade_log.append(record)
    # Optional Postgres persistence hook (see migration 076):
    # persist_downgrade(record)  # non-fatal


def list_downgrades() -> List[Dict[str, Any]]:
    return list(_downgrade_log)


def list_downgrades_for_claim(claim_id: str) -> List[Dict[str, Any]]:
    return [r for r in _downgrade_log if r.get("claim_id") == claim_id]


def clear_downgrade_log() -> None:
    _downgrade_log.clear()


async def persist_downgrade(_record: Dict[str, Any]) -> None:
    """Optional Postgres persistence hook. Inert until a live DARE write path exists."""
    return
