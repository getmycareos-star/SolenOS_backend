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


def test_schema_verifier_rejects_composite_index_with_reversed_column_order():
    """A reversed composite B-tree index does not satisfy the ORM query contract."""
    from sqlalchemy import inspect

    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(bind=engine)
        candidate = None
        for table in sorted(Base.metadata.tables.values(), key=lambda item: item.name):
            for index in sorted(table.indexes, key=lambda item: item.name or ""):
                expressions = list(index.expressions)
                columns = [expression.name for expression in expressions if hasattr(expression, "name")]
                predicate = index.dialect_options["sqlite"].get("where")
                if (
                    len(columns) > 1
                    and len(columns) == len(expressions)
                    and not index.unique
                    and predicate is None
                ):
                    reflected = next(
                        (
                            item for item in inspect(engine).get_indexes(table.name)
                            if item.get("name") == index.name
                            and item.get("column_names") == columns
                        ),
                        None,
                    )
                    if reflected is not None:
                        candidate = (table.name, index.name, columns)
                        break
            if candidate:
                break

        assert candidate is not None, "test schema must contain a simple composite index"
        table_name, index_name, columns = candidate
        preparer = engine.dialect.identifier_preparer
        quoted_index = preparer.quote(index_name)
        quoted_table = preparer.quote(table_name)
        reversed_columns = ", ".join(preparer.quote(name) for name in reversed(columns))
        with engine.begin() as connection:
            connection.execute(text(f"DROP INDEX {quoted_index}"))
            connection.execute(text(
                f"CREATE INDEX {quoted_index} ON {quoted_table} ({reversed_columns})"
            ))

        differences = database_schema_differences(engine)
        mismatch = next(
            (
                item for item in differences
                if item.startswith(f"{table_name}: missing indexes or index predicates")
            ),
            None,
        )
        assert mismatch is not None
        assert all(column in mismatch for column in columns)
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


def test_schema_verifier_reports_foreign_key_action_drift():
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE persons (id VARCHAR PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE changes (id VARCHAR PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE caregivers (id VARCHAR PRIMARY KEY)"))
            # The ORM requires CASCADE for change_id. A different action is a
            # real persistence-contract mismatch even though the FK target matches.
            connection.execute(text("""
                CREATE TABLE change_confirmation_events (
                    id VARCHAR PRIMARY KEY,
                    change_id VARCHAR NOT NULL REFERENCES changes(id) ON DELETE SET NULL,
                    person_id VARCHAR NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
                    actor_caregiver_id VARCHAR NOT NULL REFERENCES caregivers(id)
                )
            """))
        differences = database_schema_differences(engine)
        mismatch = next(
            (
                item for item in differences
                if item.startswith("change_confirmation_events: missing foreign keys or foreign-key actions")
            ),
            None,
        )
        assert mismatch is not None
        assert "change_id" in mismatch
        assert "ondelete=CASCADE" in mismatch
    finally:
        engine.dispose()


def test_schema_verifier_reports_missing_partial_index_predicate():
    from sqlalchemy import inspect

    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(bind=engine)
        index_name = "uq_patterns_one_active_per_subject"
        assert index_name in {
            item["name"] for item in inspect(engine).get_indexes("patterns")
        }
        with engine.begin() as connection:
            connection.execute(text(f'DROP INDEX "{index_name}"'))
            # Same columns and uniqueness, but without the required active-row
            # predicate. This changes the model's uniqueness semantics.
            connection.execute(text(
                f"CREATE UNIQUE INDEX \"{index_name}\" "
                "ON patterns (person_id, subject_type, subject_id, pattern_type)"
            ))
        differences = database_schema_differences(engine)
        mismatch = next(
            (
                item for item in differences
                if item.startswith("patterns: missing indexes or index predicates")
            ),
            None,
        )
        assert mismatch is not None
        assert "is_active is true" in mismatch
    finally:
        engine.dispose()


def test_schema_verifier_preserves_composite_index_column_order():
    from sqlalchemy import inspect

    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(bind=engine)
        index_name = "uq_patterns_one_active_per_subject"
        with engine.begin() as connection:
            connection.execute(text(f'DROP INDEX "{index_name}"'))
            connection.execute(text(
                f"CREATE UNIQUE INDEX \"{index_name}\" "
                "ON patterns (pattern_type, subject_id, subject_type, person_id) "
                "WHERE is_active IS TRUE"
            ))
        actual = next(
            item for item in inspect(engine).get_indexes("patterns")
            if item["name"] == index_name
        )
        assert actual["column_names"] == [
            "pattern_type", "subject_id", "subject_type", "person_id"
        ]
        differences = database_schema_differences(engine)
        assert any(
            item.startswith("patterns: missing indexes or index predicates")
            for item in differences
        )
    finally:
        engine.dispose()


def test_schema_verifier_does_not_accept_partial_unique_index_as_full_constraint():
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE persons (id VARCHAR PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE caregivers (id VARCHAR PRIMARY KEY)"))
            connection.execute(text("""
                CREATE TABLE caregiver_accounts (
                    id VARCHAR PRIMARY KEY,
                    email_normalized VARCHAR(320) NOT NULL,
                    password_hash VARCHAR(256) NOT NULL,
                    person_id VARCHAR NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
                    caregiver_id VARCHAR NOT NULL REFERENCES caregivers(id) ON DELETE CASCADE,
                    created_at DATETIME NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE UNIQUE INDEX uq_caregiver_accounts_email_normalized
                ON caregiver_accounts(email_normalized)
                WHERE id IS NOT NULL
            """))
        differences = database_schema_differences(engine)
        assert any(
            item.startswith("caregiver_accounts: missing unique constraints")
            and "email_normalized" in item
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


def test_schema_type_contract_accepts_json_and_wider_string_storage():
    from sqlalchemy import String, Text
    from sqlalchemy.dialects.postgresql import JSON as PostgreSQLJSON, JSONB

    from app.core.database import _schema_column_types_compatible
    from app.core.types import JSONList
    from app.models.privacy_request import DataSubjectRequest

    assert DataSubjectRequest.__table__.c.subject_id.nullable is True
    assert _schema_column_types_compatible(JSONList(), PostgreSQLJSON(), "postgresql")
    assert _schema_column_types_compatible(JSONList(), JSONB(), "postgresql")
    assert _schema_column_types_compatible(String(64), Text(), "postgresql")
    assert _schema_column_types_compatible(String(64), String(128), "postgresql")
    assert not _schema_column_types_compatible(String(128), String(64), "postgresql")


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
    from sqlalchemy import Enum as SQLAlchemyEnum, String
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


def test_notification_delivery_attempt_references_uuid_notification_id_width():
    from app.models.notifications import CareNotification, NotificationDeliveryAttempt

    notification_id = NotificationDeliveryAttempt.__table__.c.notification_id
    assert notification_id.type.length == 36
    assert next(iter(notification_id.foreign_keys)).target_fullname == "care_notifications.id"
    assert CareNotification.__table__.c.id.primary_key is True
