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
