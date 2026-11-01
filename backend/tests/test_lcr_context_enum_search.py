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
        Column("gap_reason", SAEnum("absent", "not_provided", name="gapreason")),
        Column("confidence", Float),
    )

    predicates = _text_search_predicates(table, "uncertainty_assessments", ["uncertain"])
    compiled = str(or_(*predicates).compile(dialect=postgresql.dialect()))

    assert "CAST(uncertainty_assessments.epistemic_state AS VARCHAR) ILIKE" in compiled
    assert "CAST(uncertainty_assessments.gap_reason AS VARCHAR) ILIKE" in compiled
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

def test_postgresql_native_enum_search_predicate_executes():
    """Execute the casted lexical predicate against a real PostgreSQL ENUM."""
    import os
    import uuid

    import pytest
    from sqlalchemy import create_engine, select

    database_url = os.environ.get("DATABASE_URL", "")
    if database_url.startswith("postgresql+psycopg://"):
        database_url = database_url.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
    if not database_url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip("requires a real PostgreSQL DATABASE_URL")

    suffix = uuid.uuid4().hex[:16]
    metadata = MetaData()
    table = Table(
        f"lcr_enum_search_{suffix}",
        metadata,
        Column(
            "epistemic_state",
            SAEnum("known", "uncertain", "missing", name=f"lcr_epistemicstate_{suffix}"),
        ),
        Column("gap_reason", Text),
    )
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(table.insert(), [
                {"epistemic_state": "uncertain", "gap_reason": None},
                {"epistemic_state": "known", "gap_reason": "unrelated fixture"},
            ])
            predicates = _text_search_predicates(
                table, "uncertainty_assessments", ["uncertain"]
            )
            matched = connection.execute(
                select(table.c.epistemic_state).where(or_(*predicates))
            ).scalars().all()
            assert matched == ["uncertain"]
    finally:
        metadata.drop_all(engine)
        engine.dispose()
