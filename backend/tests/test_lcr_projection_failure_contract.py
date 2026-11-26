from app.services.lcr_context import _mark_source_enrichment_partial


def test_failed_enrichment_preserves_retrieved_records_but_downgrades_projection():
    retrieved_item = {"id": "fact-1", "fact_value": "Uses a walking frame"}
    sources = {
        "facts": {
            "status": "available",
            "items": [retrieved_item],
            "candidate_scan_complete": True,
            "returned_history_complete": True,
            "returned_count": 1,
        }
    }

    _mark_source_enrichment_partial(
        sources,
        "facts",
        error_code="fact_evidence_lineage_lookup_failed",
        interpretation_limit="The fact was retrieved, but its source evidence could not be checked.",
    )

    facts = sources["facts"]
    assert facts["status"] == "partial"
    assert facts["items"] == [retrieved_item]
    assert facts["returned_count"] == 1
    assert facts["candidate_scan_complete"] is False
    assert facts["returned_history_complete"] is False
    assert facts["enrichment_status"] == "failed"
    assert facts["error_code"] == "fact_evidence_lineage_lookup_failed"
    assert "source evidence could not be checked" in facts["interpretation_limit"]


def test_failed_enrichment_does_not_create_a_missing_source_entry():
    sources = {"events": {"status": "empty", "items": []}}

    _mark_source_enrichment_partial(
        sources,
        "not_requested",
        error_code="should_not_be_written",
        interpretation_limit="No source was requested.",
    )

    assert set(sources) == {"events"}
    assert sources["events"]["status"] == "empty"
