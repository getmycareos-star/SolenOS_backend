"""Configuration safety tests for production startup defaults."""
import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_production_refuses_the_placeholder_signing_key():
    with pytest.raises(ValidationError, match="SECRET_KEY must be configured"):
        Settings(debug=False, secret_key="change-me-in-production")


def test_production_refuses_a_short_signing_key():
    with pytest.raises(ValidationError, match="SECRET_KEY must be configured"):
        Settings(debug=False, secret_key="short")


def test_production_accepts_a_configured_strong_signing_key():
    settings = Settings(debug=False, secret_key="sufficiently-long-test-secret-key-123456")
    assert settings.debug is False
    assert settings.secret_key == "sufficiently-long-test-secret-key-123456"


def test_development_can_use_the_documented_local_default():
    settings = Settings(debug=True, secret_key="change-me-in-production")
    assert settings.debug is True
