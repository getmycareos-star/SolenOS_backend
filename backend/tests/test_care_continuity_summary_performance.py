from sqlalchemy import Column, MetaData, String, Table, create_engine, event, insert
from sqlalchemy.orm import Session

from app.api import care_continuity


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
