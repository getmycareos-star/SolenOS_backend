"""Regression tests for the document validity and renewal workflow."""

from pydantic import ValidationError

from app.api.collaboration import QwenAssistRequest
from app.services.qwen_background_worker import ENABLED_WORKFLOWS, _route_from_text


def test_document_validity_renewal_is_enabled():
    assert "document_validity_renewal" in ENABLED_WORKFLOWS


def test_document_validity_renewal_prompts_route_correctly():
    prompts = [
        "Review document validity and renewal.",
        "What is the stated document expiration date?",
        "Check the renewal date written in this document.",
        "Review expiring care documents and their source dates.",
        "When does this document expire according to its text?",
    ]
    for prompt in prompts:
        assert _route_from_text(prompt) == "document_validity_renewal", prompt


def test_document_validity_renewal_contract_and_community_exclusion():
    payload = QwenAssistRequest(
        workflow="document_validity_renewal",
        text="Review the stated expiry date and renewal instructions.",
    )
    assert payload.workflow == "document_validity_renewal"

    try:
        QwenAssistRequest(workflow="community", text="Find peer stories.")
    except ValidationError:
        pass
    else:
        raise AssertionError("Community must remain excluded from Qwen workflow contracts")
