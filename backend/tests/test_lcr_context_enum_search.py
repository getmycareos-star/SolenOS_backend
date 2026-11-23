"""Regression coverage for PostgreSQL enum columns in shared LCR lexical search."""
from sqlalchemy import Column, Enum as SAEnum, Float, MetaData, String, Text, Table, or_, select
from sqlalchemy.dialects import postgresql

from app.services.lcr_context import _apply_text_search_filter, _text_search_predicates


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



def test_postgres_query_does_not_return_unsearchable_source_rows():
    """A topic query must not receive every row from a source with no searchable fields."""
    import os
    import uuid

    import pytest
    from sqlalchemy import create_engine

    database_url = os.environ.get("DATABASE_URL", "")
    if database_url.startswith("postgresql+psycopg://"):
        database_url = database_url.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
    if not database_url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip("requires a real PostgreSQL DATABASE_URL")

    table_name = f"lcr_unsearchable_source_{uuid.uuid4().hex[:16]}"
    metadata = MetaData()
    table = Table(
        table_name,
        metadata,
        Column("id", String, primary_key=True),
        Column("person_id", String, nullable=False),
        Column("private_payload", Text),
    )
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(table.insert(), [
                {"id": "unrelated-1", "person_id": "person-a", "private_payload": "unrelated care detail"},
                {"id": "unrelated-2", "person_id": "person-a", "private_payload": "another unrelated detail"},
            ])
            statement = select(table.c.id).where(table.c.person_id == "person-a")
            statement, searchable = _apply_text_search_filter(
                statement, table, "legacy_source_without_search_fields", ["medication"]
            )
            assert searchable is False
            assert connection.execute(statement).scalars().all() == []
    finally:
        metadata.drop_all(engine)
        engine.dispose()



def test_postgres_unsearchable_optional_source_marks_projection_incomplete(monkeypatch):
    """An unsearchable optional source must make the projection incomplete even when no required source is missing."""
    import os
    import uuid

    import pytest
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    import app.services.lcr_context as lcr_context
    from app.services.lcr_context import _load_source_schema, build_lcr_context

    database_url = os.environ.get("DATABASE_URL", "")
    if database_url.startswith("postgresql+psycopg://"):
        database_url = database_url.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
    if not database_url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip("requires a real PostgreSQL DATABASE_URL")

    monkeypatch.setattr(
        lcr_context,
        "SOURCE_SPECS",
        {"evidence_reviews": lcr_context.SOURCE_SPECS["evidence_reviews"]},
    )
    schema = "lcr_unsearchable_" + uuid.uuid4().hex[:16]
    engine = create_engine(database_url, pool_pre_ping=True)
    connection = engine.connect()
    try:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        connection.exec_driver_sql(f'SET search_path TO "{schema}"')
        connection.exec_driver_sql(
            "CREATE TABLE evidence_review_records (id VARCHAR PRIMARY KEY, person_id VARCHAR NOT NULL, private_payload TEXT)"
        )
        connection.exec_driver_sql(
            "INSERT INTO evidence_review_records (id, person_id, private_payload) VALUES ('review-a', 'person-a', 'unrelated detail')"
        )
        _load_source_schema.cache_clear()
        with Session(bind=connection) as db:
            context = build_lcr_context(db, "person-a", query="medication")
        assert context["sources"]["evidence_reviews"]["status"] == "unsearchable"
        assert "evidence_reviews" in context["failed_sources"]
        assert "evidence_reviews" in context["failed_optional_sources"]
        assert context["missing_required_sources"] == []
        assert context["missing_optional_sources"] == []
        assert context["retrieval_scan_complete"] is False
        assert context["retrieval_projection_complete"] is False
        assert context["history_coverage"] == "partial_source_history"
    finally:
        _load_source_schema.cache_clear()
        connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        connection.close()
        engine.dispose()
