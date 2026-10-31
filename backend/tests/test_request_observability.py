import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from app.request_metrics import RequestMetricsMiddleware
from app.main import _index_event_ids_by_evidence


def test_request_metrics_logs_route_template_and_never_query_or_care_content(caplog):
    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 503, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"detail":"private care text"}'})

    middleware = RequestMetricsMiddleware(downstream)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/evidence/person-secret-123",
        "query_string": b"person_id=person-secret-123&private_note=do-not-log",
        "headers": [(b"authorization", b"Bearer secret-token")],
        "route": SimpleNamespace(path="/api/v1/evidence/{person_id}"),
    }
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    with caplog.at_level(logging.INFO, logger="solenos.http"):
        asyncio.run(middleware(scope, receive, send))

    start = next(item for item in sent if item["type"] == "http.response.start")
    headers = dict(start["headers"])
    assert headers[b"x-request-id"]
    record = next(item for item in caplog.records if item.getMessage().startswith("solenos_http_request "))
    payload = json.loads(record.getMessage().split(" ", 1)[1])
    assert payload["route"] == "/api/v1/evidence/{person_id}"
    assert payload["status_code"] == 503
    assert payload["outcome"] == "server_error"
    rendered = record.getMessage()
    for forbidden in ("person-secret-123", "private_note", "do-not-log", "secret-token", "private care text"):
        assert forbidden not in rendered


def test_request_metrics_marks_unhandled_exception_without_logging_exception_text(caplog):
    async def downstream(scope, receive, send):
        raise RuntimeError("private request payload must not be logged")

    middleware = RequestMetricsMiddleware(downstream)
    scope = {"type": "http", "method": "POST", "path": "/api/private", "headers": []}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    with caplog.at_level(logging.WARNING, logger="solenos.http"):
        with pytest.raises(RuntimeError):
            asyncio.run(middleware(scope, receive, send))

    record = next(item for item in caplog.records if item.getMessage().startswith("solenos_http_request "))
    payload = json.loads(record.getMessage().split(" ", 1)[1])
    assert payload["status_code"] == 500
    assert payload["error_category"] == "RuntimeError"
    assert "private request payload" not in record.getMessage()
    assert payload["route"] == "unmatched"


def test_event_evidence_projection_index_preserves_links_in_linear_pass():
    events = [
        SimpleNamespace(id="event-a", evidence_ids=["evidence-1", "evidence-2"]),
        SimpleNamespace(id="event-b", evidence_ids=["evidence-2"]),
        SimpleNamespace(id="event-c", evidence_ids=None),
    ]
    indexed = _index_event_ids_by_evidence(events)
    assert indexed == {
        "evidence-1": ["event-a"],
        "evidence-2": ["event-a", "event-b"],
    }
