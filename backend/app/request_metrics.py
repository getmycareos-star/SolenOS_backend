"""Privacy-preserving HTTP request telemetry for production operations.

Only route templates and transport metadata are logged. Request/response bodies,
query strings, headers, care identifiers and exception messages are never logged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Awaitable, Callable

from app.core.database import engine
from app.core.db_observability import begin_request_metrics, end_request_metrics, pool_snapshot

logger = logging.getLogger("solenos.http")


class RequestMetricsMiddleware:
    """Emit one structured, privacy-safe record for each HTTP request."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        started = time.perf_counter()
        db_token, db_metrics = begin_request_metrics()
        status_code: int | None = None
        error_category: str | None = None
        client_disconnected = False

        async def send_with_request_id(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500))
                original_headers = list(message.get("headers", []))
                existing_server_timing = next(
                    (value for key, value in original_headers if key.lower() == b"server-timing"),
                    None,
                )
                headers = [
                    (key, value)
                    for key, value in original_headers
                    if key.lower() not in {b"x-request-id", b"server-timing"}
                ]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                app_timing = f"app;dur={(time.perf_counter() - started) * 1000:.2f}".encode("ascii")
                headers.append((
                    b"server-timing",
                    existing_server_timing + b", " + app_timing if existing_server_timing else app_timing,
                ))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except asyncio.CancelledError:
            client_disconnected = True
            error_category = "request_cancelled"
            if status_code is None:
                status_code = 499
            raise
        except OSError as exc:
            client_disconnected = True
            error_category = type(exc).__name__
            if status_code is None:
                status_code = 499
            raise
        except Exception as exc:
            error_category = type(exc).__name__
            if status_code is None:
                status_code = 500
            raise
        finally:
            route = scope.get("route")
            route_template = getattr(route, "path", None) or "unmatched"
            end_request_metrics(db_token)
            pool = pool_snapshot(engine)
            operations = db_metrics.get("operations", {})
            if status_code is None:
                status_code = 500
            if client_disconnected:
                outcome = "client_disconnect"
            elif status_code >= 500:
                outcome = "server_error"
            elif status_code >= 400:
                outcome = "client_error"
            else:
                outcome = "success"

            event = {
                "event": "http_request",
                "request_id": request_id,
                "method": str(scope.get("method", "UNKNOWN")).upper(),
                "route": route_template,
                "status_code": status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "outcome": outcome,
                "error_category": error_category,
                "deployment_commit": os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,
                "db_query_count": db_metrics["query_count"],
                "db_query_total_ms": round(db_metrics["query_total_ms"], 3),
                "db_query_max_ms": round(db_metrics["query_max_ms"], 3),
                "db_query_failed": db_metrics["query_failed"],
                "db_select_count": operations.get("SELECT", 0) + operations.get("WITH", 0),
                "db_insert_count": operations.get("INSERT", 0),
                "db_update_count": operations.get("UPDATE", 0),
                "db_delete_count": operations.get("DELETE", 0),
                "db_other_count": operations.get("OTHER", 0),
                "db_pool_checkouts": db_metrics["pool_checkouts"],
                "db_pool_checkins": db_metrics["pool_checkins"],
                "db_pool_peak_checked_out": db_metrics["pool_peak_checked_out"],
                "db_pool_checked_out": pool["checked_out"],
                "db_pool_size": pool["pool_size"],
                "db_pool_overflow": pool["overflow"],
            }
            message = "solenos_http_request " + json.dumps(event, separators=(",", ":"), sort_keys=True)
            if status_code >= 500 or client_disconnected:
                logger.warning(message)
            else:
                logger.info(message)
