"""Contract checks for the enabled SolenOS Qwen workflows.

These checks prove accepted workflow IDs and Community exclusion only. They do
not claim live inference or durable background-job execution; those require
separate integration tests.
"""
from typing import get_args

import pytest
from pydantic import ValidationError

from app.api.collaboration import QwenAssistRequest
from app.services.qwen_background_worker import ENABLED_WORKFLOWS as WORKER_ENABLED_WORKFLOWS
from app.services.qwen_workflows import ENABLED_WORKFLOWS




def test_api_and_background_worker_share_the_same_39_workflow_contract():
    api_workflows = get_args(QwenAssistRequest.model_fields["workflow"].annotation)
    assert len(ENABLED_WORKFLOWS) == 39
    assert len(set(ENABLED_WORKFLOWS)) == 39
    assert set(api_workflows) == set(ENABLED_WORKFLOWS)
    assert set(WORKER_ENABLED_WORKFLOWS) == set(ENABLED_WORKFLOWS)
    assert "community" not in ENABLED_WORKFLOWS


@pytest.mark.parametrize("workflow", ENABLED_WORKFLOWS)
def test_all_39_enabled_solenos_workflows_are_accepted(workflow):
    request = QwenAssistRequest(workflow=workflow, text="Help organize the supplied information.")
    assert request.workflow == workflow


def test_community_workflow_is_rejected_while_community_is_disabled():
    with pytest.raises(ValidationError):
        QwenAssistRequest(workflow="community", text="Summarize peer experiences.")


def test_unknown_workflow_is_rejected():
    with pytest.raises(ValidationError):
        QwenAssistRequest(workflow="not_a_solenos_workflow", text="Help me.")
