"""Content-free SQL timing and connection-pool metrics for one HTTP request.

SQL statements, bind parameters, result values and care-record identifiers are
never retained or logged. Only operation classes, durations and pool counters
are aggregated in the current request context.
"""
from __future__ import annotations

from contextvars import ContextVar
from time import perf_counter
from threading import Lock
from typing import Any

from sqlalchemy import event

_request_metrics: ContextVar[dict[str, Any] | None] = ContextVar(
    "solenos_db_request_metrics", default=None
)
_pool_lock = Lock()


def begin_request_metrics() -> tuple[Any, dict[str, Any]]:
    metrics: dict[str, Any] = {
        "query_count": 0,
        "query_total_ms": 0.0,
        "query_max_ms": 0.0,
        "query_failed": 0,
        "pool_checkouts": 0,
        "pool_checkins": 0,
        "pool_peak_checked_out": 0,
        "operations": {},
    }
    return _request_metrics.set(metrics), metrics


def end_request_metrics(token: Any) -> None:
    _request_metrics.reset(token)


def _operation(statement: str) -> str:
    first = (statement or "").lstrip().split(None, 1)
    verb = first[0].upper().rstrip(";") if first else ""
    if verb in {"SELECT", "INSERT", "UPDATE", "DELETE", "WITH"}:
        return verb
    return "OTHER"


def _record(context: Any, elapsed_ms: float, failed: bool = False) -> None:
    metrics = _request_metrics.get()
    if metrics is None:
        return
    operation = getattr(context, "_solenos_operation", "OTHER")
    metrics["query_count"] += 1
    metrics["query_total_ms"] += elapsed_ms
    metrics["query_max_ms"] = max(metrics["query_max_ms"], elapsed_ms)
    metrics["query_failed"] += int(failed)
    operations = metrics["operations"]
    operations[operation] = operations.get(operation, 0) + 1


def install_sqlalchemy_observability(target_engine: Any) -> None:
    """Attach timing hooks once per engine without retaining SQL or parameters."""
    if getattr(target_engine, "_solenos_db_timing_installed", False):
        return
    setattr(target_engine, "_solenos_db_timing_installed", True)
    setattr(target_engine, "_solenos_pool_checked_out_count", 0)

    @event.listens_for(target_engine, "before_cursor_execute")
    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        context._solenos_started_at = perf_counter()
        context._solenos_operation = _operation(statement)

    @event.listens_for(target_engine, "after_cursor_execute")
    def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        started = getattr(context, "_solenos_started_at", None)
        if started is not None:
            _record(context, (perf_counter() - started) * 1000)

    @event.listens_for(target_engine, "handle_error")
    def _handle_error(exception_context):
        context = exception_context.execution_context
        started = getattr(context, "_solenos_started_at", None) if context else None
        if started is not None:
            _record(context, (perf_counter() - started) * 1000, failed=True)

    @event.listens_for(target_engine, "checkout")
    def _pool_checkout(dbapi_connection, connection_record, connection_proxy):
        metrics = _request_metrics.get()
        with _pool_lock:
            checked_out = int(getattr(target_engine, "_solenos_pool_checked_out_count", 0)) + 1
            setattr(target_engine, "_solenos_pool_checked_out_count", checked_out)
        if metrics is not None:
            metrics["pool_checkouts"] += 1
            metrics["pool_peak_checked_out"] = max(
                metrics["pool_peak_checked_out"], checked_out
            )

    @event.listens_for(target_engine, "checkin")
    def _pool_checkin(dbapi_connection, connection_record):
        metrics = _request_metrics.get()
        with _pool_lock:
            checked_out = max(
                0, int(getattr(target_engine, "_solenos_pool_checked_out_count", 0)) - 1
            )
            setattr(target_engine, "_solenos_pool_checked_out_count", checked_out)
        if metrics is not None:
            metrics["pool_checkins"] += 1


def pool_snapshot(target_engine: Any) -> dict[str, int | None]:
    """Return pool occupancy/capacity counters, never connection details."""
    pool = target_engine.pool

    def read(name: str) -> int | None:
        value = getattr(pool, name, None)
        try:
            return int(value() if callable(value) else value)
        except (TypeError, ValueError, NotImplementedError):
            return None

    return {
        "checked_out": read("checkedout") if read("checkedout") is not None else int(getattr(target_engine, "_solenos_pool_checked_out_count", 0)),
        "pool_size": read("size"),
        "overflow": read("overflow"),
    }
