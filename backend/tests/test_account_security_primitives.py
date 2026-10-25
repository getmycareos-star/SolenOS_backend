import os
import time

import pytest

from app.api.account_security import _seal, _unseal, _totp, _valid_totp


def test_mfa_secret_uses_authenticated_encryption(monkeypatch):
    monkeypatch.setenv("ACCOUNT_SECURITY_KEY", "test-key-that-is-at-least-32-bytes-long")
    secret = b"0123456789abcdefghij"
    encrypted = _seal(secret)
    assert encrypted != secret.decode(errors="ignore")
    assert _unseal(encrypted) == secret


def test_mfa_secret_rejects_tampering(monkeypatch):
    monkeypatch.setenv("ACCOUNT_SECURITY_KEY", "test-key-that-is-at-least-32-bytes-long")
    encrypted = _seal(b"authenticator secret")
    damaged = encrypted[:-2] + ("AA" if encrypted[-2:] != "AA" else "BB")
    with pytest.raises(ValueError):
        _unseal(damaged)


def test_totp_accepts_current_code_and_rejects_malformed_code():
    secret = b"0123456789abcdefghij"
    code = _totp(secret, time.time())
    assert _valid_totp(secret, code)
    assert not _valid_totp(secret, "12ab56")
    assert not _valid_totp(secret, "123")
