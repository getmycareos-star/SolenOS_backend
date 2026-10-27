from app.core.database import Base
from app.models import care as _care  # noqa: F401
from app.models import qwen_background_job as _qwen  # noqa: F401
from app.models import ai_output_governance as _ai  # noqa: F401


AI_GOVERNANCE_TABLES = {
    "ai_output_rejections",
    "ai_safety_checks",
    "ai_safety_violations",
    "ai_model_versions",
    "ai_prompt_versions",
    "ai_inference_metrics",
    "ai_job_status_events",
    "ai_human_feedback",
    "ai_output_lcr_proposals",
    "ai_output_lcr_decisions",
}


def test_ai_governance_tables_are_registered_for_persistence():
    assert AI_GOVERNANCE_TABLES.issubset(set(Base.metadata.tables))


def test_lcr_proposals_require_explicit_decision_and_default_to_pending_authorization():
    proposal = Base.metadata.tables["ai_output_lcr_proposals"]
    decision = Base.metadata.tables["ai_output_lcr_decisions"]

    assert "status" in proposal.c
    assert proposal.c.status.default.arg == "pending_authorization"
    assert "decision" in decision.c
    assert "reviewer_caregiver_id" in decision.c
    assert "resulting_lcr_revision_id" in decision.c
