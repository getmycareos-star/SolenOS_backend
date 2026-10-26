import asyncio
import json

import pytest

from app.services import qwen_background_worker as worker


def test_background_classifier_accepts_only_enabled_workflows(monkeypatch):
    monkeypatch.setattr(
        worker,
        "qwen_chat",
        lambda *args, **kwargs: json.dumps({"workflow": "daily_capture"}),
    )
    assert asyncio.run(worker._classify_task("I want to record today's observation", "")) == "daily_capture"


def test_background_classifier_rejects_community(monkeypatch):
    monkeypatch.setattr(
        worker,
        "qwen_chat",
        lambda *args, **kwargs: json.dumps({"workflow": "community"}),
    )
    with pytest.raises(ValueError, match="workflow_classification_invalid"):
        asyncio.run(worker._classify_task("Show me caregiver stories", ""))


def test_background_classifier_rejects_malformed_output(monkeypatch):
    monkeypatch.setattr(worker, "qwen_chat", lambda *args, **kwargs: "I think this is daily_capture")
    with pytest.raises(ValueError, match="workflow_classification_invalid"):
        asyncio.run(worker._classify_task("Help with care notes", ""))


def test_enabled_workflow_list_has_30_unique_entries_and_no_community():
    assert len(worker.ENABLED_WORKFLOWS) == 30
    assert len(set(worker.ENABLED_WORKFLOWS)) == 30
    assert "community" not in worker.ENABLED_WORKFLOWS
