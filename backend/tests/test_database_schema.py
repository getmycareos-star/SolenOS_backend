from sqlalchemy import create_engine, text

from app.core.database import Base, database_schema_differences


def test_schema_verifier_accepts_all_registered_tables_and_columns():
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(bind=engine)
        assert database_schema_differences(engine) == []
    finally:
        engine.dispose()


def test_schema_verifier_reports_missing_columns_without_reading_records():
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
