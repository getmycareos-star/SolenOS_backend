from sqlalchemy import Column, MetaData, String, Table, create_engine

from app.services.lcr_context import _load_source_schema


def test_lcr_source_schema_is_cached_per_engine_and_reflects_partial_schema():
    _load_source_schema.cache_clear()
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table("living_care_records", metadata, Column("id", String, primary_key=True))
    metadata.create_all(engine)

    try:
        first_tables, first_metadata = _load_source_schema(engine)
        first_cache = _load_source_schema.cache_info()
        second_tables, second_metadata = _load_source_schema(engine)
        second_cache = _load_source_schema.cache_info()

        assert "living_care_records" in first_tables
        assert first_tables == second_tables
        assert first_metadata is second_metadata
        assert second_cache.hits == first_cache.hits + 1
        # Partial schemas are still represented; the cache must not pretend
        # absent required care sources are available.
        assert "care_events" not in second_tables
    finally:
        _load_source_schema.cache_clear()
        engine.dispose()


def test_lcr_context_decodes_persisted_jsonlist_text_without_guessing_plain_text():
    from app.services.lcr_context import _safe_value

    assert _safe_value('["evidence-a", "evidence-b"]', "evidence_ids") == ["evidence-a", "evidence-b"]
    assert _safe_value('{"reported": true}', "context_payload") == {"reported": True}
    assert _safe_value("not-json", "evidence_ids") == "not-json"
    assert _safe_value('["not-a-list-field"]', "source_text") == '["not-a-list-field"]'

def test_lcr_context_includes_only_person_scoped_structured_evidence_observations():
    from sqlalchemy import Integer, Text
    from app.services.lcr_context import build_lcr_context

    _load_source_schema.cache_clear()
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    observations = Table(
        "evidence_observations",
        metadata,
        Column("id", String, primary_key=True),
        Column("person_id", String, nullable=False),
        Column("evidence_id", String, nullable=False),
        Column("attribute", String, nullable=False),
        Column("value_text", Text),
        Column("source_span_start", Integer, nullable=False),
        Column("source_span_end", Integer, nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(observations.insert(), [
            {"id": "owner-observation", "person_id": "person-a", "evidence_id": "evidence-a",
             "attribute": "sleep", "value_text": "reported waking overnight",
             "source_span_start": 10, "source_span_end": 37},
            {"id": "private-observation", "person_id": "person-b", "evidence_id": "evidence-b",
             "attribute": "mobility", "value_text": "private mobility detail",
             "source_span_start": 0, "source_span_end": 24},
        ])

    try:
        from sqlalchemy.orm import Session

        with Session(engine) as db:
            context = build_lcr_context(db, "person-a", limit_per_source=10)
        rows = context["sources"]["evidence_observations"]["items"]
        assert [row["id"] for row in rows] == ["owner-observation"]
        assert rows[0]["evidence_id"] == "evidence-a"
        assert rows[0]["source_span_start"] == 10
        assert "private mobility detail" not in str(context)
    finally:
        _load_source_schema.cache_clear()
        engine.dispose()

