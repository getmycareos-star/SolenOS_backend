"""Password hashing migration and compatibility regressions."""
from app.services.authentication import hash_password, verify_password, hash_token, verify_token


# Fixed Passlib pbkdf2_sha256 fixture generated for password "legacy-password".
# This protects existing users' credentials during the Argon2id migration.
LEGACY_PBKDF2_HASH = (
    "$pbkdf2-sha256$29000$c29sZW5vcy1sZWdhY3ktc2FsdA$"
    "5lY4HPPhOs9hP5zET3sTvxU7y3PYOgphT6Cci9PhC1w"
)


def test_new_password_hashes_use_argon2id_and_verify():
    encoded = hash_password("new-password")
    assert encoded.startswith("$argon2id$")
    assert verify_password("new-password", encoded)
    assert not verify_password("wrong-password", encoded)


def test_legacy_password_hashes_remain_verifiable():
    assert verify_password("legacy-password", LEGACY_PBKDF2_HASH)
    assert not verify_password("wrong-password", LEGACY_PBKDF2_HASH)


def test_invalid_password_hash_is_rejected_without_raising():
    assert not verify_password("anything", "not-a-valid-password-hash")
    assert not verify_password("anything", "")


def test_new_session_token_hashes_use_argon2id():
    encoded = hash_token("session-token-value")
    assert encoded.startswith("$argon2id$")
    assert verify_token("session-token-value", encoded)
    assert not verify_token("wrong-token", encoded)


def test_legacy_session_token_hashes_remain_verifiable():
    assert verify_token("legacy-password", LEGACY_PBKDF2_HASH)
    assert not verify_token("wrong-token", LEGACY_PBKDF2_HASH)
