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
        status_code: int | None = None
        error_category: str | None = None
        client_disconnected = False

        async def send_with_request_id(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500))
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode("ascii")))
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
            }
            message = "solenos_http_request " + json.dumps(event, separators=(",", ":"), sort_keys=True)
            if status_code >= 500 or client_disconnected:
                logger.warning(message)
            else:
                logger.info(message)
