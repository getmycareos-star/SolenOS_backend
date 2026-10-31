from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.core.db_observability import (
    begin_request_metrics,
    end_request_metrics,
    install_sqlalchemy_observability,
    pool_snapshot,
)


def test_query_metrics_capture_duration_and_pool_occupancy_without_sql_values():
    engine = create_engine("sqlite:///:memory:")
    install_sqlalchemy_observability(engine)
    token, metrics = begin_request_metrics()
    private_value = "care-record-private-value-must-not-be-retained"
    try:
        with engine.connect() as connection:
            connection.execute(
                text("SELECT :private_value"),
                {"private_value": private_value},
            ).scalar()
        assert metrics["query_count"] == 1
        assert metrics["query_total_ms"] >= 0
        assert metrics["query_max_ms"] >= 0
        assert metrics["query_failed"] == 0
        assert metrics["operations"] == {"SELECT": 1}
        assert metrics["pool_checkouts"] == 1
        assert metrics["pool_checkins"] == 1
        assert metrics["pool_peak_checked_out"] >= 1
        assert private_value not in repr(metrics)
        assert set(pool_snapshot(engine)) == {"checked_out", "pool_size", "overflow"}
    finally:
        end_request_metrics(token)
        engine.dispose()


def test_failed_query_is_timed_without_retaining_statement_text():
    engine = create_engine("sqlite:///:memory:")
    install_sqlalchemy_observability(engine)
    token, metrics = begin_request_metrics()
    try:
        with engine.connect() as connection:
            try:
                connection.execute(text("SELECT * FROM table_that_does_not_exist"))
            except OperationalError:
                pass
            else:
                raise AssertionError("Expected the database to reject the missing table")
        assert metrics["query_count"] == 1
        assert metrics["query_failed"] == 1
        assert metrics["operations"] == {"SELECT": 1}
        assert "table_that_does_not_exist" not in repr(metrics)
    finally:
        end_request_metrics(token)
        engine.dispose()


def test_request_database_metrics_are_isolated_between_requests():
    engine = create_engine("sqlite:///:memory:")
    install_sqlalchemy_observability(engine)
    first_token, first = begin_request_metrics()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar()
    finally:
        end_request_metrics(first_token)

    second_token, second = begin_request_metrics()
    try:
        assert second["query_count"] == 0
        with engine.connect() as connection:
            connection.execute(text("SELECT 2")).scalar()
        assert second["query_count"] == 1
        assert first["query_count"] == 1
    finally:
        end_request_metrics(second_token)
        engine.dispose()
