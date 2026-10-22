import pytest
from app.services.auth_service import hash_password, verify_password

def test_password_hash_is_salted_and_verifies():
    password = "correct-horse-battery-staple-2026"
    first = hash_password(password)
    second = hash_password(password)
    assert first != second
    assert verify_password(password, first)
    assert verify_password(password, second)
    assert not verify_password("wrong-password-value", first)

def test_password_minimum_length_is_enforced():
    with pytest.raises(ValueError, match="at least 12"):
        hash_password("short")

def test_malformed_hash_fails_closed():
    assert not verify_password("anything-long-enough", "not-a-valid-hash")
