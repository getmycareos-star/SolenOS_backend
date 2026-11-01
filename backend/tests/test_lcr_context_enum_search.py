"""Regression coverage for PostgreSQL enum columns in shared LCR lexical search."""
from sqlalchemy import Column, Enum as SAEnum, Float, MetaData, Text, Table, or_
from sqlalchemy.dialects import postgresql

from app.services.lcr_context import _text_search_predicates


def test_uncertainty_assessment_enum_is_cast_before_ilike():
    metadata = MetaData()
    table = Table(
        "uncertainty_assessments",
        metadata,
        Column("epistemic_state", SAEnum("known", "uncertain", "missing", name="epistemicstate")),
        Column("gap_reason", Text),
        Column("confidence", Float),
    )

    predicates = _text_search_predicates(table, "uncertainty_assessments", ["uncertain"])
    compiled = str(or_(*predicates).compile(dialect=postgresql.dialect()))

    assert "CAST(uncertainty_assessments.epistemic_state AS VARCHAR) ILIKE" in compiled
    assert "uncertainty_assessments.gap_reason ILIKE" in compiled
    assert "confidence" not in compiled


def test_evidence_observation_enum_is_cast_but_text_fields_remain_searchable():
    metadata = MetaData()
    table = Table(
        "evidence_observations",
        metadata,
        Column("epistemic_status", SAEnum("unverified", "uncertain", "unknown", name="epistemicstatus")),
        Column("source_passage", Text),
    )

    predicates = _text_search_predicates(table, "evidence_observations", ["uncertain"])
    compiled = str(or_(*predicates).compile(dialect=postgresql.dialect()))

    assert "CAST(evidence_observations.epistemic_status AS VARCHAR) ILIKE" in compiled
    assert "evidence_observations.source_passage ILIKE" in compiled
