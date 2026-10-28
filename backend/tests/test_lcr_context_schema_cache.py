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
