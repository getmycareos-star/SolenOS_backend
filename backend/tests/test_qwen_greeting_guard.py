"""Regression checks for the Qwen Companion greeting quality gate.

These tests exercise deterministic response validation only; they do not claim
that a live Qwen runtime or Railway production inference has been verified.
"""
import pytest

from app.api.collaboration import _greeting_reply_needs_revision


@pytest.mark.parametrize("reply", ["Hello!", "Hi there!", "Good morning!", "Hello, good to hear from you."])
def test_natural_short_greetings_pass(reply):
    assert _greeting_reply_needs_revision(reply) is False


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "Hello! How are you doing?",
        "Hi there! How can I help you today?",
        "Hello? What do you need?",
        "This is a greeting that is far too long and keeps going until it is no longer a short acknowledgement.",
    ],
)
def test_empty_long_or_interview_style_greetings_fail(reply):
    assert _greeting_reply_needs_revision(reply) is True
