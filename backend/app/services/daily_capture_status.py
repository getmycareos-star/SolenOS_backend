"""Shared caregiver-safe status for Daily Care extraction persistence."""

from __future__ import annotations


def daily_capture_extraction_status(analysis, job) -> str:
    """Return extraction state from persisted analysis, not the worker receipt alone."""
    if analysis is not None:
        return str(getattr(analysis, "extraction_status", None) or "unavailable")
    if job is None:
        return "unavailable"

    job_status = getattr(job, "status", None)
    if job_status in {"queued", "processing"}:
        return "queued"
    if job_status in {"failed", "cancelled"}:
        return "failed"

    # A completed job without its saved analysis has no reviewable extraction.
    return "unavailable"
