from app.services.companion_context import rank_authorized_context


def test_retrieval_keeps_recent_anchors_and_finds_older_question_relevant_context():
    records = [
        {"id": index, "text": f"Routine unrelated daily observation number {index}"}
        for index in range(20)
    ]
    records[12]["text"] = "Sleep has been getting worse over several nights"
    records[16]["text"] = "A note about the garden and a walk"

    selected = rank_authorized_context(
        records,
        "Has sleep been getting worse?",
        text_of=lambda record: record["text"],
        limit=6,
        always_keep_recent=3,
    )

    ids = [record["id"] for record in selected]
    assert ids[:3] == [0, 1, 2]
    assert 12 in ids
    assert len(ids) == 6
    assert ids == sorted(ids)


def test_retrieval_is_deterministic_and_handles_empty_or_unmatched_queries():
    records = [{"text": f"Record {index}"} for index in range(8)]

    assert rank_authorized_context(
        records, "", text_of=lambda row: row["text"], limit=4
    ) == records[:4]
    first = rank_authorized_context(
        records, "unmatched concept", text_of=lambda row: row["text"], limit=4
    )
    second = rank_authorized_context(
        records, "unmatched concept", text_of=lambda row: row["text"], limit=4
    )
    assert first == second == records[:4]
