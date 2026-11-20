from sqlalchemy import Column, MetaData, String, Table, create_engine, event, insert
from sqlalchemy.orm import Session
from types import SimpleNamespace

from app.api import care_continuity




def test_change_source_refs_resolve_events_assertions_and_observations():
    change = SimpleNamespace(evidence_ids=["event-1", "assertion-1", "observation-1", "evidence-1", "missing-1"])
    events = {
        "event-1": SimpleNamespace(
            id="event-1", title="Daily care update", event_type="daily_care",
            occurred_at="2026-11-11T21:10:00Z", recorded_at="2026-11-11T22:13:16Z",
            created_at=None, evidence_ids=["source-evidence-1"],
        )
    }
    assertions = {
        "assertion-1": SimpleNamespace(
            id="assertion-1", field="mood", observed_at="2026-11-11T21:10:00Z",
            recorded_at="2026-11-11T22:13:16Z", evidence_ids=["source-evidence-2"],
        )
    }
    observations = {
        "observation-1": SimpleNamespace(
            id="observation-1", attribute="weight", value_text="possible weight loss",
            occurred_at=None, observed_at="2026-11-11T21:10:00Z",
            recorded_at="2026-11-11T22:13:16Z", evidence_id="source-evidence-3",
        )
    }

    evidence = {
        "evidence-1": SimpleNamespace(
            id="evidence-1", title=None, type="note",
            occurred_at="2026-11-11T21:10:00Z", ingested_at="2026-11-11T22:13:16Z",
            uploaded_at=None,
        )
    }
    refs = care_continuity._change_source_refs(
        change,
        event_by_id=events,
        assertion_by_id=assertions,
        observation_by_id=observations,
        evidence_by_id=evidence,
    )

    assert [item["record_id"] for item in refs] == ["event-1", "assertion-1", "observation-1", "evidence-1"]
    assert [item["source"] for item in refs] == ["events", "care_assertions", "evidence_observations", "evidence"]
    assert refs[0]["evidence_ids"] == ["source-evidence-1"]
    assert refs[1]["evidence_ids"] == ["source-evidence-2"]
    assert refs[2]["evidence_ids"] == ["source-evidence-3"]


def test_continuity_summary_counts_are_aggregated_and_person_scoped(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    table_names = set(care_continuity.RESOURCES.values())
    table_names.update({
        "open_questions",
        "information_gaps",
        "changes",
        "situations",
        "care_events",
        "evidence",
        "temporal_contradictions",
        "uncertainty_assessments",
        "significance_assessments",
        "care_delta_runs",
    })
    tables = {
        name: Table(
            name,
            metadata,
            Column("id", String, primary_key=True),
            Column("person_id", String, nullable=False),
        )
        for name in sorted(table_names)
    }
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(tables["care_events"]), [
            {"id": "event-a1", "person_id": "person-a"},
            {"id": "event-a2", "person_id": "person-a"},
            {"id": "event-b1", "person_id": "person-b"},
        ])
        connection.execute(insert(tables["evidence"]), [
            {"id": "evidence-a1", "person_id": "person-a"},
            {"id": "evidence-b1", "person_id": "person-b"},
        ])

    monkeypatch.setattr(
        care_continuity,
        "_scoped_identity",
        lambda request, db: ("person-a", "caregiver-a"),
    )
    statements = []

    def capture_sql(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture_sql)
    try:
        with Session(engine) as db:
            result = care_continuity.continuity_summary(request=None, db=db)
    finally:
        event.remove(engine, "before_cursor_execute", capture_sql)
        engine.dispose()

    assert result["person_id"] == "person-a"
    assert result["resources"]["events"] == 2
    assert result["resources"]["evidence"] == 1
    assert result["resources"]["open_questions"] == 0
    assert result["resources"]["facts"] == 0
    # The inventory count is one SQL aggregate rather than one SELECT per table.
    count_statements = [statement for statement in statements if "count(" in statement.lower()]
    assert len(count_statements) == 1
