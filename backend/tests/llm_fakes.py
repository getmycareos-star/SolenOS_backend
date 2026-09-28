"""Fakes for the local reasoning layer.

Tests must not reach a real model. A call to a 1.5b model takes minutes on a
modest machine, and a test suite that depends on which model happens to be pulled
is a suite that will hang on someone else's laptop.

The fake returns whatever JSON the test queued, and records what it was asked,
so a test can assert on what the guard did rather than on what a model happened
to produce.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.llm_enums import LLMStatus
from app.services.llm.engine import LocalReasoningEngine


class FakeOllamaClient:
    def __init__(self, responses: Optional[List[Any]] = None) -> None:
        self.responses = list(responses or [])
        self.calls: List[Dict[str, Any]] = []
        self.available = True
        self.detail: Optional[str] = None

    def queue(self, *responses: Any) -> "FakeOllamaClient":
        self.responses.extend(responses)
        return self

    def is_available(self):
        return (True, None) if self.available else (False, self.detail or "unavailable")

    def list_models(self):
        return ["fake-model"]

    def generate_json(self, system_prompt: str, user_payload: Dict[str, Any]):
        self.calls.append({"system": system_prompt, "payload": user_payload})
        if not self.responses:
            return False, None, "no queued response", {"status": LLMStatus.UNAVAILABLE.value, "latency_ms": 1}
        nxt = self.responses.pop(0)
        if isinstance(nxt, str):
            return False, None, nxt, {"status": LLMStatus.UNAVAILABLE.value, "latency_ms": 1}
        return True, nxt, str(nxt), {"status": LLMStatus.OK.value, "latency_ms": 1}


def fake_engine(responses: Optional[List[Any]] = None) -> tuple:
    """An engine wired to a fake client. Returns (engine, client)."""
    client = FakeOllamaClient(responses)
    engine = LocalReasoningEngine(db=None, client=client)
    engine.enabled = True
    return engine, client


def guarded_response(
    summary: str = "A reading of the record.",
    evidence_ids: Optional[List[str]] = None,
    confidence: Optional[float] = None,
    possible_context: Optional[str] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """A well-formed model response that should survive the guard cleanly."""
    payload: Dict[str, Any] = {
        "summary": summary,
        "evidence_ids": evidence_ids or [],
        "confidence": confidence,
        "possible_context": possible_context
        or "Whether anyone has assessed this has not been recorded.",
    }
    payload.update(extra)
    return payload
