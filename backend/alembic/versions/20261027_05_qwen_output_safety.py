"""Persist Qwen outputs separately from job status and authoritative care facts.

Revision ID: 20261027_05_qwen_output_safety
Revises: 20261027_04_caregiver_context
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, select, update
import uuid

from app.core.database import Base
from app.models import care as _care  # noqa: F401
from app.models import qwen_background_job as _qwen  # noqa: F401
from app.models import safety as _safety  # noqa: F401

revision = "20261027_05_qwen_output_safety"
down_revision = "20261027_04_caregiver_context"
branch_labels = None
depends_on = None

SAFETY_TABLES = (
    "safety_benchmark_cases",
    "safety_regressions",
    "safety_events",
    "safety_feedback",
    "safety_decisions",
    "safety_audit_records",
    "safety_blind_spots",
    "escalations",
    "safety_policy_rules",
)


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    # Repair missing safety tables from existing ORM definitions, without
    # recreating or dropping any existing table or record.
    missing_safety = [
        Base.metadata.tables[name]
        for name in SAFETY_TABLES
        if name in Base.metadata.tables and not inspector.has_table(name)
    ]
    if missing_safety:
        Base.metadata.create_all(bind=bind, tables=missing_safety, checkfirst=True)

    # Structured assessment dimensions use their canonical ORM definition.
    if "assessment_dimensions" in Base.metadata.tables and not inspect(bind).has_table("assessment_dimensions"):
        Base.metadata.create_all(
            bind=bind,
            tables=[Base.metadata.tables["assessment_dimensions"]],
            checkfirst=True,
        )

    # Generated output, job history, and safety review remain separate from LCR.
    qwen_tables = [
        Base.metadata.tables[name]
        for name in ("qwen_model_outputs", "qwen_job_events", "qwen_safety_evaluations")
        if name in Base.metadata.tables and not inspect(bind).has_table(name)
    ]
    if qwen_tables:
        Base.metadata.create_all(bind=bind, tables=qwen_tables, checkfirst=True)

    # Move legacy completed outputs into the dedicated output table before
    # clearing the old job column. The output text and source references survive.
    inspector = inspect(bind)
    required_tables = {"qwen_background_jobs", "qwen_model_outputs", "qwen_safety_evaluations", "qwen_job_events"}
    if required_tables.issubset(set(inspector.get_table_names())):
        job_table = Base.metadata.tables["qwen_background_jobs"]
        output_table = Base.metadata.tables["qwen_model_outputs"]
        safety_table = Base.metadata.tables["qwen_safety_evaluations"]
        event_table = Base.metadata.tables["qwen_job_events"]
        job_columns = {column["name"] for column in inspector.get_columns("qwen_background_jobs")}
        if {"id", "person_id", "caregiver_id", "status", "result_text", "workflow"}.issubset(job_columns):
            legacy_rows = bind.execute(
                select(job_table).where(
                    job_table.c.status == "completed",
                    job_table.c.result_text.is_not(None),
                )
            ).mappings().all()
            for job in legacy_rows:
                output = bind.execute(
                    select(output_table.c.id).where(output_table.c.job_id == job["id"])
                ).first()
                if output is None:
                    output_id = str(uuid.uuid4())
                    bind.execute(output_table.insert().values(
                        id=output_id,
                        job_id=job["id"],
                        person_id=job["person_id"],
                        caregiver_id=job["caregiver_id"],
                        workflow=job["workflow"] or "unknown",
                        model_name=None,
                        model_version=None,
                        output_text=job["result_text"],
                        output_metadata={"legacy_backfill": True, "written_to_lcr": False},
                        source_event_ids_json=job.get("source_event_ids_json") or "[]",
                        source_evidence_ids_json=job.get("source_evidence_ids_json") or "[]",
                        review_status="pending_review",
                        review_required=True,
                        written_to_lcr=False,
                    ))
                    bind.execute(safety_table.insert().values(
                        id=str(uuid.uuid4()),
                        output_id=output_id,
                        job_id=job["id"],
                        person_id=job["person_id"],
                        caregiver_id=job["caregiver_id"],
                        risk_class="unassessed",
                        decision="review_required",
                        human_review_required=True,
                        violations_json=[],
                        policy_references_json=[],
                    ))
                    bind.execute(event_table.insert().values(
                        id=str(uuid.uuid4()),
                        job_id=job["id"],
                        person_id=job["person_id"],
                        caregiver_id=job["caregiver_id"],
                        event_type="legacy_output_backfilled",
                        from_status="completed",
                        to_status="completed",
                        metadata_json={"output_id": output_id, "written_to_lcr": False},
                    ))
                # Only clear the legacy copy after a dedicated output exists.
                bind.execute(
                    update(job_table).where(job_table.c.id == job["id"]).values(result_text=None)
                )


def downgrade():
    # Retain safety/audit/output history; destructive downgrade is intentionally blocked.
    raise RuntimeError("Qwen output/safety history is retained; use a reviewed backup and data-retention plan.")
