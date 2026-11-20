from sqlalchemy import create_engine, text

from app.core.database import Base, database_schema_differences


def test_schema_verifier_accepts_registered_tables_and_columns():
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(bind=engine)
        assert database_schema_differences(engine) == []
    finally:
        engine.dispose()


def test_schema_verifier_reports_missing_columns():
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE caregivers (id VARCHAR PRIMARY KEY)'))
        differences = database_schema_differences(engine)
        caregiver_difference = next(
            (item for item in differences if item.startswith("caregivers: missing columns")),
            None,
        )
        assert caregiver_difference is not None
        assert "name" in caregiver_difference or "person_id" in caregiver_difference
        assert any(item.startswith("caregivers: missing foreign keys") for item in differences)
        assert any(item.startswith("caregivers: missing unique constraints") for item in differences)
        assert any(item.startswith("caregivers: missing indexes") for item in differences)
    finally:
        engine.dispose()


def test_schema_verifier_reports_data_type_and_primary_key_drift():
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE persons (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
            ))
        differences = database_schema_differences(engine)
        assert any(
            item.startswith("persons: data types differ") and "id" in item
            for item in differences
        )
    finally:
        engine.dispose()


def test_schema_verifier_reports_missing_indexes():
    from sqlalchemy import inspect

    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(bind=engine)
        indexes = inspect(engine).get_indexes("evidence")
        person_index = next(
            item["name"] for item in indexes
            if item.get("column_names") == ["person_id"] and not item.get("unique", False)
        )
        with engine.begin() as connection:
            connection.execute(text(f'DROP INDEX "{person_index}"'))

        differences = database_schema_differences(engine)
        assert any(
            item.startswith("evidence: missing indexes") and "person_id" in item
            for item in differences
        )
    finally:
        engine.dispose()


def test_schema_verifier_reports_missing_tables():
    engine = create_engine("sqlite:///:memory:")
    try:
        differences = database_schema_differences(engine)
        assert differences
        assert any("(table missing)" in item for item in differences)
    finally:
        engine.dispose()


def test_committed_care_record_survives_engine_recreation(tmp_path):
    from sqlalchemy.orm import sessionmaker

    from app.models.care import Person

    database_file = tmp_path / "persistence-check.db"
    first_engine = create_engine(f"sqlite:///{database_file}")
    Base.metadata.create_all(bind=first_engine)
    FirstSession = sessionmaker(bind=first_engine)
    first_session = FirstSession()
    person = Person(name="Persistence verification record")
    first_session.add(person)
    first_session.commit()
    person_id = person.id
    first_session.close()
    first_engine.dispose()

    # A new engine/session simulates the storage being reopened after a process
    # restart; the committed record must still be present.
    second_engine = create_engine(f"sqlite:///{database_file}")
    SecondSession = sessionmaker(bind=second_engine)
    second_session = SecondSession()
    try:
        restored = second_session.query(Person).filter(Person.id == person_id).one()
        assert restored.name == "Persistence verification record"
    finally:
        second_session.close()
        second_engine.dispose()


def test_schema_type_contract_accepts_matching_postgres_native_enum_labels():
    from enum import Enum as PythonEnum
    from sqlalchemy import Enum as SQLAlchemyEnum
    from sqlalchemy.dialects.postgresql import ENUM as PostgreSQLEnum

    from app.core.database import _schema_column_types_compatible

    class ReviewState(str, PythonEnum):
        PENDING = "pending"
        ACCEPTED = "accepted"

    expected = SQLAlchemyEnum(ReviewState, name="review_state")
    matching_native = PostgreSQLEnum("PENDING", "ACCEPTED", name="review_state")
    mismatched_native = PostgreSQLEnum("PENDING", "REJECTED", name="review_state")

    assert _schema_column_types_compatible(expected, matching_native, "postgresql")
    assert not _schema_column_types_compatible(expected, mismatched_native, "postgresql")
    assert _schema_column_types_compatible(expected, String(32), "postgresql")


def test_schema_type_contract_only_enforces_explicit_numeric_precision():
    from sqlalchemy import Float, Numeric

    from app.core.database import _schema_column_types_compatible

    assert _schema_column_types_compatible(Float(), Numeric(8, 4), "postgresql")
    assert _schema_column_types_compatible(Numeric(8, 4), Numeric(8, 4), "postgresql")
    assert not _schema_column_types_compatible(Numeric(8, 4), Numeric(10, 4), "postgresql")
